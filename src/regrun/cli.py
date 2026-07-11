"""CLI entry point for the YAML regression test runner."""

import asyncio
import logging
import sys
from pathlib import Path

import click
import structlog
import yaml

from regrun.config import settings
from regrun.engine import artifacts, executor
from regrun.engine.linter import format_lint_report, lint_directory, lint_exit_code
from regrun.engine.reporter import format_json, format_text
from regrun.models import Group, TestFile

logger = structlog.get_logger()

CONFIG_FILENAME = "regrun.yaml"


def _find_config() -> tuple[dict, Path] | None:
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


def _resolve_target(target: str) -> Path:
    """Resolve a CLI target to a test directory path.

    If target is an existing directory, use it directly.
    Otherwise, look up as a product name in regrun.yaml.
    """
    target_path = Path(target)
    if target_path.is_dir():
        return target_path.resolve()

    # Not a directory — look up in regrun.yaml
    result = _find_config()
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


def _discover_yaml_files(
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
    layer_order = {"setup": 0, "api": 1, "mcp": 2, "chat": 3}
    file_layers.sort(key=lambda x: (layer_order.get(x[1], 99), x[0].name))

    return [f for f, _ in file_layers]


def _parse_yaml_file(path: Path) -> TestFile:
    """Parse a YAML file into a TestFile model."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return TestFile.model_validate(raw)


def _filter_groups(
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


def _print_dry_run(yaml_files: list[Path], test_files: list[TestFile]) -> None:
    """Print the test plan without executing."""
    click.echo("\n  DRY RUN - Test Plan")
    click.echo("  " + "=" * 40)

    total_tests = 0
    for path, tf in zip(yaml_files, test_files):
        click.echo(f"\n  File: {path.name}")
        click.echo(f"    Layer: {tf.meta.layer} | Runner: {tf.meta.runner}")
        if tf.meta.endpoint:
            click.echo(f"    Endpoint: {tf.meta.endpoint}")
        if tf.meta.mcp_endpoint:
            click.echo(f"    MCP Endpoint: {tf.meta.mcp_endpoint}")

        for group in tf.groups:
            test_count = len(group.tests)
            total_tests += test_count
            click.echo(
                f"    Group {group.id}: {group.name} "
                f"({test_count} tests, priority: {group.priority})"
            )
            for test in group.tests:
                method = test.method or test.tool or "bash"
                path_or_tool = test.path or test.tool or ""
                click.echo(f"      [{test.id}] {test.name} ({method} {path_or_tool})")

    click.echo(f"\n  Total: {total_tests} tests")
    click.echo("")


def _configure_logging(verbose: bool) -> None:
    """Configure structlog for the runner."""
    log_level = "DEBUG" if verbose else "INFO"
    level = logging.getLevelName(log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@click.group()
@click.version_option(package_name="regrun")
def cli() -> None:
    """YAML-driven regression test runner for HTTP APIs, MCP servers, and shell commands."""
    pass


@cli.command()
@click.argument("target")
@click.option(
    "--layer",
    type=click.Choice(["setup", "api", "mcp", "chat"]),
    default=None,
    help="Filter by layer",
)
@click.option("--group", "group_str", default=None, help="Comma-separated group IDs (e.g. 1,2,3)")
@click.option(
    "--priority",
    type=click.Choice(["high", "medium", "low"]),
    default=None,
    help="Filter by priority",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show test plan without executing")
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Log request/response bodies")
@click.option("--fail-fast", is_flag=True, default=False, help="Stop on first failure")
@click.option(
    "--skip-setup",
    is_flag=True,
    default=False,
    help="Skip setup layer (use when variables are already populated)",
)
@click.option(
    "--skip-cleanup",
    is_flag=True,
    default=False,
    help="Skip cleanup-flagged groups (use when iterating; leaks must be swept later)",
)
def run(
    target: str,
    layer: str | None,
    group_str: str | None,
    priority: str | None,
    dry_run: bool,
    output_format: str,
    verbose: bool,
    fail_fast: bool,
    skip_setup: bool,
    skip_cleanup: bool,
) -> None:
    """Run regression tests.

    TARGET is a directory path or a product name from regrun.yaml.
    """
    # Merge CLI verbose with env setting
    verbose = verbose or settings.verbose
    _configure_logging(verbose)

    test_path = _resolve_target(target)

    # Parse group IDs
    group_ids: list[int] | None = None
    if group_str:
        try:
            group_ids = [int(g.strip()) for g in group_str.split(",")]
        except ValueError:
            raise click.ClickException(
                f"Invalid group IDs: '{group_str}'. Use comma-separated integers."
            )

    # Discover YAML files
    yaml_files = _discover_yaml_files(test_path, layer, skip_setup)
    logger.info("discovered_files", count=len(yaml_files), test_dir=str(test_path), layer=layer)

    # Parse all files, tracking which paths matched
    matched_paths: list[Path] = []
    test_files: list[TestFile] = []
    for path in yaml_files:
        try:
            tf = _parse_yaml_file(path)
            # Setup runs in full when auto-included as a dependency -- group/priority
            # filters rarely match its groups, which would drop captured variables.
            # Only filter setup when it is the explicit target (--layer setup).
            if not (tf.meta.layer == "setup" and layer != "setup"):
                tf = _filter_groups(tf, group_ids, priority, skip_cleanup)
            if tf.groups:
                matched_paths.append(path)
                test_files.append(tf)
        except Exception as e:
            raise click.ClickException(f"Failed to parse {path.name}: {e}")

    if not test_files:
        raise click.ClickException("No test files matched the given filters.")

    # Apply endpoint overrides from env vars (CI uses service aliases, not *.localhost)
    if settings.api_endpoint:
        for tf in test_files:
            tf.meta.endpoint = settings.api_endpoint
    if settings.mcp_endpoint:
        for tf in test_files:
            tf.meta.mcp_endpoint = settings.mcp_endpoint

    # Dry run: just print the plan
    if dry_run:
        _print_dry_run(matched_paths, test_files)
        return

    # Execute tests
    run_result = asyncio.run(
        executor.run_tests(matched_paths, test_files, fail_fast, verbose, skip_cleanup)
    )

    # Build both reports, persist them (every run -- pass, fail, or abort), then
    # emit the requested format followed by the pointer line agents parse.
    text_report = format_text(run_result)
    json_report = format_json(run_result)
    run_dir = artifacts.write_run_artifacts(run_result, text_report, json_report)

    click.echo(json_report if output_format == "json" else text_report)
    click.echo(artifacts.pointer_line(run_dir))

    # Exit with non-zero if any failures
    if run_result.failed > 0 or run_result.errors > 0:
        sys.exit(1)


@cli.command("list")
def list_products() -> None:
    """List products registered in regrun.yaml."""
    result = _find_config()
    if result is None:
        raise click.ClickException(f"No {CONFIG_FILENAME} found. Create one with a 'paths' key.")

    config, project_root = result
    paths = config.get("paths")
    if not paths or not isinstance(paths, dict):
        raise click.ClickException(f"{CONFIG_FILENAME} has no 'paths' mapping.")

    click.echo(f"\nProducts ({CONFIG_FILENAME}):\n")
    for name, rel_path in paths.items():
        full = project_root / rel_path
        if full.is_dir():
            count = len(list(full.glob("*.yaml")))
            click.echo(f"  {name:<20} {rel_path}  ({count} files)")
        else:
            click.echo(f"  {name:<20} {rel_path}  (not found)")
    click.echo("")


@cli.command()
@click.argument("target")
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Treat warnings as errors (exit 1 on any finding)",
)
@click.option(
    "--budget-floor",
    type=float,
    default=75.0,
    help="Minimum eventually: ceiling in seconds before W003 fires (default 75)",
)
@click.option(
    "--allow-positional",
    "allow_positional",
    multiple=True,
    help="File glob(s) where positional array asserts (W002) are permitted",
)
def lint(target: str, strict: bool, budget_floor: float, allow_positional: tuple[str, ...]) -> None:
    """Statically lint a YAML regression suite (no network, no execution).

    TARGET is a directory path or a product name from regrun.yaml.
    """
    test_path = _resolve_target(target)
    findings = lint_directory(test_path, budget_floor, allow_positional)
    click.echo(format_lint_report(findings, strict))
    sys.exit(lint_exit_code(findings, strict))


if __name__ == "__main__":
    cli()
