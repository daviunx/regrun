"""Unit tests (TDD-RED) for the ``eventually`` retry primitive.

These tests pin the contract of ``regrun.engine.retry.run_with_retry`` BEFORE
the module exists -- importing it is the expected RED failure. A later phase
adds ``engine/retry.py`` (and the real ``EventuallyConfig`` to ``models.py``)
to turn these GREEN.

Target contract::

    async def run_with_retry(
        execute_fn,   # Callable[[], Awaitable[RunnerResponse]]
        assert_fn,    # Callable[[RunnerResponse], list[AssertionResult]]
        config,       # EventuallyConfig (max_attempts, interval, backoff, initial_delay)
        test_id,      # str
    ) -> tuple[RunnerResponse, list[AssertionResult]]

Behaviour pinned here:
  * optional ``initial_delay`` slept BEFORE the first attempt
  * sleep ``interval * (backoff ** attempt_index)`` BETWEEN attempts only
  * each attempt runs ``execute_fn`` then ``assert_fn``
  * ALL AssertionResults pass -> return immediately (short-circuit)
  * ``max_attempts`` exhausted -> return the LAST (failing) attempt's results,
    NEVER raise
  * ``execute_fn`` raising -> caught and wrapped in ``RunnerResponse(error=...)``,
    NEVER propagated raw

Plain sync tests driving the async function via ``asyncio.run`` (no
pytest-asyncio / anyio dependency, matching the existing regrun test style).
``asyncio.sleep`` is patched so timing is verified without real delays.
"""

import asyncio
from dataclasses import dataclass

from regrun.engine.assertions import AssertionResult
from regrun.engine.retry import run_with_retry
from regrun.runners.base import RunnerResponse


@dataclass
class _EventuallyConfig:
    """Minimal stand-in for the real ``EventuallyConfig`` (Phase 2, Task 1).

    Mirrors the field names + defaults from the impl spec so these tests express
    the contract; the implementation phase swaps this for the real Pydantic
    model in ``regrun.models``.
    """

    max_attempts: int = 10
    interval: float = 2.0
    backoff: float = 1.0
    initial_delay: float = 0.0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ok_response() -> RunnerResponse:
    return RunnerResponse(status_code=200, body={"ok": True})


def _passing_results() -> list[AssertionResult]:
    return [AssertionResult(passed=True, assertion_type="status")]


def _failing_results() -> list[AssertionResult]:
    return [AssertionResult(passed=False, assertion_type="status")]


class _CallCounter:
    """Wraps an async ``execute_fn`` recording how many times it was called."""

    def __init__(self, response: RunnerResponse | None = None) -> None:
        self.calls = 0
        self._response = response or _ok_response()

    async def __call__(self) -> RunnerResponse:
        self.calls += 1
        return self._response


def _patch_sleep(monkeypatch) -> list[float]:
    """Replace ``asyncio.sleep`` (as imported by the retry module) with a no-op
    that records each requested duration. Returns the list of recorded sleeps.
    """
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # Patch the symbol on the engine.retry module so we capture exactly the
    # sleeps the retry loop requests (whether it references asyncio.sleep or a
    # module-level `sleep` import). Fall back to patching asyncio itself.
    import regrun.engine.retry as retry_mod

    if hasattr(retry_mod, "asyncio"):
        monkeypatch.setattr(retry_mod.asyncio, "sleep", _fake_sleep)
    else:
        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return sleeps


