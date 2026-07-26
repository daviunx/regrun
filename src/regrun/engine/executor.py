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
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import structlog

from regrun.config import settings
from regrun.engine.assertions import evaluate_assertions
from regrun.engine.diagnostics import build_failure_diagnostics
from regrun.engine.reporter import RunResult, TestResult
from regrun.engine.run_lock import (
    RunLockError,
    acquire_run_lock,
    derive_lock_target,
    release_run_lock,
)
from regrun.engine.retry import resolve_response_and_results
from regrun.engine.variables import (
    UnresolvedVariableError,
    VariableStore,
    capture_from_response,
    render_test,
)
from regrun.models import Test, TestFile
from regrun.runners.base import RunnerResponse
from regrun.runners.bash_runner import BashRunner
from regrun.runners.fastmcp_runner import FastMcpRunner
from regrun.runners.httpx_runner import HttpxRunner
from regrun.runners.sql_runner import SqlRunner
from regrun.runners.websocket_runner import WebSocketRunner

logger = structlog.get_logger()

# The engine version that adjudicates every verdict — recorded on RunResult so
# a persisted report can always be attributed to the regrun that produced it.
try:
    REGRUN_VERSION = _pkg_version("regrun")
except PackageNotFoundError:  # pragma: no cover - source tree without install
    REGRUN_VERSION = "unknown"

Runner = HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner | SqlRunner

__all__ = ["RunLockError", "run_tests", "create_runner_for_type"]


def create_runner_for_type(
    runner_type: str,
    test_file: TestFile,
    store: VariableStore | None = None,
) -> Runner | None:
    """Create a runner instance for the given runner type.

    ``store`` (when provided) feeds the bash child environment its ``RUN_ID``.
    """
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
        # The bash child always knows the stack under test: the resolved
        # endpoints and the run's effective RUN_ID ride the environment, so
        # ${REGRUN_API_ENDPOINT} is always correct and hardcoding a host is
        # unnecessary rather than merely discouraged.
        extra_env: dict[str, str] = {}
        if test_file.meta.endpoint:
            extra_env["REGRUN_API_ENDPOINT"] = test_file.meta.endpoint
        if test_file.meta.mcp_endpoint:
            extra_env["REGRUN_MCP_ENDPOINT"] = test_file.meta.mcp_endpoint
        if store is not None:
            extra_env["RUN_ID"] = store.effective_run_id
        return BashRunner(cwd=str(Path.cwd()), timeout=settings.timeout, env=extra_env)

    if runner_type == "sql":
        return SqlRunner(
            sql_connection=test_file.meta.sql_connection,
            cwd=str(Path.cwd()),
            timeout=settings.timeout,
        )

    if runner_type == "websocket":
        return WebSocketRunner(
            auth_configs=test_file.auth,
            timeout=settings.ws_timeout,
            default_auth=test_file.meta.default_auth,
        )

    logger.warning("unsupported_runner", runner=runner_type)
    return None


# Runner types whose requests carry auth credentials — the only ones where a
# dangling auth-profile reference silently weakens the request (bash/sql
# runners never read auth config).
AUTH_CONSUMING_RUNNERS = frozenset({"httpx", "fastmcp", "websocket"})


def unknown_auth_profile_error(test: Test, test_file: TestFile) -> str | None:
    """Return an error string when the test references an undefined auth profile.

    Auth profiles are PER-FILE (only captured variables propagate cross-file via
    the VariableStore). Before 0.9.1 an undefined ``auth:`` reference degraded
    to a warning and the request went out with NO credentials — surfacing as a
    confusing 401-instead-of-403 that reads like a product bug (rally A15,
    2026-07-26). Same closed-world doctrine as strict-vars: a dangling
    reference fails loudly, never silently weakens the request.
    """
    runner_type = test.runner or test_file.meta.runner
    if runner_type not in AUTH_CONSUMING_RUNNERS:
        return None
    auth_name = test.auth or test_file.meta.default_auth
    if not auth_name or auth_name == "none":
        return None
    if auth_name in test_file.auth:
        return None
    defined = ", ".join(sorted(test_file.auth)) or "<none>"
    source = "auth" if test.auth else "meta.default_auth"
    return (
        f"unknown auth profile in test {test.id}: {source}={auth_name!r} is not "
        f"defined in this file's auth: block (defined: {defined}). Auth profiles "
        f"are per-file — redeclare the profile in this file (its token variable "
        f"still propagates via the VariableStore), or use 'none'."
    )


