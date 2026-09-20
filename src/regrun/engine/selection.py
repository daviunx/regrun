"""Turning CLI options into the concrete set of files, groups and tests to run.

Everything between "the operator typed a target and some filters" and "here is
the ordered list of files with their surviving groups" lives here. The CLI
command stays a thin shell over ``build_run_plan``; selection semantics (which
files, in which order, with which groups) are testable without Click.

The canonical run order is defined once, by ``discover_yaml_files``: layer rank
(setup, api, mcp, chat) then filename. Every later consumer honours that order
rather than deriving its own.
"""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import click
import structlog
import yaml

from regrun.config import settings
from regrun.engine import artifacts, rerun, shardplan
from regrun.engine.depgraph import (
    LAYER_ORDER,
    FileNode,
    build,
    detect_cycles,
    selection_closure,
)
from regrun.engine.run_lock import derive_lock_target
from regrun.models import Group, TestFile

logger = structlog.get_logger()

CONFIG_FILENAME = "regrun.yaml"

__all__ = [
    "CONFIG_FILENAME",
    "LAYER_ORDER",
    "NothingSelected",
    "RunPlan",
    "build_run_plan",
    "discover_yaml_files",
    "file_nodes",
    "filter_groups",
    "find_config",
    "parse_group_ids",
    "parse_yaml_file",
    "resolve_target",
    "validate_requires",
]


class NothingSelected(Exception):
    """The selection is deliberately empty and the run is a no-op, not a failure.

    Raised by ``--rerun-failed`` when the previous report was green (or absent):
    there is nothing to re-run, which is the answer the operator wanted, so the
    caller reports the reason and exits zero.
    """


@dataclass(frozen=True)
class RunPlan:
    """The files to run, in run order, with their surviving groups.

    ``paths`` and ``test_files`` are positionally aligned: ``paths[i]`` is the
    file ``test_files[i]`` was parsed from.
    """

    paths: list[Path]
    test_files: list[TestFile]
    # Lines the CLI echoes so a filtered run says what it filtered on: which
    # report ``--rerun-failed`` read, which shard was planned.
    notes: list[str] = field(default_factory=list)


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
        file_layers = [(f, fl) for f, fl in file_layers if fl in (layer, "setup")]
    elif layer == "setup":
        file_layers = [(f, fl) for f, fl in file_layers if fl == "setup"]

    # --skip-setup drops the setup layer whatever else was selected: it is the
    # operator asserting the variables are already in place.
    if skip_setup:
        file_layers = [(f, fl) for f, fl in file_layers if fl != "setup"]

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


def _parse_files(yaml_files: list[Path]) -> RunPlan:
    """Parse every discovered file, unfiltered, preserving the canonical order."""
    paths: list[Path] = []
    test_files: list[TestFile] = []
    for path in yaml_files:
        try:
            test_files.append(parse_yaml_file(path))
            paths.append(path)
        except Exception as e:
            raise click.ClickException(f"Failed to parse {path.name}: {e}")
    return RunPlan(paths=paths, test_files=test_files)


def validate_requires(test_dir: Path, plan: RunPlan) -> None:
    """Reject a ``meta.requires:`` no run can satisfy, before anything executes.

    An unknown stem (a typo, or a producer someone renamed) and a self-reference
    are suite defects, and so is a cycle: there is no order that satisfies it.
    Dropping any of them silently would leave the run green with blocked-skip
    quietly disabled for that dependency, which is the invisible coupling the
    declaration exists to remove. Same doctrine as an unknown auth profile: fail
    loud, at load.

    "Unknown" is judged against every file in the suite DIRECTORY, not against
    the files this run loaded, so the operator's own narrowing (``--file``,
    ``--layer``, ``--shard``) never turns into an error about a producer they
    deliberately left out.
    """
    universe = {path.stem for path in test_dir.glob("*.yaml")}
    for path, test_file in zip(plan.paths, plan.test_files):
        for required in test_file.meta.requires:
            if required == path.stem:
                raise click.ClickException(
                    f"{path.name}: meta.requires lists the file itself ('{required}')."
                )
            if required not in universe:
                known = ", ".join(sorted(universe))
                raise click.ClickException(
                    f"{path.name}: meta.requires names '{required}', which is not a test file "
                    f"in {test_dir}. Known files: {known}"
                )

    cycles = detect_cycles(build(file_nodes(plan.paths, plan.test_files)))
    if cycles:
        rendered = "; ".join(" -> ".join([*cycle, cycle[0]]) for cycle in cycles)
        raise click.ClickException(
            f"meta.requires forms a cycle no run order can satisfy: {rendered}"
        )


