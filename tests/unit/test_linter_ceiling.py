"""Unit tests for the eventually: ceiling formula used by lint rule W003."""

import pytest

from regrun.engine.linter import eventually_ceiling


def test_fixed_interval_ceiling() -> None:
    """backoff 1.0: ceiling = initial_delay + interval * (max_attempts - 1)."""
    # 20 attempts, 3.0s interval, no backoff, 1s initial => 1 + 3*19 = 58s
    assert eventually_ceiling(
        {"max_attempts": 20, "interval": 3.0, "backoff": 1.0, "initial_delay": 1.0}
    ) == pytest.approx(58.0)


def test_no_initial_delay() -> None:
    # 15 attempts, 2.0s => 2 * 14 = 28s
    assert eventually_ceiling({"max_attempts": 15, "interval": 2.0}) == pytest.approx(28.0)


def test_geometric_backoff_sums_series() -> None:
    # 4 attempts, interval 1.0, backoff 2.0 => 1 + 2 + 4 = 7s (k=0,1,2)
    assert eventually_ceiling(
        {"max_attempts": 4, "interval": 1.0, "backoff": 2.0}
    ) == pytest.approx(7.0)


def test_defaults_match_model() -> None:
    # Model defaults: max_attempts 10, interval 2.0, backoff 1.0 => 2 * 9 = 18s
    assert eventually_ceiling({}) == pytest.approx(18.0)


def test_single_attempt_has_no_between_sleep() -> None:
    assert eventually_ceiling({"max_attempts": 1, "interval": 5.0}) == pytest.approx(0.0)