def get_runner_for_test(
    test: Test,
    test_file: TestFile,
    runner_cache: dict[str, Runner],
    store: VariableStore | None = None,
) -> Runner | None:
    """Get the runner for a test, respecting per-test runner overrides."""
    runner_type = test.runner or test_file.meta.runner
    if runner_type not in runner_cache:
        runner = create_runner_for_type(runner_type, test_file, store)
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


class _PhaseOutcome:
    """Outcome of a pre-group phase (preflight checks or sweep steps).

    ``count`` is the number of checks/steps actually executed; ``failed_result``
    / ``failed_name`` are set only when one failed (short-circuit).
    """

    def __init__(self) -> None:
        self.count = 0
        self.failed_result: TestResult | None = None
        self.failed_name: str | None = None


def _effective_strict(test_file: TestFile, no_strict_vars: bool) -> bool:
    """Per-file strict-vars mode: ``meta.strict_vars`` unless globally disabled."""
    return test_file.meta.strict_vars and not no_strict_vars


def _merge_file_variables(store: VariableStore, test_file: TestFile) -> None:
    """Merge a file's declared variables into the store, SEQUENTIALLY.

    Pre-rendered so ``{{timestamp}}`` etc. resolve, one variable at a time so a
    later declaration can reference an earlier one (e.g.
    ``TAG: "regr-vis-{{RUN_ID}}"`` after ``RUN_ID: "{{timestamp}}"``).
    Variables already set by a previous file or a runtime capture are skipped to
    keep identifiers like ``RUN_ID`` consistent across the entire run.
    """
    for key, value in test_file.variables.items():
        if store.get(key) is None:
            store.set(key, store.render_string(value))


async def _run_preflight(
    yaml_files: list[Path],
    test_files: list[TestFile],
    store: VariableStore,
    verbose: bool,
    no_strict_vars: bool = False,
) -> _PhaseOutcome | None:
    """Run every ``preflight:`` check across all files, once, before any group.

    Returns ``None`` when no checks are declared. Otherwise returns an outcome
    carrying the executed count and, on the first failure, the failing result —
    the caller aborts the run without executing any group.
    """
    checks = [
        (path, tf, chk) for path, tf in zip(yaml_files, test_files) for chk in (tf.preflight or [])
    ]
    return await _run_phase(
        checks, "preflight", test_files, yaml_files, store, verbose, no_strict_vars
    )


async def _run_sweep(
    yaml_files: list[Path],
    test_files: list[TestFile],
    store: VariableStore,
    verbose: bool,
    no_strict_vars: bool = False,
) -> _PhaseOutcome | None:
    """Run every ``sweep:`` step across all files, once, after preflight and
    before any group.

    Returns ``None`` when no steps are declared. A step failure aborts the run
    with zero groups executed — a suite must not create fixtures into an
    environment it could not sweep.
    """
    steps = [
        (path, tf, step) for path, tf in zip(yaml_files, test_files) for step in (tf.sweep or [])
    ]
    return await _run_phase(steps, "sweep", test_files, yaml_files, store, verbose, no_strict_vars)


async def _run_phase(
    items: list,
    phase: str,
    test_files: list[TestFile],
    yaml_files: list[Path],
    store: VariableStore,
    verbose: bool,
    no_strict_vars: bool,
) -> _PhaseOutcome | None:
    """Shared engine for the preflight and sweep phases (run-once, fail-aborts)."""
    if not items:
        return None

    # Static file variables must be resolvable inside a probe/step (env is
    # already loaded). Merge them (skip-if-set) mirroring the per-file loop;
    # capture is forbidden in both phases so no run state is introduced here.
    for _path, tf in zip(yaml_files, test_files):
        store.strict = _effective_strict(tf, no_strict_vars)
        _merge_file_variables(store, tf)

    outcome = _PhaseOutcome()
    runner_cache: dict[str, Runner] = {}
    for path, test_file, item in items:
        store.strict = _effective_strict(test_file, no_strict_vars)
        test = item.as_test()
        runner = get_runner_for_test(test, test_file, runner_cache, store)
        outcome.count += 1
        if runner is None:
            effective_type = test.runner or test_file.meta.runner
            outcome.failed_result = TestResult(
                test_id=test.id,
                test_name=test.name,
                group_name=phase,
                passed=False,
                error=f"Unsupported runner: {effective_type}",
                file_stem=path.stem,
            )
            outcome.failed_name = item.name
            break

        result = await execute_single_test(
            test=test,
            group_name=phase,
            runner=runner,
            store=store,
            verbose=verbose,
            file_stem=path.stem,
        )
        if not result.passed:
            outcome.failed_result = result
            outcome.failed_name = item.name
            break

    await close_runners(runner_cache)
    return outcome


