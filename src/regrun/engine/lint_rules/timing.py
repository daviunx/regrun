"""Poll-budget rule: W003 — an ``eventually:`` ceiling below the budget floor."""

from regrun.engine.lint_rules.context import WARN, FileContext, LintFinding

__all__ = ["check_test", "eventually_ceiling"]


def eventually_ceiling(cfg: dict) -> float:
    """Worst-case wall time of an ``eventually:`` block, in seconds.

    Mirrors ``engine/retry.py``: ``initial_delay`` before the first attempt,
    then a between-attempt sleep of ``interval * backoff**k`` for
    ``k = 0 .. max_attempts-2`` (one sleep less than the attempt count).
    """
    max_attempts = int(cfg.get("max_attempts", 10))
    interval = float(cfg.get("interval", 2.0))
    backoff = float(cfg.get("backoff", 1.0))
    initial_delay = float(cfg.get("initial_delay", 0.0))
    total = initial_delay
    for k in range(max_attempts - 1):
        total += interval * (backoff**k)
    return total


def check_test(ctx: FileContext, _group_index: int, _group: dict, test: dict) -> list[LintFinding]:
    """W003 for one test."""
    ev = test.get("eventually")
    if not isinstance(ev, dict):
        return []
    ceiling = eventually_ceiling(ev)
    if ceiling >= ctx.budget_floor:
        return []
    return [
        ctx.finding(
            test.get("id", "-"),
            "W003",
            WARN,
            f"eventually ceiling {ceiling:.0f}s < floor {ctx.budget_floor:.0f}s",
        )
    ]
