"""Result emission for tests the run decided NOT to execute.

Two reasons a test does not run, and they are not the same thing:

* a plain SKIP — the run aborted (fail-fast) before reaching it.
* BLOCKED — something this test's file depends on failed, so running it could
  only produce noise. A cascade of 30 indistinguishable failures is 30 things to
  read and one thing to fix; one failure plus 29 BLOCKED rows naming it is one
  thing to read and the same one thing to fix.

BLOCKED is a DISCRIMINATED sub-kind of ``skipped``, never a new state:
``skipped=True`` plus ``blocked_by``. Every existing consumer's counts keep
their meaning, and the exit code is still driven by the real failure.
"""

from regrun.engine.reporter import TestResult
from regrun.models import Test

__all__ = ["BlockedTracker", "blocked_result", "file_failed", "skipped_result"]


def skipped_result(test: Test, group_name: str, file_stem: str = "") -> TestResult:
    """A plain skip: the run aborted (fail-fast) before reaching this test."""
    return TestResult(
        test_id=test.id,
        test_name=test.name,
        group_name=group_name,
        passed=False,
        skipped=True,
        file_stem=file_stem,
    )


def blocked_result(
    test_id: str,
    test_name: str,
    group_name: str,
    file_stem: str,
    blocker: str,
) -> TestResult:
    """A blocked test: not executed because ``blocker`` failed."""
    return TestResult(
        test_id=test_id,
        test_name=test_name,
        group_name=group_name,
        passed=False,
        skipped=True,
        blocked_by=blocker,
        file_stem=file_stem,
    )


def file_failed(results: list[TestResult], cleanup_groups: set[str]) -> bool:
    """Did this file fail in a way that should block the files depending on it?

    A failed or errored test counts. A CLEANUP group's failure does not: the
    sweep is a backstop, not a provider, so a delete that could not find its row
    must never blocked-skip the rest of the suite. A skipped test is not a
    failure either — it never ran, so it proved nothing either way.
    """
    return any(
        not result.passed and not result.skipped and result.group_name not in cleanup_groups
        for result in results
    )


class BlockedTracker:
    """Which files have failed or been blocked, and what that blocks next.

    Blocking is transitive by construction: a blocked file is recorded as a
    blocker in its own right, so a file depending on it is blocked too without
    the caller walking the graph a second time.
    """

    def __init__(self) -> None:
        self.failed: set[str] = set()
        self.blocked: set[str] = set()

    def record_failure(self, stem: str) -> None:
        """Mark a file as having failed on its own merits."""
        self.failed.add(stem)

    def record_blocked(self, stem: str) -> None:
        """Mark a file as blocked, which makes it a blocker for its own dependents."""
        self.blocked.add(stem)

    def blocker_for(self, dependencies: set[str]) -> str | None:
        """The stem blocking a file with this dependency closure, or None if clean.

        With more than one blocker in the closure the canonically first stem is
        reported, so the same run always names the same blocker.
        """
        candidates = dependencies & (self.failed | self.blocked)
        return min(candidates) if candidates else None
