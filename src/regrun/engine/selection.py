"""Turning CLI options into the concrete set of files, groups and tests to run.

Everything between "the operator typed a target and some filters" and "here is
the ordered list of files with their surviving groups" lives here. The CLI
command stays a thin shell over ``build_run_plan``; selection semantics (which
files, in which order, with which groups) are testable without Click.

The canonical run order is defined once, by ``discover_yaml_files``: layer rank
(setup, api, mcp, chat) then filename. Every later consumer honours that order
rather than deriving its own.
"""

from dataclasses import dataclass
from pathlib import Path

import click
import structlog
import yaml

from regrun.config import settings
from regrun.models import Group, TestFile

logger = structlog.get_logger()

CONFIG_FILENAME = "regrun.yaml"

# Layer rank for the canonical run order. Unknown layers sort last.
LAYER_ORDER = {"setup": 0, "api": 1, "mcp": 2, "chat": 3}

__all__ = [
    "CONFIG_FILENAME",
    "LAYER_ORDER",
    "RunPlan",
    "build_run_plan",
    "discover_yaml_files",
    "filter_groups",
    "find_config",
    "parse_group_ids",
    "parse_yaml_file",
    "resolve_target",
]


@dataclass(frozen=True)
class RunPlan:
    """The files to run, in run order, with their surviving groups.

    ``paths`` and ``test_files`` are positionally aligned: ``paths[i]`` is the
    file ``test_files[i]`` was parsed from.
    """

    paths: list[Path]
    test_files: list[TestFile]


def find_config() -> tuple[dict, Path] | None:
    """Walk up from CWD looking for regrun.yaml.

    Returns (parsed_config, project_root) or None if not found.
    """
    current = Path.cwd().resolve()
    for _ in range(10):
        config_path = current / CONFIG_FILENAME
        if config_path.is_file():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            if isinstance(config, dict):
                return config, current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def resolve_target(target: str) -> Path:
    """Resolve a CLI target to a test directory path.

    If target is an existing directory, use it directly.
    Otherwise, look up as a product name in regrun.yaml.
    """
    target_path = Path(target)
    if target_path.is_dir():
        return target_path.resolve()

    # Not a directory — look up in regrun.yaml
    result = find_config()
    if result is None:
        raise click.ClickException(
            f"'{target}' is not a directory and no {CONFIG_FILENAME} found. "
            f"Pass a directory path or create {CONFIG_FILENAME} with a 'paths' key."
        )

    config, project_root = result
    paths = config.get("paths")
    if not paths or not isinstance(paths, dict):
        raise click.ClickException(f"{CONFIG_FILENAME} found but missing 'paths' mapping.")

    test_path_rel = paths.get(target)
    if not test_path_rel:
        known = ", ".join(paths.keys())
        raise click.ClickException(f"Unknown product '{target}'. Known: {known}")

    resolved = project_root / test_path_rel
    if not resolved.is_dir():
        raise click.ClickException(
            f"Path for '{target}' resolved to {resolved} but directory not found."
        )
    return resolved


def discover_yaml_files(
    test_dir: Path,
    layer: str | None,
    skip_setup: bool = False,
) -> list[Path]:
    """Discover YAML test files in a directory, optionally filtered by layer.

    Setup files are always included as a dependency unless skip_setup=True.
    Files are ordered: setup layer first, then alphabetically.
    """
    if not test_dir.is_dir():
        raise click.ClickException(f"Test directory not found: {test_dir}")

    yaml_files = sorted(test_dir.glob("*.yaml"))
    if not yaml_files:
        raise click.ClickException(f"No YAML test files found in {test_dir}")

    # Parse meta from each file to get the layer, then filter and sort
    file_layers: list[tuple[Path, str]] = []
    for f in yaml_files:
        try:
            with open(f) as fh:
                raw = yaml.safe_load(fh)
            file_layer = raw.get("meta", {}).get("layer", "unknown")
            file_layers.append((f, file_layer))
        except Exception as e:
            logger.warning("yaml_parse_skip", file=str(f), error=str(e))

    # Filter by layer -- setup auto-included as dependency unless skipped
    if layer and layer != "setup":
        if skip_setup:
            file_layers = [(f, fl) for f, fl in file_layers if fl == layer]
        else:
            file_layers = [(f, fl) for f, fl in file_layers if fl in (layer, "setup")]
    elif layer == "setup":
        file_layers = [(f, fl) for f, fl in file_layers if fl == "setup"]

    # Sort: setup first, then alphabetically
    file_layers.sort(key=lambda x: (LAYER_ORDER.get(x[1], 99), x[0].name))

    return [f for f, _ in file_layers]