async def run_tests(
    yaml_files: list[Path],
    test_files: list[TestFile],
    fail_fast: bool,
    verbose: bool,
    skip_cleanup: bool = False,
    skip_preflight: bool = False,
    no_lock: bool = False,
    no_strict_vars: bool = False,
    skip_sweep: bool = False,
) -> RunResult:
    """Execute all tests across all files and collect results.

    A per-product exclusive run lock is held for the duration (unless
    ``no_lock``): a concurrent run for the same product raises ``RunLockError``
    so the sweep-first no-concurrency assumption is enforced mechanically.

    Preflight checks (``preflight:`` blocks, collected across all loaded files in
    file order) run once, after env/variables load and before any group. Any
    preflight failure aborts the run immediately with a ``preflight_failed``
    result and executes zero groups. ``skip_preflight`` bypasses them.

    Sweep steps (``sweep:`` blocks, same collection order) run once, AFTER
    preflight and before any group — the structural sweep-first guarantee. A
    step failure aborts the run (``sweep_failed`` result, zero groups
    executed): a suite must not create fixtures into an environment it could
    not sweep. ``skip_sweep`` bypasses them; ``cleanup: true`` group semantics
    are untouched (tail-end backstop).

    On a ``--fail-fast`` abort, cleanup-flagged groups still run (unless
    ``skip_cleanup``); every other remaining test is marked skipped. Iteration
    continues across all files so cleanup groups in later files also execute.
    """
    lock_product = test_files[0].meta.product if test_files else "unknown"
    # The lock target keys BOTH the run lock and the artifacts namespace:
    # same product against the same stack serializes; different stacks (e.g.
    # two isolate slugs) run concurrently. meta.endpoint already reflects the
    # REGRUN_API_ENDPOINT override (applied in cli before this call).
    api_endpoint = next((tf.meta.endpoint for tf in test_files if tf.meta.endpoint), None)
    target = derive_lock_target(api_endpoint)
    lock_fd = None if no_lock else acquire_run_lock(lock_product, target)
    try:
        return await _run_tests_locked(
            yaml_files,
            test_files,
            fail_fast,
            verbose,
            skip_cleanup,
            skip_preflight,
            no_strict_vars,
            skip_sweep,
            target,
        )
    finally:
        release_run_lock(lock_fd)


