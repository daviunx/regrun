"""CLI entry point for the YAML regression test runner."""

import asyncio
import logging
import sys
import time
from pathlib import Path

import click
import structlog
import yaml

from regrun.config import settings
from regrun.engine.assertions import evaluate_assertions
from regrun.engine.reporter import RunResult, TestResult, format_json, format_text
from regrun.engine.retry import resolve_response_and_results
from regrun.engine.variables import VariableStore, capture_from_response, render_test
from regrun.models import Test, TestFile
from regrun.runners.bash_runner import BashRunner
from regrun.runners.fastmcp_runner import FastMcpRunner
from regrun.runners.httpx_runner import HttpxRunner
from regrun.runners.websocket_runner import WebSocketRunner

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
) -> TestFile:
    """Filter test file groups by group IDs and/or priority."""
    groups = test_file.groups

    if group_ids:
        groups = [g for g in groups if g.id in group_ids]

    if priority:
        groups = [g for g in groups if g.priority == priority]

    return test_file.model_copy(update={"groups": groups})


def _create_runner_for_type(
    runner_type: str,
    test_file: TestFile,
) -> HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner | None:
    """Create a runner instance for the given runner type."""
    if runner_type == "httpx":
        endpoint = test_file.meta.endpoint
        if not endpoint:
            logger.error("missing_endpoint", runner=runner_type)
            return None
        return HttpxRunner(
            base_url=endpoint,
            auth_configs=test_file.auth,
            timeout=settings.timeout,
            default_auth=test_file.meta.default_auth,
        )

    if runner_type == "fastmcp":
        endpoint = test_file.meta.mcp_endpoint or test_file.meta.endpoint
        if not endpoint:
            logger.error("missing_endpoint", runner=runner_type)
            return None
        return FastMcpRunner(
            server_url=endpoint,
            auth_configs=test_file.auth,
            timeout=settings.mcp_timeout,
            default_auth=test_file.meta.default_auth,
        )

    if runner_type == "bash":
        # Use the current working directory as the cwd for bash commands.
        # This is where regrun was invoked from.
        cwd = str(Path.cwd())
        return BashRunner(cwd=cwd, timeout=settings.timeout)

    if runner_type == "websocket":
        return WebSocketRunner(
            auth_configs=test_file.auth,
            timeout=settings.ws_timeout,
            default_auth=test_file.meta.default_auth,
        )

    logger.warning("unsupported_runner", runner=runner_type)
    return None


def _get_runner_for_test(
    test: Test,
    test_file: TestFile,
    runner_cache: dict[str, HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner],
) -> HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner | None:
    """Get the runner for a test, respecting per-test runner overrides."""
    runner_type = test.runner or test_file.meta.runner
    if runner_type not in runner_cache:
        runner = _create_runner_for_type(runner_type, test_file)
        if runner is not None:
            runner_cache[runner_type] = runner
        else:
            return None
    return runner_cache[runner_type]


