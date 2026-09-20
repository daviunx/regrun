"""CLI entry point for the YAML regression test runner."""

import asyncio
import logging
import sys

import click
import structlog

from regrun import cli_output
from regrun.config import settings
from regrun.engine import executor, selection, shardplan
from regrun.engine.linter import format_lint_report, lint_directory, lint_exit_code
from regrun.engine.reporter import RunResult
from regrun.engine.selection import CONFIG_FILENAME
from regrun.engine.variables import UnresolvedVariableError

logger = structlog.get_logger()


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


def _execute(
    plan: selection.RunPlan,
    fail_fast: bool,
    verbose: bool,
    skip_cleanup: bool,
    skip_preflight: bool,
    no_lock: bool,
    no_strict_vars: bool,
    skip_sweep: bool,
    budget_seconds: float | None,
) -> RunResult:
    """Run the plan (per-product lock held for the duration unless --no-lock)."""
    try:
        return asyncio.run(
            executor.run_tests(
                plan.paths,
                plan.test_files,
                fail_fast,
                verbose,
                skip_cleanup,
                skip_preflight,
                no_lock,
                no_strict_vars,
                skip_sweep,
                budget_seconds,
            )
        )
    except executor.RunLockError as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    except UnresolvedVariableError as e:
        # A file-level `variables:` declaration referenced an undefined variable
        # (strict-vars, default on) — a suite defect, aborted before any group.
        click.echo(
            f"UNRESOLVED VARIABLE: {e} (opt out with meta.strict_vars: false or --no-strict-vars)",
            err=True,
        )
        sys.exit(1)


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
@click.option(
    "--skip-preflight",
    is_flag=True,
    default=False,
    help="Skip preflight dependency-health checks (deliberate local override)",
)
@click.option(
    "--skip-sweep",
    is_flag=True,
    default=False,
    help="Skip the declared sweep: block (use when iterating; leaks must be swept later)",
)
@click.option(
    "--no-lock",
    is_flag=True,
    default=False,
    help="Bypass the per-product run lock (allow a concurrent run for this product)",
)
@click.option(
    "--no-strict-vars",
    is_flag=True,
    default=False,
    help="Do not fail tests on unresolved {{VAR}} templates (render as literal, warn)",
)
@click.option(
    "--file",
    "file_patterns",
    multiple=True,
    help=(
        "Run only this file stem or glob (repeatable). The setup layer and every "
        "file the selection requires are pulled in automatically"
    ),
)
@click.option(
    "--rerun-failed",
    is_flag=True,
    default=False,
    help=(
        "Run only the files that failed or were blocked in the latest report for "
        "this product and target (plus setup); exits 0 when there is nothing to re-run"
    ),
)
@click.option(
    "--shard",
    default=None,
    help=(
        "Run shard k of n (e.g. 1/3). Each shard REQUIRES a disjoint environment "
        "(its own database and index prefix); regrun cannot verify that"
    ),
)
@click.option(
    "--budget-seconds",
    type=float,
    default=None,
    help="Fail the run when its wall time exceeds this many seconds",
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
    skip_preflight: bool,
    skip_sweep: bool,
    no_lock: bool,
    no_strict_vars: bool,
    file_patterns: tuple[str, ...],
    rerun_failed: bool,
    shard: str | None,
    budget_seconds: float | None,
) -> None:
    """Run regression tests.

    TARGET is a directory path or a product name from regrun.yaml.
    """
    # Merge CLI verbose with env setting
    verbose = verbose or settings.verbose
    _configure_logging(verbose)

    plan = _plan(
        target,
        layer,
        group_str,
        priority,
        skip_setup,
        skip_cleanup,
        file_patterns,
        rerun_failed,
        shard,
    )
    if plan is None:
        return
    for note in plan.notes:
        click.echo(note)

    if dry_run:
        cli_output.print_dry_run(plan.paths, plan.test_files)
        return

    result = _execute(
        plan,
        fail_fast,
        verbose,
        skip_cleanup,
        skip_preflight,
        no_lock,
        no_strict_vars,
        skip_sweep,
        budget_seconds,
    )
    _report_and_exit(result, output_format)


def _plan(
    target: str,
    layer: str | None,
    group_str: str | None,
    priority: str | None,
    skip_setup: bool,
    skip_cleanup: bool,
    file_patterns: tuple[str, ...],
    rerun_failed: bool,
    shard: str | None,
) -> selection.RunPlan | None:
    """The plan to run, or None when the selection is legitimately empty.

    An empty selection is not a failure: ``--rerun-failed`` after a green run has
    nothing to re-run, which is the answer the operator asked for. The reason is
    echoed and the caller returns without running anything.
    """
    try:
        return selection.build_run_plan(
            target,
            layer,
            group_str,
            priority,
            skip_setup,
            skip_cleanup,
            file_patterns,
            rerun_failed,
            shard,
        )
    except selection.NothingSelected as e:
        click.echo(str(e))
        return None
    except shardplan.ShardSpecError as e:
        raise click.ClickException(str(e)) from e


def _report_and_exit(result: RunResult, output_format: str) -> None:
    """Emit the report and exit with the verdict's code."""
    # Preflight / sweep failure: instant abort before any group ran.
    if result.preflight_failed:
        cli_output.print_phase_abort(
            "PREFLIGHT",
            result.preflight_failed_name,
            result.preflight_error,
            result.preflight_diagnostics,
        )
        sys.exit(1)
    if result.sweep_failed:
        cli_output.print_phase_abort(
            "SWEEP", result.sweep_failed_name, result.sweep_error, result.sweep_diagnostics
        )
        sys.exit(1)

    cli_output.emit_and_persist(result, output_format)

    # A breached budget is a red run in its own right: the tests may all have
    # passed, but a suite nobody is willing to wait for stops being run.
    if result.failed > 0 or result.errors > 0 or result.budget_breaches:
        sys.exit(1)


@cli.command("list")
def list_products() -> None:
    """List products registered in regrun.yaml."""
    result = selection.find_config()
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
    test_path = selection.resolve_target(target)
    findings = lint_directory(test_path, budget_floor, allow_positional)
    click.echo(format_lint_report(findings, strict))
    sys.exit(lint_exit_code(findings, strict))


if __name__ == "__main__":
    cli()
