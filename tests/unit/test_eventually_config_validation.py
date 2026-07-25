"""Unit tests for ``EventuallyConfig`` field constraints.

These pin a FALSE-GREEN defect, not a style preference. Before ``ge=1`` existed,
``eventually: {max_attempts: 0}`` was accepted by the model and produced:

  1. ``retry.run_with_retry`` -- ``for attempt in range(0)`` never executes, so
     the function returns ``(None, [])``.
  2. ``executor`` -- ``all_passed = all(ar.passed for ar in assertion_results)``
     and ``all([])`` is ``True``.
  3. The test is reported **PASSED having evaluated zero assertions**, with no
     crash, because the ``None`` response is only dereferenced on the
     ``not all_passed`` branch.

A suite could therefore silence any test by setting ``max_attempts: 0`` and the
run would stay green. Since regrun adjudicates every product's regression suite,
that is a false verdict propagated fleet-wide.

Mutation-checked per ``testing/overview.md`` § Anti-Patterns 0: reverting
``max_attempts`` to a bare ``int = 10`` turns the zero/negative cases RED.
"""

import pytest
from pydantic import ValidationError

from regrun.models import EventuallyConfig


class TestMaxAttempts:
    """``max_attempts`` must be >= 1 -- see module docstring."""

    def test_zero_max_attempts_is_rejected(self):
        """The false-green case: 0 attempts means 0 assertions evaluated."""
        with pytest.raises(ValidationError) as exc_info:
            EventuallyConfig(max_attempts=0)

        assert "max_attempts" in str(exc_info.value)

    def test_negative_max_attempts_is_rejected(self):
        with pytest.raises(ValidationError):
            EventuallyConfig(max_attempts=-1)

    def test_one_attempt_is_allowed(self):
        """1 is the floor, not 2 -- a single-shot poll is legitimate."""
        assert EventuallyConfig(max_attempts=1).max_attempts == 1

    def test_default_is_unchanged(self):
        """The constraint must not have altered the default."""
        assert EventuallyConfig().max_attempts == 10


class TestTimingFields:
    """Negative timings are nonsense and would reach ``asyncio.sleep``."""

    @pytest.mark.parametrize(
        "field",
        ["interval", "backoff", "initial_delay"],
    )
    def test_negative_timing_is_rejected(self, field):
        with pytest.raises(ValidationError):
            EventuallyConfig(**{field: -1.0})

    @pytest.mark.parametrize(
        ("field", "expected"),
        [("interval", 2.0), ("backoff", 1.0), ("initial_delay", 0.0)],
    )
    def test_timing_defaults_are_unchanged(self, field, expected):
        assert getattr(EventuallyConfig(), field) == expected

    def test_zero_timings_are_allowed(self):
        """0 is valid for all three -- only negatives are rejected."""
        config = EventuallyConfig(interval=0.0, backoff=0.0, initial_delay=0.0)

        assert (config.interval, config.backoff, config.initial_delay) == (0.0, 0.0, 0.0)