def _filter_plan(
    plan: RunPlan,
    layer: str | None,
    group_ids: list[int] | None,
    priority: str | None,
    skip_cleanup: bool,
    direct: set[str] | None,
) -> RunPlan:
    """Apply the group/priority filters and drop files left with no groups.

    Two kinds of file are exempt, for the same reason: they are present as a
    DEPENDENCY, not as something the operator asked to narrow. Group and priority
    filters rarely match a dependency's groups, and dropping those groups drops
    the variables the selected file needs.

      * setup, unless it is the explicit target (``--layer setup``);
      * a file pulled in by the ``requires`` closure of a ``--file`` or
        ``--rerun-failed`` selection (``direct`` names the ones asked for; None
        means nothing was narrowed by file, so every file is direct).
    """
    kept_paths: list[Path] = []
    kept_files: list[TestFile] = []
    for path, tf in zip(plan.paths, plan.test_files):
        dependency_only = (tf.meta.layer == "setup" and layer != "setup") or (
            direct is not None and path.stem not in direct
        )
        filtered = tf if dependency_only else filter_groups(tf, group_ids, priority, skip_cleanup)
        if filtered.groups:
            kept_paths.append(path)
            kept_files.append(filtered)
    return RunPlan(paths=kept_paths, test_files=kept_files, notes=plan.notes)


def _apply_endpoint_overrides(test_files: list[TestFile]) -> None:
    """Apply endpoint overrides from env vars (CI uses service aliases, not *.localhost)."""
    if settings.api_endpoint:
        for tf in test_files:
            tf.meta.endpoint = settings.api_endpoint
    if settings.mcp_endpoint:
        for tf in test_files:
            tf.meta.mcp_endpoint = settings.mcp_endpoint


def file_nodes(paths: list[Path], test_files: list[TestFile]) -> list[FileNode]:
    """The dependency-graph nodes for the files a run actually loaded.

    ``requires:`` entries naming a file this run did not load are dropped: a
    filtered run cannot judge a producer it never executed. That is the ONLY
    tolerated case, and it is reachable only through the operator's own
    narrowing, because ``validate_requires`` has already rejected every entry
    that names no file in the suite directory. The self-reference guard is kept
    as a belt-and-braces for callers that skip validation (the linter builds its
    own nodes from unvalidated YAML).
    """
    stems = {path.stem for path in paths}
    return [
        FileNode(
            stem=path.stem,
            layer=tf.meta.layer,
            requires=[r for r in tf.meta.requires if r in stems and r != path.stem],
            serial=tf.meta.serial,
            test_count=sum(len(group.tests) for group in tf.groups),
        )
        for path, tf in zip(paths, test_files)
    ]


def _with_closure(nodes: list[FileNode], stems: set[str]) -> set[str]:
    """``stems`` plus everything that has to run for them (setup included).

    A setup stem also pulls the setup files sorting before it, because the setup
    layer is ordered and the first file owns the suite's variables. See
    ``depgraph.selection_closure``.
    """
    graph = build(nodes)
    selected = set(stems)
    for stem in stems:
        selected |= selection_closure(graph, stem)
    return selected


def _match_patterns(nodes: list[FileNode], patterns: tuple[str, ...]) -> set[str]:
    """Stems matching any ``--file`` pattern. A pattern matching nothing is an error.

    Silently running zero files is the worst possible answer to a typo, so each
    pattern must match at least one loaded file.
    """
    available = [node.stem for node in nodes]
    matched: set[str] = set()
    for pattern in patterns:
        hits = {stem for stem in available if stem == pattern or fnmatch.fnmatch(stem, pattern)}
        if not hits:
            known = ", ".join(sorted(available))
            raise click.ClickException(f"--file '{pattern}' matched no test file. Known: {known}")
        matched |= hits
    return matched