# --------------------------------------------------------------------------- #
# TC-1: Immediate success returns on first attempt (short-circuit)
# --------------------------------------------------------------------------- #
def test_immediate_success_returns_on_first_attempt(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    execute = _CallCounter()

    def assert_fn(_resp):
        return _passing_results()

    config = _EventuallyConfig(max_attempts=5, interval=0.1)
    response, results = asyncio.run(
        run_with_retry(execute, assert_fn, config, "TC-1")
    )

    assert execute.calls == 1  # no retry
    assert all(r.passed for r in results)
    assert response is execute._response
    assert sleeps == []  # short-circuit adds no between-attempt delay


# --------------------------------------------------------------------------- #
# TC-2: Retry on assertion failure, then succeed on attempt 4
# --------------------------------------------------------------------------- #
def test_retry_until_pass_returns_winning_attempt(monkeypatch):
    _patch_sleep(monkeypatch)
    execute = _CallCounter()
    win_response = RunnerResponse(status_code=200, body={"attempt": 4})

    state = {"n": 0}

    def assert_fn(resp):
        state["n"] += 1
        # fail attempts 1-3, pass on attempt 4
        if state["n"] >= 4:
            # mark the response we consider "the winner" for the assertion below
            execute._response = win_response  # noqa: SLF001
            return _passing_results()
        return _failing_results()

    # The winner is produced on attempt 4: make execute return win_response from
    # the 4th call onward so the returned response matches the passing attempt.
    base = _ok_response()

    async def execute_fn():
        execute.calls += 1
        return win_response if execute.calls >= 4 else base

    config = _EventuallyConfig(max_attempts=10, interval=0.1)
    response, results = asyncio.run(
        run_with_retry(execute_fn, assert_fn, config, "TC-2")
    )

    assert execute.calls == 4  # exactly four execute calls
    assert all(r.passed for r in results)
    assert response is win_response  # returns the attempt-4 result


# --------------------------------------------------------------------------- #
# TC-3: Budget exhaustion returns the LAST failing attempt (no raise)
# --------------------------------------------------------------------------- #
def test_budget_exhaustion_returns_last_failure_without_raising(monkeypatch):
    _patch_sleep(monkeypatch)

    last_response = RunnerResponse(status_code=500, body={"final": True})

    async def execute_fn():
        execute_fn.calls += 1  # type: ignore[attr-defined]
        return last_response if execute_fn.calls >= 3 else _ok_response()  # type: ignore[attr-defined]

    execute_fn.calls = 0  # type: ignore[attr-defined]

    def assert_fn(_resp):
        return _failing_results()  # never passes

    config = _EventuallyConfig(max_attempts=3, interval=0.1)
    response, results = asyncio.run(
        run_with_retry(execute_fn, assert_fn, config, "TC-3")
    )

    assert execute_fn.calls == 3  # type: ignore[attr-defined]  # exactly three calls
    assert results == _failing_results()  # last attempt's failing results
    assert all(not r.passed for r in results)
    assert response is last_response  # last attempt's response, not raised


# --------------------------------------------------------------------------- #
# TC-4: Backoff multiplier grows the between-attempt sleeps
# --------------------------------------------------------------------------- #
def test_backoff_multiplier_increases_sleep_intervals(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    execute = _CallCounter()

    def assert_fn(_resp):
        return _failing_results()  # never passes -> exhaust all attempts

    config = _EventuallyConfig(
        max_attempts=4, interval=1.0, backoff=2.0, initial_delay=0.0
    )
    asyncio.run(run_with_retry(execute, assert_fn, config, "TC-4"))

    # 4 attempts -> 3 between-attempt sleeps: interval * backoff**index
    # index 0 -> 1.0, index 1 -> 2.0, index 2 -> 4.0
    assert sleeps == [1.0, 2.0, 4.0]


# --------------------------------------------------------------------------- #
# TC-5: Fixed interval when backoff == 1.0 (no growth)
# --------------------------------------------------------------------------- #
def test_fixed_interval_when_backoff_is_one(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    execute = _CallCounter()

    def assert_fn(_resp):
        return _failing_results()

    config = _EventuallyConfig(
        max_attempts=3, interval=0.5, backoff=1.0, initial_delay=0.0
    )
    asyncio.run(run_with_retry(execute, assert_fn, config, "TC-5"))

    # 3 attempts -> 2 between-attempt sleeps, all equal to the fixed interval
    assert sleeps == [0.5, 0.5]


# --------------------------------------------------------------------------- #
# TC-6: initial_delay is slept BEFORE the first execute call
# --------------------------------------------------------------------------- #
def test_initial_delay_slept_before_first_attempt(monkeypatch):
    order: list[str] = []

    async def _fake_sleep(seconds: float) -> None:
        order.append(f"sleep:{seconds}")

    import regrun.engine.retry as retry_mod

    if hasattr(retry_mod, "asyncio"):
        monkeypatch.setattr(retry_mod.asyncio, "sleep", _fake_sleep)
    else:
        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def execute_fn():
        order.append("execute")
        return _ok_response()

    def assert_fn(_resp):
        return _passing_results()  # pass on first attempt

    config = _EventuallyConfig(max_attempts=2, interval=0.1, initial_delay=1.0)
    asyncio.run(run_with_retry(execute_fn, assert_fn, config, "TC-6"))

    # initial_delay must be the very first thing, before any execute call
    assert order[0] == "sleep:1.0"
    assert order[1] == "execute"
    # success on attempt 1 -> no further between-attempt sleeps
    assert order.count("execute") == 1


# --------------------------------------------------------------------------- #
# TC-7: execute_fn raising every attempt is CAUGHT + wrapped (never propagated)
# --------------------------------------------------------------------------- #
def test_execute_exception_is_caught_and_wrapped(monkeypatch):
    _patch_sleep(monkeypatch)

    async def execute_fn():
        execute_fn.calls += 1  # type: ignore[attr-defined]
        raise ConnectionError("connection refused")

    execute_fn.calls = 0  # type: ignore[attr-defined]

    seen_bodies: list = []

    def assert_fn(resp):
        # assert_fn still runs against the wrapped error response each attempt
        seen_bodies.append(resp)
        return _failing_results()

    config = _EventuallyConfig(max_attempts=3, interval=0.1)
    response, results = asyncio.run(
        run_with_retry(execute_fn, assert_fn, config, "TC-7")
    )

    # No raise escaped run_with_retry; the error was wrapped in a RunnerResponse
    assert isinstance(response, RunnerResponse)
    assert response.error is not None
    assert "connection refused" in response.error
    assert all(not r.passed for r in results)
    assert execute_fn.calls == 3  # type: ignore[attr-defined]  # retried, not short-circuited
