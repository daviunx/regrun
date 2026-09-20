"""Terminal output for the ``run`` command: the dry-run plan, aborts, reports.

Separated from ``cli.py`` so the command body stays a readable sequence of
decisions rather than a wall of ``click.echo`` calls.
"""

from pathlib import Path

import click

from regrun.engine import artifacts
from regrun.engine.junit import format_junit
from regrun.engine.reporter import FailureDiagnostics, RunResult, format_json, format_text
from regrun.models import TestFile

__all__ = ["emit_and_persist", "print_dry_run", "print_phase_abort"]


def print_dry_run(yaml_files: list[Path], test_files: list[TestFile]) -> None:
    """Print the test plan without executing."""
    click.echo("\n  DRY RUN - Test Plan")
    click.echo("  " + "=" * 40)

    preflight_checks = [
        (path, chk) for path, tf in zip(yaml_files, test_files) for chk in (tf.preflight or [])
    ]
    if preflight_checks:
        click.echo(f"\n  Preflight ({len(preflight_checks)} checks):")
        for path, chk in preflight_checks:
            runner = chk.runner or "meta.runner"
            click.echo(f"    [{path.name}] {chk.name} (runner: {runner})")

    sweep_steps = [
        (path, step) for path, tf in zip(yaml_files, test_files) for step in (tf.sweep or [])
    ]
    if sweep_steps:
        click.echo(f"\n  Sweep ({len(sweep_steps)} steps):")
        for path, step in sweep_steps:
            runner = step.runner or "meta.runner"
            click.echo(f"    [{path.name}] {step.name} (runner: {runner})")

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


def print_phase_abort(
    label: str,
    name: str | None,
    error: str | None,
    diagnostics: FailureDiagnostics | None,
) -> None:
    """Report a preflight or sweep abort: the failed step plus its diagnostics.

    Both phases share one contract — an instant abort with zero groups executed
    (a suite must not create fixtures into an unswept environment, and must not
    run at all against an unhealthy dependency) — so they share one renderer.
    """
    click.echo(f"{label} FAILED: {name}")
    if error:
        click.echo(f"  error: {error}")
    if diagnostics is None:
        return
    for ar in diagnostics.failed_assertions:
        click.echo(f"  ✗ {ar.assertion_type}: {ar.message}")
    if diagnostics.response_body is not None:
        click.echo(f"  response body: {diagnostics.response_body}")


def emit_and_persist(run_result: RunResult, output_format: str) -> None:
    """Print the run's report, then persist all three formats to the runs dir.

    The report is emitted FIRST and unconditionally -- showing the run is the
    whole point; artifact persistence is a best-effort side channel that must
    never suppress it. A write failure (unwritable REGRUN_RUNS_DIR, disk full,
    bad product name) is surfaced as a warning on stderr and never changes the
    exit code.
    """
    text_report = format_text(run_result)
    json_report = format_json(run_result)
    junit_report = format_junit(run_result)
    click.echo(json_report if output_format == "json" else text_report)

    try:
        run_dir = artifacts.write_run_artifacts(run_result, text_report, json_report, junit_report)
        click.echo(artifacts.pointer_line(run_dir))
    except OSError as e:
        click.echo(f"Warning: could not persist run artifacts: {e}", err=True)