async def _close_runners(
    runner_cache: dict[str, HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner],
) -> None:
    """Close any runners holding persistent connections (e.g. the in-process
    fastmcp client). Best-effort: a close failure must not fail the run."""
    for runner in runner_cache.values():
        aclose = getattr(runner, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask results
            logger.warning("runner_close_failed", error=str(exc))


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


async def _run_tests(
    yaml_files: list[Path],
    test_files: list[TestFile],
    fail_fast: bool,
    verbose: bool,
) -> RunResult:
    """Execute all tests across all files and collect results."""
    store = VariableStore()
    all_results: list[TestResult] = []
    run_start = time.monotonic()
    aborted = False

    product = test_files[0].meta.product if test_files else "unknown"
    layer = test_files[0].meta.layer if len(test_files) == 1 else None

    # Load env_file from any file's meta (typically the setup file).
    # Path is relative to the test file's directory.
    for path, tf in zip(yaml_files, test_files):
        if tf.meta.env_file:
            env_path = path.parent / tf.meta.env_file
            if env_path.is_file():
                store.load_env_file(str(env_path))
            else:
                logger.warning("env_file_not_found", path=str(env_path))
            break

    for path, test_file in zip(yaml_files, test_files):
        if aborted:
            break

        logger.info("processing_file", file=path.name, layer=test_file.meta.layer)

        # Merge file-level variables (pre-render so {{timestamp}} etc. resolve).
        # Skip variables already set by a previous file or runtime capture to
        # keep identifiers like RUN_ID consistent across the entire run.
        new_vars = {
            k: store.render_string(v)
            for k, v in test_file.variables.items()
            if store.get(k) is None
        }
        store.merge(new_vars)

        # Cache runners per type to reuse within a file
        runner_cache: dict[str, HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner] = {}

        for group in test_file.groups:
            if aborted:
                break

            logger.info("running_group", group_id=group.id, group_name=group.name)

            for test in group.tests:
                if aborted:
                    all_results.append(
                        TestResult(
                            test_id=test.id,
                            test_name=test.name,
                            group_name=group.name,
                            passed=False,
                            skipped=True,
                        )
                    )
                    continue

                runner = _get_runner_for_test(test, test_file, runner_cache)
                if runner is None:
                    effective_type = test.runner or test_file.meta.runner
                    all_results.append(
                        TestResult(
                            test_id=test.id,
                            test_name=test.name,
                            group_name=group.name,
                            passed=False,
                            error=f"Unsupported runner: {effective_type}",
                        )
                    )
                    continue

                result = await _execute_single_test(
                    test=test,
                    group_name=group.name,
                    runner=runner,
                    store=store,
                    verbose=verbose,
                )
                all_results.append(result)

                if fail_fast and not result.passed and not result.skipped:
                    logger.warning("fail_fast_triggered", test_id=test.id)
                    aborted = True

        # Close persistent runner sessions opened for this file (e.g. the
        # in-process fastmcp client). Runs after the groups loop — including on
        # fail-fast abort — before moving to the next file.
        await _close_runners(runner_cache)

    run_duration = (time.monotonic() - run_start) * 1000

    return RunResult(
        product=product,
        layer=layer,
        total=len(all_results),
        passed=sum(1 for r in all_results if r.passed),
        failed=sum(1 for r in all_results if not r.passed and not r.skipped and not r.error),
        skipped=sum(1 for r in all_results if r.skipped),
        errors=sum(1 for r in all_results if r.error),
        duration_ms=run_duration,
        test_results=all_results,
    )


async def _execute_single_test(
    test: Test,
    group_name: str,
    runner: HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner,
    store: VariableStore,
    verbose: bool,
) -> TestResult:
    """Execute a single test: render, run, capture, assert."""
    test_start = time.monotonic()

    try:
        # Render template variables
        rendered_test = render_test(test, store)

        # Execute via runner; an `eventually:` block retries execute+assert.
        response, results = await resolve_response_and_results(rendered_test, runner, store)
        duration_ms = (time.monotonic() - test_start) * 1000

        if verbose:
            logger.info(
                "test_detail",
                test_id=test.id,
                status_code=response.status_code,
                body=str(response.body)[:500] if response.body else None,
            )

        # Check for runner-level error (eventually path reports via assertions)
        if response.error and results is None:
            return TestResult(
                test_id=test.id,
                test_name=test.name,
                group_name=group_name,
                passed=False,
                error=response.error,
                duration_ms=duration_ms,
            )

        # Capture variables from response
        if rendered_test.capture and response.body:
            captured = capture_from_response(rendered_test.capture, response.body)
            store.merge(captured)

        # Evaluate assertions (eventually path already ran them in the retry loop)
        assertion_results = results or evaluate_assertions(
            rendered_test.assert_,
            response.status_code,
            response.body,
        )

        all_passed = all(ar.passed for ar in assertion_results)

        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=all_passed,
            duration_ms=duration_ms,
            assertion_results=assertion_results,
        )

    except Exception as e:
        duration_ms = (time.monotonic() - test_start) * 1000
        logger.error("test_execution_error", test_id=test.id, error=str(e))
        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=False,
            error=str(e),
            duration_ms=duration_ms,
        )


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
                tf = _filter_groups(tf, group_ids, priority)
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
    run_result = asyncio.run(_run_tests(matched_paths, test_files, fail_fast, verbose))

    # Output results
    if output_format == "json":
        click.echo(format_json(run_result))
    else:
        click.echo(format_text(run_result))

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


if __name__ == "__main__":
    cli()
