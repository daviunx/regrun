"""Unit tests for the setup gate in ``BlockedTracker``.

A setup file is the bootstrap contract of the whole run, and setup files never
declare ``requires:`` on each other (there is nothing to declare: they ARE the
bootstrap). So the dependency graph cannot express "the auth bootstrap failed, so
the seed file after it is pointless" -- the tracker carries that as an explicit
gate instead: once ANY setup file fails, every later file is blocked by it,
whatever it declares.

Guarantees under test:

  * a failed setup file becomes the blocker for a file that declares nothing.
  * the FIRST failed setup file stays the blocker, so the report names the root
    cause rather than the latest symptom.
  * an ordinary (non-setup) failure still only blocks its declared dependents.
"""

from regrun.engine import blocked


def test_a_file_declaring_nothing_is_not_blocked_by_an_unrelated_failure() -> None:
    tracker = blocked.BlockedTracker()
    tracker.record_failure("01_provider")
    assert tracker.blocker_for(set()) is None


def test_a_failed_setup_file_blocks_a_file_that_declares_nothing() -> None:
    """The gap a graph edge cannot cover: setup files declare no dependencies."""
    tracker = blocked.BlockedTracker()
    tracker.record_failure("00_auth", setup=True)
    assert tracker.blocker_for(set()) == "00_auth"


def test_a_failed_setup_file_blocks_a_file_with_unrelated_dependencies() -> None:
    tracker = blocked.BlockedTracker()
    tracker.record_failure("00_auth", setup=True)
    assert tracker.blocker_for({"01_provider"}) == "00_auth"


def test_the_first_failed_setup_file_stays_the_blocker() -> None:
    """The report must name the root cause, not the last thing that broke."""
    tracker = blocked.BlockedTracker()
    tracker.record_failure("00_auth", setup=True)
    tracker.record_failure("00a_seed", setup=True)
    assert tracker.blocker_for(set()) == "00_auth"


def test_the_setup_gate_outranks_a_failed_declared_dependency() -> None:
    """Both apply once setup is down; the earlier, deeper cause is the useful one."""
    tracker = blocked.BlockedTracker()
    tracker.record_failure("00_auth", setup=True)
    tracker.record_failure("01_provider")
    assert tracker.blocker_for({"01_provider"}) == "00_auth"


def test_a_non_setup_failure_does_not_arm_the_gate() -> None:
    tracker = blocked.BlockedTracker()
    tracker.record_failure("01_provider", setup=False)
    assert tracker.blocker_for(set()) is None
    assert tracker.blocker_for({"01_provider"}) == "01_provider"