def parse_yaml_file(path: Path) -> TestFile:
    """Parse a YAML file into a TestFile model."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return TestFile.model_validate(raw)


def parse_group_ids(group_str: str | None) -> list[int] | None:
    """Parse a ``--group`` value (``1,2,3``) into group ids, or None if absent."""
    if not group_str:
        return None
    try:
        return [int(g.strip()) for g in group_str.split(",")]
    except ValueError:
        raise click.ClickException(
            f"Invalid group IDs: '{group_str}'. Use comma-separated integers."
        )


def filter_groups(
    test_file: TestFile,
    group_ids: list[int] | None,
    priority: str | None,
    skip_cleanup: bool = False,
) -> TestFile:
    """Filter test file groups by group IDs and/or priority.

    Cleanup-flagged groups (``cleanup: true``) are exempt from filtering — they
    are always retained, mirroring the setup-always guarantee, so filtered
    iteration runs still sweep the environment. ``--skip-cleanup`` removes that
    exemption: cleanup groups are then subject to the normal filters (and dropped
    when they don't match), which is how a developer iterates without a sweep.
    """
    groups = test_file.groups

    def _matches(g: Group) -> bool:
        if group_ids and g.id not in group_ids:
            return False
        if priority and g.priority != priority:
            return False
        return True

    if group_ids or priority:
        groups = [g for g in groups if _matches(g) or (g.cleanup and not skip_cleanup)]

    return test_file.model_copy(update={"groups": groups})


def _parse_and_filter(
    yaml_files: list[Path],
    layer: str | None,
    group_ids: list[int] | None,
    priority: str | None,
    skip_cleanup: bool,
) -> RunPlan:
    """Parse each discovered file and drop the ones left with no groups."""
    paths: list[Path] = []
    test_files: list[TestFile] = []
    for path in yaml_files:
        try:
            tf = parse_yaml_file(path)
            # Setup runs in full when auto-included as a dependency -- group/priority
            # filters rarely match its groups, which would drop captured variables.
            # Only filter setup when it is the explicit target (--layer setup).
            if not (tf.meta.layer == "setup" and layer != "setup"):
                tf = filter_groups(tf, group_ids, priority, skip_cleanup)
            if tf.groups:
                paths.append(path)
                test_files.append(tf)
        except Exception as e:
            raise click.ClickException(f"Failed to parse {path.name}: {e}")
    return RunPlan(paths=paths, test_files=test_files)


def _apply_endpoint_overrides(test_files: list[TestFile]) -> None:
    """Apply endpoint overrides from env vars (CI uses service aliases, not *.localhost)."""
    if settings.api_endpoint:
        for tf in test_files:
            tf.meta.endpoint = settings.api_endpoint
    if settings.mcp_endpoint:
        for tf in test_files:
            tf.meta.mcp_endpoint = settings.mcp_endpoint


def build_run_plan(
    target: str,
    layer: str | None = None,
    group_str: str | None = None,
    priority: str | None = None,
    skip_setup: bool = False,
    skip_cleanup: bool = False,
) -> RunPlan:
    """Resolve CLI selection options into the concrete plan to execute.

    Raises ``click.ClickException`` when the target cannot be resolved, a file
    cannot be parsed, or the filters leave nothing to run.
    """
    test_path = resolve_target(target)
    group_ids = parse_group_ids(group_str)

    yaml_files = discover_yaml_files(test_path, layer, skip_setup)
    logger.info("discovered_files", count=len(yaml_files), test_dir=str(test_path), layer=layer)

    plan = _parse_and_filter(yaml_files, layer, group_ids, priority, skip_cleanup)
    if not plan.test_files:
        raise click.ClickException("No test files matched the given filters.")

    _apply_endpoint_overrides(plan.test_files)
    return plan
