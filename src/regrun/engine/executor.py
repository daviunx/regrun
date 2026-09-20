"""Test-execution engine: the group/test run loop.

Runner construction lives in ``runner_factory``, single-test adjudication in
``single_test``, the preflight/sweep phases in ``phases`` and skip emission in
``blocked`` — this module is the coordinator that sequences them.

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

from regrun.engine.blocked import skipped_result
from regrun.engine.phases import (
    effective_strict,
    merge_file_variables,
    run_preflight,
    run_sweep,
)
from regrun.engine.reporter import RunResult, TestResult
from regrun.engine.run_lock import (
    RunLockError,
    acquire_run_lock,
    derive_lock_target,
    release_run_lock,
)
from regrun.engine.runner_factory import (
    AUTH_CONSUMING_RUNNERS,
    Runner,
    close_runners,
    create_runner_for_type,
    get_runner_for_test,
    unknown_auth_profile_error,
)
from regrun.engine.single_test import execute_single_test
from regrun.engine.variables import VariableStore
from regrun.models import Group, TestFile

logger = structlog.get_logger()

# The engine version that adjudicates every verdict — recorded on RunResult so
# a persisted report can always be attributed to the regrun that produced it.
try:
    REGRUN_VERSION = _pkg_version("regrun")
except PackageNotFoundError:  # pragma: no cover - source tree without install
    REGRUN_VERSION = "unknown"

__all__ = [
    "AUTH_CONSUMING_RUNNERS",
    "REGRUN_VERSION",
    "RunLockError",
    "Runner",
    "create_runner_for_type",
    "execute_single_test",
    "get_runner_for_test",
    "run_tests",
    "unknown_auth_profile_error",
]


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


def _load_env_file(
    yaml_files: list[Path], test_files: list[TestFile], store: VariableStore
) -> None:
    """Load ``meta.env_file`` from the first file declaring one (typically setup)."""
    for path, tf in zip(yaml_files, test_files):
        if tf.meta.env_file:
            env_path = path.parent / tf.meta.env_file
            if env_path.is_file():
                store.load_env_file(str(env_path))
            else:
                logger.warning("env_file_not_found", path=str(env_path))
            break


class _RunContext:
    """Immutable run-wide facts every result and abort path needs."""

    def __init__(self, test_files: list[TestFile], target: str) -> None:
        self.product = test_files[0].meta.product if test_files else "unknown"
        self.layer = test_files[0].meta.layer if len(test_files) == 1 else None
        self.target = target
        self.api_endpoint = next((tf.meta.endpoint for tf in test_files if tf.meta.endpoint), None)
        self.mcp_endpoint = next(
            (tf.meta.mcp_endpoint for tf in test_files if tf.meta.mcp_endpoint), None
        )

    def result(self, store: VariableStore, **fields: object) -> RunResult:
        """Build a ``RunResult`` carrying this run's provenance plus ``fields``."""
        return RunResult(
            product=self.product,
            layer=self.layer,
            run_id=store.effective_run_id,
            target=self.target,
            regrun_version=REGRUN_VERSION,
            api_endpoint=self.api_endpoint,
            mcp_endpoint=self.mcp_endpoint,
            **fields,
        )


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
    run_start = time.monotonic()
    ctx = _RunContext(test_files, target)

    _load_env_file(yaml_files, test_files, store)

    # Preflight phase: read-only dependency-health probes, run once before any
    # group. A failure aborts the run in seconds naming the failed dependency.
    preflight_count = 0
    if not skip_preflight:
        outcome = await run_preflight(yaml_files, test_files, store, verbose, no_strict_vars)
        if outcome is not None:
            preflight_count = outcome.count
            if outcome.failed_result is not None:
                return ctx.result(
                    store,
                    duration_ms=(time.monotonic() - run_start) * 1000,
                    preflight_count=outcome.count,
                    preflight_failed=True,
                    preflight_failed_name=outcome.failed_name,
                    preflight_diagnostics=outcome.failed_result.diagnostics,
                    preflight_error=outcome.failed_result.error,
                )

    # Sweep phase: declared pattern-based cleanup of prior-run artifacts, run
    # once after preflight and before any group. A failure aborts the run —
    # the structural sweep-first guarantee (creating fixtures into an unswept
    # environment is how cross-run collisions are born).
    sweep_count = 0
    if not skip_sweep:
        outcome = await run_sweep(yaml_files, test_files, store, verbose, no_strict_vars)
        if outcome is not None:
            sweep_count = outcome.count
            if outcome.failed_result is not None:
                return ctx.result(
                    store,
                    duration_ms=(time.monotonic() - run_start) * 1000,
                    preflight_count=preflight_count,
                    sweep_count=outcome.count,
                    sweep_failed=True,
                    sweep_failed_name=outcome.failed_name,
                    sweep_diagnostics=outcome.failed_result.diagnostics,
                    sweep_error=outcome.failed_result.error,
                )

    all_results = await _run_files(
        yaml_files, test_files, store, fail_fast, verbose, skip_cleanup, no_strict_vars
    )

    return ctx.result(
        store,
        total=len(all_results),
        passed=sum(1 for r in all_results if r.passed),
        failed=sum(1 for r in all_results if not r.passed and not r.skipped and not r.error),
        skipped=sum(1 for r in all_results if r.skipped),
        errors=sum(1 for r in all_results if r.error),
        duration_ms=(time.monotonic() - run_start) * 1000,
        test_results=all_results,
        preflight_count=preflight_count,
        sweep_count=sweep_count,
    )