def _rerun_stems(nodes: list[FileNode], test_files: list[TestFile]) -> tuple[set[str], str]:
    """Stems to re-run from the latest report for this product and target, plus a note."""
    product = test_files[0].meta.product
    api_endpoint = next((tf.meta.endpoint for tf in test_files if tf.meta.endpoint), None)
    lock_target = derive_lock_target(api_endpoint)

    report = artifacts.latest_report(product, lock_target)
    if report is None:
        raise NothingSelected(
            f"No previous report for '{product}' on target '{lock_target}' — nothing to re-run."
        )

    available = {node.stem for node in nodes}
    stems = rerun.failed_stems(report) & available
    if not stems:
        raise NothingSelected(f"Last report {report} is green — nothing to re-run.")
    return stems, f"rerun-failed: {report}"


def _subset(plan: RunPlan, stems: set[str]) -> RunPlan:
    """Keep only the plan entries whose stem is selected, preserving run order."""
    kept = [(path, tf) for path, tf in zip(plan.paths, plan.test_files) if path.stem in stems]
    return RunPlan(
        paths=[path for path, _tf in kept],
        test_files=[tf for _path, tf in kept],
        notes=plan.notes,
    )


def _apply_selection(
    plan: RunPlan,
    file_patterns: tuple[str, ...],
    rerun_failed: bool,
    shard: str | None,
) -> tuple[RunPlan, set[str] | None]:
    """Narrow the plan by ``--file``, ``--rerun-failed`` and ``--shard``, in that order.

    ``--file`` and ``--rerun-failed`` both pull the dependency closure of what
    they select, so running one file never means running it without its producer.

    Returns the narrowed plan and the stems that were asked for DIRECTLY (None
    when neither file selector was used) -- the closure members are not among
    them, so a later group filter can leave them whole.
    """
    nodes = file_nodes(plan.paths, plan.test_files)

    selected: set[str] | None = None
    direct: set[str] | None = None
    if file_patterns:
        direct = _match_patterns(nodes, file_patterns)
        selected = _with_closure(nodes, direct)
    if rerun_failed:
        stems, note = _rerun_stems(nodes, plan.test_files)
        plan.notes.append(note)
        direct = stems if direct is None else direct | stems
        expanded = _with_closure(nodes, stems)
        selected = expanded if selected is None else selected & expanded

    if shard is not None:
        index, total = shardplan.parse_shard_spec(shard)
        plan.notes.append(f"shard: {index}/{total}")
        shard_stems = set(shardplan.plan_shards(nodes, total)[index - 1].stems)
        selected = shard_stems if selected is None else selected & shard_stems

    return (plan if selected is None else _subset(plan, selected)), direct


def build_run_plan(
    target: str,
    layer: str | None = None,
    group_str: str | None = None,
    priority: str | None = None,
    skip_setup: bool = False,
    skip_cleanup: bool = False,
    file_patterns: tuple[str, ...] = (),
    rerun_failed: bool = False,
    shard: str | None = None,
) -> RunPlan:
    """Resolve CLI selection options into the concrete plan to execute.

    Raises ``click.ClickException`` when the target cannot be resolved, a file
    cannot be parsed, a selector matches nothing, or the filters leave nothing to
    run. Raises ``NothingSelected`` when the selection is legitimately empty.
    """
    test_path = resolve_target(target)
    group_ids = parse_group_ids(group_str)

    yaml_files = discover_yaml_files(test_path, layer, skip_setup)
    logger.info("discovered_files", count=len(yaml_files), test_dir=str(test_path), layer=layer)

    parsed = _parse_files(yaml_files)
    validate_requires(test_path, parsed)

    # File selection runs BEFORE the group filters, so a closure member can be
    # recognised as a dependency and kept whole.
    plan, direct = _apply_selection(parsed, file_patterns, rerun_failed, shard)
    plan = _filter_plan(plan, layer, group_ids, priority, skip_cleanup, direct)
    if not plan.test_files:
        raise click.ClickException("No test files matched the given filters.")

    _apply_endpoint_overrides(plan.test_files)
    return plan