async def _run_tests_locked(
    yaml_files: list[Path],
    test_files: list[TestFile],
    fail_fast: bool,
    verbose: bool,
    skip_cleanup: bool,
    skip_preflight: bool,
    no_strict_vars: bool = False,
    skip_sweep: bool = False,
    target: str = "default",
) -> RunResult:
    """Run body, executed while the per-product lock is held (see ``run_tests``)."""
    store = VariableStore()
    all_results: list[TestResult] = []
    run_start = time.monotonic()
    aborted = False

    product = test_files[0].meta.product if test_files else "unknown"
    layer = test_files[0].meta.layer if len(test_files) == 1 else None

    # Report provenance: the resolved endpoints (meta.* already reflects the
    # REGRUN_* overrides applied in cli) ride every RunResult.
    api_endpoint = next((tf.meta.endpoint for tf in test_files if tf.meta.endpoint), None)
    mcp_endpoint = next((tf.meta.mcp_endpoint for tf in test_files if tf.meta.mcp_endpoint), None)

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

    # Preflight phase: read-only dependency-health probes, run once before any
    # group. A failure aborts the run in seconds naming the failed dependency.
    preflight_count = 0
    if not skip_preflight:
        preflight_result = await _run_preflight(
            yaml_files, test_files, store, verbose, no_strict_vars
        )
        if preflight_result is not None:
            preflight_count = preflight_result.count
            if preflight_result.failed_result is not None:
                run_duration = (time.monotonic() - run_start) * 1000
                return RunResult(
                    product=product,
                    layer=layer,
                    run_id=store.effective_run_id,
                    target=target,
                    regrun_version=REGRUN_VERSION,
                    api_endpoint=api_endpoint,
                    mcp_endpoint=mcp_endpoint,
                    duration_ms=run_duration,
                    preflight_count=preflight_result.count,
                    preflight_failed=True,
                    preflight_failed_name=preflight_result.failed_name,
                    preflight_diagnostics=preflight_result.failed_result.diagnostics,
                    preflight_error=preflight_result.failed_result.error,
                )

    # Sweep phase: declared pattern-based cleanup of prior-run artifacts, run
    # once after preflight and before any group. A failure aborts the run —
    # the structural sweep-first guarantee (creating fixtures into an unswept
    # environment is how cross-run collisions are born).
    sweep_count = 0
    if not skip_sweep:
        sweep_result = await _run_sweep(yaml_files, test_files, store, verbose, no_strict_vars)
        if sweep_result is not None:
            sweep_count = sweep_result.count
            if sweep_result.failed_result is not None:
                run_duration = (time.monotonic() - run_start) * 1000
                return RunResult(
                    product=product,
                    layer=layer,
                    run_id=store.effective_run_id,
                    target=target,
                    regrun_version=REGRUN_VERSION,
                    api_endpoint=api_endpoint,
                    mcp_endpoint=mcp_endpoint,
                    duration_ms=run_duration,
                    preflight_count=preflight_count,
                    sweep_count=sweep_result.count,
                    sweep_failed=True,
                    sweep_failed_name=sweep_result.failed_name,
                    sweep_diagnostics=sweep_result.failed_result.diagnostics,
                    sweep_error=sweep_result.failed_result.error,
                )

    for path, test_file in zip(yaml_files, test_files):
        logger.info("processing_file", file=path.name, layer=test_file.meta.layer)

        # Strict-vars mode is per file (meta.strict_vars, --no-strict-vars).
        store.strict = _effective_strict(test_file, no_strict_vars)

        # Merge file-level variables sequentially (skip-if-set).
        _merge_file_variables(store, test_file)

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
                # Closed-world auth references (0.9.1): an ``auth:`` profile not
                # defined in THIS file hard-fails the test instead of silently
                # sending the request with no credentials.
                auth_error = unknown_auth_profile_error(test, test_file)
                if auth_error:
                    logger.error("unknown_auth_profile", test_id=test.id, error=auth_error)
                    all_results.append(
                        TestResult(
                            test_id=test.id,
                            test_name=test.name,
                            group_name=group.name,
                            passed=False,
                            error=auth_error,
                            file_stem=path.stem,
                        )
                    )
                    if fail_fast and not force_run_cleanup:
                        logger.warning("fail_fast_triggered", test_id=test.id)
                        aborted = True
                    continue

                runner = get_runner_for_test(test, test_file, runner_cache, store)
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
        run_id=store.effective_run_id,
        target=target,
        regrun_version=REGRUN_VERSION,
        api_endpoint=api_endpoint,
        mcp_endpoint=mcp_endpoint,
        total=len(all_results),
        passed=sum(1 for r in all_results if r.passed),
        failed=sum(1 for r in all_results if not r.passed and not r.skipped and not r.error),
        skipped=sum(1 for r in all_results if r.skipped),
        errors=sum(1 for r in all_results if r.error),
        duration_ms=run_duration,
        test_results=all_results,
        preflight_count=preflight_count,
        sweep_count=sweep_count,
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

        # Zero evaluated assertions is a hard error, never a pass: ``all([])``
        # is True, so an assert block that matches no recognised key (e.g.
        # ``assert: {}`` or ``json_path: {}``) would otherwise report PASSED
        # having checked NOTHING — the same false-green family as
        # ``eventually.max_attempts: 0`` (closed in 0.8.3).
        if not assertion_results:
            error_msg = "zero assertions evaluated (assert block matched no recognised assertion)"
            logger.error("zero_assertions_evaluated", test_id=test.id)
            return TestResult(
                test_id=test.id,
                test_name=test.name,
                group_name=group_name,
                passed=False,
                error=error_msg,
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

    except UnresolvedVariableError as e:
        # Strict-vars (default on): an unresolved {{VAR}} fails the test loudly,
        # naming the variable and the test — a literal "{{RUN_ID}}" fixture name
        # is byte-identical every run, a guaranteed cross-run collision.
        duration_ms = (time.monotonic() - test_start) * 1000
        error_msg = (
            f"unresolved variable in test {test.id}: {e.detail} "
            f"(template: {e.template!r}; opt out with meta.strict_vars: false "
            f"or --no-strict-vars)"
        )
        logger.error("unresolved_variable_strict", test_id=test.id, error=e.detail)
        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=False,
            error=error_msg,
            duration_ms=duration_ms,
            file_stem=file_stem,
            diagnostics=build_failure_diagnostics(
                request=None,
                response=RunnerResponse(error=error_msg),
                failed_assertions=[],
                attempts=1,
            ),
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
