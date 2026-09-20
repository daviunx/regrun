"""Pre-group phases: preflight probes and the declared sweep.

Extracted from ``executor.py`` (size limits). Both phases share one engine:
run every declared item once, in file order, and abort the run on the first
failure with zero groups executed.
"""

from pathlib import Path

from regrun.engine.reporter import TestResult
from regrun.engine.runner_factory import Runner, close_runners, get_runner_for_test
from regrun.engine.single_test import execute_single_test
from regrun.engine.variables import VariableStore
from regrun.models import TestFile

__all__ = [
    "PhaseOutcome",
    "effective_strict",
    "merge_file_variables",
    "run_preflight",
    "run_sweep",
]


class PhaseOutcome:
    """Outcome of a pre-group phase (preflight checks or sweep steps).

    ``count`` is the number of checks/steps actually executed; ``failed_result``
    / ``failed_name`` are set only when one failed (short-circuit).
    """

    def __init__(self) -> None:
        self.count = 0
        self.failed_result: TestResult | None = None
        self.failed_name: str | None = None


def effective_strict(test_file: TestFile, no_strict_vars: bool) -> bool:
    """Per-file strict-vars mode: ``meta.strict_vars`` unless globally disabled."""
    return test_file.meta.strict_vars and not no_strict_vars


def merge_file_variables(store: VariableStore, test_file: TestFile) -> None:
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


async def run_preflight(
    yaml_files: list[Path],
    test_files: list[TestFile],
    store: VariableStore,
    verbose: bool,
    no_strict_vars: bool = False,
) -> PhaseOutcome | None:
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


async def run_sweep(
    yaml_files: list[Path],
    test_files: list[TestFile],
    store: VariableStore,
    verbose: bool,
    no_strict_vars: bool = False,
) -> PhaseOutcome | None:
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
) -> PhaseOutcome | None:
    """Shared engine for the preflight and sweep phases (run-once, fail-aborts)."""
    if not items:
        return None

    # Static file variables must be resolvable inside a probe/step (env is
    # already loaded). Merge them (skip-if-set) mirroring the per-file loop;
    # capture is forbidden in both phases so no run state is introduced here.
    for _path, tf in zip(yaml_files, test_files):
        store.strict = effective_strict(tf, no_strict_vars)
        merge_file_variables(store, tf)

    outcome = PhaseOutcome()
    runner_cache: dict[str, Runner] = {}
    for path, test_file, item in items:
        store.strict = effective_strict(test_file, no_strict_vars)
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
