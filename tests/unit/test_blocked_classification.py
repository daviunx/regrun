"""Unit tests for ``engine/blocked.py`` — blocked-file classification (0.10.0).

Pure logic over in-memory ``TestResult`` objects; no files, no network, no mocks.

Contract:

  * ``file_failed(results, cleanup_groups)`` — a file counts as FAILED when it has
    at least one failed or errored NON-cleanup test. A cleanup group's failure
    does not block downstream files (the sweep is a backstop, not a provider), and
    a skipped test is not a failure.
  * ``BlockedTracker`` accumulates failed + blocked stems and answers, for a
    dependency closure, which stem blocks it (``None`` when the closure is clean).
    Blocking is transitive: a blocked file blocks its own dependents.
  * ``blocked_result(...)`` mints the discriminated skip: ``skipped=True`` with
    ``blocked_by`` naming the blocker, so plain skips stay distinguishable.
"""

from regrun.engine import blocked
from regrun.engine.reporter import TestResult


def _result(
    test_id: str,
    *,
    passed: bool = True,
    skipped: bool = False,
    error: str | None = None,
    group_name: str = "Surface",
) -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name=f"test {test_id}",
        group_name=group_name,
        passed=passed,
        skipped=skipped,
        error=error,
        file_stem="01_provider",
    )


# ------------------------------------------------------------------------ file_failed


def test_file_with_a_failed_test_counts_as_failed() -> None:
    results = [_result("A.1"), _result("A.2", passed=False)]
    assert blocked.file_failed(results, cleanup_groups=set()) is True


def test_file_with_an_errored_test_counts_as_failed() -> None:
    results = [_result("A.1", passed=False, error="connection refused")]
    assert blocked.file_failed(results, cleanup_groups=set()) is True


def test_all_passing_file_does_not_count_as_failed() -> None:
    results = [_result("A.1"), _result("A.2")]
    assert blocked.file_failed(results, cleanup_groups=set()) is False


def test_cleanup_group_failure_does_not_count_as_failed() -> None:
    """A sweep that could not delete must not blocked-skip the rest of the suite."""
    results = [_result("A.1"), _result("CL.1", passed=False, group_name="Cleanup")]
    assert blocked.file_failed(results, cleanup_groups={"Cleanup"}) is False


def test_skipped_test_does_not_count_as_failed() -> None:
    results = [_result("A.1"), _result("A.2", passed=False, skipped=True)]
    assert blocked.file_failed(results, cleanup_groups=set()) is False


def test_empty_result_list_does_not_count_as_failed() -> None:
    assert blocked.file_failed([], cleanup_groups=set()) is False


# --------------------------------------------------------------------- BlockedTracker


def test_tracker_starts_clean() -> None:
    tracker = blocked.BlockedTracker()
    assert tracker.blocker_for({"01_provider"}) is None


def test_tracker_reports_a_failed_stem_in_the_closure() -> None:
    tracker = blocked.BlockedTracker()
    tracker.record_failure("01_provider")
    assert tracker.blocker_for({"00_setup", "01_provider"}) == "01_provider"


def test_tracker_ignores_a_failure_outside_the_closure() -> None:
    tracker = blocked.BlockedTracker()
    tracker.record_failure("04_unrelated")
    assert tracker.blocker_for({"00_setup", "01_provider"}) is None


def test_blocking_is_transitive_through_a_blocked_file() -> None:
    """A fails; B requires A and is blocked; C requires B — C is blocked too."""
    tracker = blocked.BlockedTracker()
    tracker.record_failure("01_a")
    tracker.record_blocked("02_b")
    assert tracker.blocker_for({"02_b"}) == "02_b"


def test_tracker_picks_the_first_blocker_deterministically() -> None:
    """Two blockers in one closure: the canonically first stem is reported."""
    tracker = blocked.BlockedTracker()
    tracker.record_failure("03_late")
    tracker.record_failure("01_early")
    assert tracker.blocker_for({"01_early", "03_late"}) == "01_early"


def test_tracker_exposes_failed_and_blocked_sets() -> None:
    tracker = blocked.BlockedTracker()
    tracker.record_failure("01_a")
    tracker.record_blocked("02_b")
    assert tracker.failed == {"01_a"}
    assert tracker.blocked == {"02_b"}


# --------------------------------------------------------------------- blocked_result


def test_blocked_result_is_a_discriminated_skip() -> None:
    result = blocked.blocked_result(
        test_id="C.1",
        test_name="consumer test",
        group_name="Consumer",
        file_stem="02_consumer",
        blocker="01_provider",
    )
    assert result.skipped is True
    assert result.passed is False
    assert result.blocked_by == "01_provider"
    assert result.test_id == "C.1"
    assert result.file_stem == "02_consumer"
