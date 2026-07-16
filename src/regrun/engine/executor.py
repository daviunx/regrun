"""Test-execution engine: runner selection and the group/test run loop.

Extracted from ``cli.py`` so the CLI module stays under the size limit and the
execution semantics (including the cleanup-always guarantee) live in one place.

Cleanup-always guarantee (mirror of the setup-always guarantee):
  * Groups flagged ``cleanup: true`` survive ``--group`` / ``--priority``
    filtering (handled in ``cli._filter_groups``).
  * On a ``--fail-fast`` abort, cleanup-flagged groups still EXECUTE — in the
    failing file and in every later file — while all other remaining tests are
    marked skipped. The run's exit code still reflects the original failure.
  * ``--skip-cleanup`` (threaded in as ``skip_cleanup``) suppresses both: the
    filter exemption and the fail-fast execution, so cleanup groups behave like
    any other group.
"""

import time
from pathlib import Path

import structlog

from regrun.config import settings
from regrun.engine.assertions import evaluate_assertions
from regrun.engine.diagnostics import build_failure_diagnostics
from regrun.engine.reporter import RunResult, TestResult
from regrun.engine.retry import resolve_response_and_results
from regrun.engine.variables import VariableStore, capture_from_response, render_test
from regrun.models import Test, TestFile
from regrun.runners.base import RunnerResponse
from regrun.runners.bash_runner import BashRunner
from regrun.runners.fastmcp_runner import FastMcpRunner
from regrun.runners.httpx_runner import HttpxRunner
from regrun.runners.websocket_runner import WebSocketRunner

logger = structlog.get_logger()

Runner = HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner


def create_runner_for_type(runner_type: str, test_file: TestFile) -> Runner | None:
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
        return BashRunner(cwd=str(Path.cwd()), timeout=settings.timeout)

    if runner_type == "websocket":
        return WebSocketRunner(
            auth_configs=test_file.auth,
            timeout=settings.ws_timeout,
            default_auth=test_file.meta.default_auth,
        )

    logger.warning("unsupported_runner", runner=runner_type)
    return None


def get_runner_for_test(
    test: Test,
    test_file: TestFile,
    runner_cache: dict[str, Runner],
) -> Runner | None:
    """Get the runner for a test, respecting per-test runner overrides."""
    runner_type = test.runner or test_file.meta.runner
    if runner_type not in runner_cache:
        runner = create_runner_for_type(runner_type, test_file)
        if runner is not None:
            runner_cache[runner_type] = runner
        else:
            return None
    return runner_cache[runner_type]


async def close_runners(runner_cache: dict[str, Runner]) -> None:
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


def _skipped_result(test: Test, group_name: str, file_stem: str = "") -> TestResult:
    return TestResult(
        test_id=test.id,
        test_name=test.name,
        group_name=group_name,
        passed=False,
        skipped=True,
        file_stem=file_stem,
    )


async def run_tests(
    yaml_files: list[Path],
    test_files: list[TestFile],
    fail_fast: bool,
    verbose: bool,
    skip_cleanup: bool = False,
) -> RunResult:
    """Execute all tests across all files and collect results.

    On a ``--fail-fast`` abort, cleanup-flagged groups still run (unless
    ``skip_cleanup``); every other remaining test is marked skipped. Iteration
    continues across all files so cleanup groups in later files also execute.
    """
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
        runner_cache: dict[str, Runner] = {}

        for group in test_file.groups:
            # Cleanup-flagged groups still run after an abort (unless suppressed
            # by --skip-cleanup); every other remaining group is skipped.
            force_run_cleanup = group.cleanup and not skip_cleanup

            if aborted and not force_run_cleanup:
                all_results.extend(_skipped_result(t, group.name, path.stem) for t in group.tests)
                continue

            logger.info(
                "running_group",
                group_id=group.id,
                group_name=group.name,
                cleanup=group.cleanup,
                after_abort=aborted,
            )

            for test in group.tests:
                runner = get_runner_for_test(test, test_file, runner_cache)
                if runner is None:
                    effective_type = test.runner or test_file.meta.runner
                    all_results.append(
                        TestResult(
                            test_id=test.id,
                            test_name=test.name,
                            group_name=group.name,
                            passed=False,
                            error=f"Unsupported runner: {effective_type}",
                            file_stem=path.stem,
                        )
                    )
                    continue

                result = await execute_single_test(
                    test=test,
                    group_name=group.name,
                    runner=runner,
                    store=store,
                    verbose=verbose,
                    file_stem=path.stem,
                )
                all_results.append(result)

                # A failure inside a force-run cleanup group must not (re)trigger
                # the abort machinery — cleanup runs to completion.
                if fail_fast and not result.passed and not result.skipped and not force_run_cleanup:
                    logger.warning("fail_fast_triggered", test_id=test.id)
                    aborted = True

        # Close persistent runner sessions opened for this file (e.g. the
        # in-process fastmcp client). Runs after the groups loop — including on
        # fail-fast abort — before moving to the next file.
        await close_runners(runner_cache)

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


async def execute_single_test(
    test: Test,
    group_name: str,
    runner: Runner,
    store: VariableStore,
    verbose: bool,
    file_stem: str = "",
) -> TestResult:
    """Execute a single test: render, run, capture, assert."""
    test_start = time.monotonic()

    try:
        # Render template variables
        rendered_test = render_test(test, store)

        # Execute via runner; an `eventually:` block retries execute+assert.
        response, results, attempts = await resolve_response_and_results(
            rendered_test, runner, store
        )
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
                file_stem=file_stem,
                diagnostics=build_failure_diagnostics(
                    request=response.request_echo,
                    response=response,
                    failed_assertions=[],
                    attempts=attempts,
                    secrets=response.secret_values,
                ),
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

        # Full diagnostics only on failure (passing tests stay terse).
        diagnostics = None
        if not all_passed:
            diagnostics = build_failure_diagnostics(
                request=response.request_echo,
                response=response,
                failed_assertions=[ar for ar in assertion_results if not ar.passed],
                attempts=attempts,
                secrets=response.secret_values,
            )

        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=all_passed,
            duration_ms=duration_ms,
            file_stem=file_stem,
            assertion_results=assertion_results,
            diagnostics=diagnostics,
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
            file_stem=file_stem,
            diagnostics=build_failure_diagnostics(
                request=None,
                response=RunnerResponse(error=str(e)),
                failed_assertions=[],
                attempts=1,
            ),
        )
