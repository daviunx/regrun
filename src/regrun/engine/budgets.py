"""Declared time budgets: turning measured durations into named overruns.

A suite that keeps getting slower stops being run, so a runtime ceiling is worth
asserting the same way a status code is. Budgets are OPT-IN: a file budget comes
from ``meta.budget_seconds``, the whole-run budget from ``--budget-seconds``, and
a suite that declares neither behaves exactly as it did before they existed.

A breach never reclassifies a test. The test passed; the BUDGET went red, and the
report has to show both facts or the reader will chase the wrong defect. Deciding
whether a breach reddens the exit code is the caller's job (see the CLI).
"""

from regrun.engine.reporter import BudgetBreach, FileTiming

__all__ = ["evaluate"]


def evaluate(
    file_budgets: dict[str, float],
    file_timings: list[FileTiming],
    duration_ms: float,
    run_budget_seconds: float | None = None,
) -> list[BudgetBreach]:
    """Every declared budget the run exceeded, file-scoped breaches first.

    ``file_budgets`` maps a file stem to its declared ceiling in seconds; a stem
    it omits is unbudgeted. File durations come from ``file_timings`` (the summed
    duration of the file's own tests), so a file that ran no tests can never
    breach. ``duration_ms`` is the whole run's wall time.

    File breaches follow ``file_timings`` order (slowest first), so the worst
    overrun reads first; the run-scoped breach, being about everything, comes
    last.
    """
    breaches = [
        BudgetBreach(
            scope="file",
            stem=row.stem,
            budget_seconds=file_budgets[row.stem],
            actual_seconds=row.duration_ms / 1000,
        )
        for row in file_timings
        if row.stem in file_budgets and row.duration_ms / 1000 > file_budgets[row.stem]
    ]

    run_seconds = duration_ms / 1000
    if run_budget_seconds is not None and run_seconds > run_budget_seconds:
        breaches.append(
            BudgetBreach(
                scope="run",
                stem=None,
                budget_seconds=run_budget_seconds,
                actual_seconds=run_seconds,
            )
        )
    return breaches