async def _run_files(
    yaml_files: list[Path],
    test_files: list[TestFile],
    store: VariableStore,
    fail_fast: bool,
    verbose: bool,
    skip_cleanup: bool,
    no_strict_vars: bool,
) -> list[TestResult]:
    """Iterate files in canonical order, running each file's groups."""
    all_results: list[TestResult] = []
    aborted = False

    for path, test_file in zip(yaml_files, test_files):
        logger.info("processing_file", file=path.name, layer=test_file.meta.layer)

        # Strict-vars mode is per file (meta.strict_vars, --no-strict-vars).
        store.strict = effective_strict(test_file, no_strict_vars)

        # Merge file-level variables sequentially (skip-if-set).
        merge_file_variables(store, test_file)

        # Cache runners per type to reuse within a file
        runner_cache: dict[str, Runner] = {}

        for group in test_file.groups:
            # Cleanup-flagged groups still run after an abort (unless suppressed
            # by --skip-cleanup); every other remaining group is skipped.
            force_run_cleanup = group.cleanup and not skip_cleanup

            if aborted and not force_run_cleanup:
                all_results.extend(skipped_result(t, group.name, path.stem) for t in group.tests)
                continue

            logger.info(
                "running_group",
                group_id=group.id,
                group_name=group.name,
                cleanup=group.cleanup,
                after_abort=aborted,
            )

            results, aborted_here = await _run_group_tests(
                group, test_file, path, store, runner_cache, verbose, fail_fast, force_run_cleanup
            )
            all_results.extend(results)
            aborted = aborted or aborted_here

        # Close persistent runner sessions opened for this file (e.g. the
        # in-process fastmcp client). Runs after the groups loop — including on
        # fail-fast abort — before moving to the next file.
        await close_runners(runner_cache)

    return all_results


async def _run_group_tests(
    group: Group,
    test_file: TestFile,
    path: Path,
    store: VariableStore,
    runner_cache: dict[str, Runner],
    verbose: bool,
    fail_fast: bool,
    force_run_cleanup: bool,
) -> tuple[list[TestResult], bool]:
    """Run one group's tests; returns the results and whether fail-fast tripped."""
    results: list[TestResult] = []
    aborted = False
    group_name = group.name

    for test in group.tests:
        # Closed-world auth references (0.9.1): an ``auth:`` profile not
        # defined in THIS file hard-fails the test instead of silently
        # sending the request with no credentials.
        auth_error = unknown_auth_profile_error(test, test_file)
        if auth_error:
            logger.error("unknown_auth_profile", test_id=test.id, error=auth_error)
            results.append(
                TestResult(
                    test_id=test.id,
                    test_name=test.name,
                    group_name=group_name,
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
            results.append(
                TestResult(
                    test_id=test.id,
                    test_name=test.name,
                    group_name=group_name,
                    passed=False,
                    error=f"Unsupported runner: {effective_type}",
                    file_stem=path.stem,
                )
            )
            continue

        result = await execute_single_test(
            test=test,
            group_name=group_name,
            runner=runner,
            store=store,
            verbose=verbose,
            file_stem=path.stem,
        )
        results.append(result)

        # A failure inside a force-run cleanup group must not (re)trigger
        # the abort machinery — cleanup runs to completion.
        if fail_fast and not result.passed and not result.skipped and not force_run_cleanup:
            logger.warning("fail_fast_triggered", test_id=test.id)
            aborted = True

    return results, aborted
