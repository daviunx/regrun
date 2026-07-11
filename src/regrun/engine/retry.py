"""Retry / poll loop for the ``eventually`` primitive.

``run_with_retry`` re-runs an ``execute_fn`` + ``assert_fn`` pair until every
assertion passes or a retry budget (``max_attempts``) is exhausted. It exists so
that async / eventually-consistent operations (search indexing, event
processing, webhook delivery) can be asserted without a fixed ``sleep`` before
the check.

Design notes:
  * The loop lives here, NOT inside any runner. The coordinator that already
    composes ``runner.execute()`` + ``evaluate_assertions()`` wraps that pair in
    ``run_with_retry`` only when ``test.eventually`` is set, so each runner stays
    single-purpose (SRP).
  * ``asyncio.sleep`` is referenced via the module-level ``asyncio`` import so
    tests can patch ``regrun.engine.retry.asyncio.sleep`` to verify timing
    without real delays.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import asyncio

import structlog

from regrun.engine.assertions import AssertionResult, evaluate_assertions
from regrun.engine.variables import VariableStore
from regrun.runners.base import RunnerProtocol, RunnerResponse

if TYPE_CHECKING:
    from regrun.models import EventuallyConfig, Test

logger = structlog.get_logger()


async def run_with_retry(
    execute_fn: "Callable[[], Awaitable[RunnerResponse]]",
    assert_fn: "Callable[[RunnerResponse], list[AssertionResult]]",
    config: "EventuallyConfig",
    test_id: str,
) -> "tuple[RunnerResponse, list[AssertionResult]]":
    """Re-run ``execute_fn`` + ``assert_fn`` until all assertions pass or budget runs out.

    Behaviour:
      * An optional ``config.initial_delay`` is slept BEFORE the first attempt
        (only when greater than zero).
      * Between attempts (never before the first) it sleeps
        ``interval * (backoff ** attempt_index)`` where ``attempt_index`` starts
        at 0 for the wait that follows the first attempt.
      * Each attempt runs ``execute_fn`` then ``assert_fn`` against the response.
        If ``execute_fn`` raises, the exception is caught and wrapped in a
        ``RunnerResponse(error=...)`` (never propagated) and assertions still run
        against that wrapped response.
      * If every ``AssertionResult.passed`` is ``True`` it returns immediately
        (short-circuit), skipping any remaining attempts and between-attempt
        sleeps.
      * If ``max_attempts`` is exhausted it returns the LAST attempt's
        ``(response, results)`` (which failed) WITHOUT raising — the normal
        assertion reporting downstream surfaces the failure.

    Args:
        execute_fn: Zero-arg coroutine producing a ``RunnerResponse``.
        assert_fn: Maps a ``RunnerResponse`` to a list of ``AssertionResult``.
        config: The ``eventually`` configuration (max_attempts, interval,
            backoff, initial_delay).
        test_id: Test identifier, used only for retry logging.

    Returns:
        The final ``(RunnerResponse, list[AssertionResult])`` — the winning
        attempt on success, or the last failing attempt on exhaustion.
    """
    if config.initial_delay > 0:
        await asyncio.sleep(config.initial_delay)

    response: RunnerResponse | None = None
    results: list[AssertionResult] = []

    for attempt in range(config.max_attempts):
        # Between-attempt backoff: applied before every attempt except the first.
        if attempt > 0:
            delay = config.interval * (config.backoff ** (attempt - 1))
            await asyncio.sleep(delay)

        response = await _safe_execute(execute_fn)
        results = assert_fn(response)

        if all(result.passed for result in results):
            return response, results

        failed = [r.assertion_type for r in results if not r.passed]
        logger.info(
            "eventually_retry",
            test_id=test_id,
            attempt=attempt + 1,
            max_attempts=config.max_attempts,
            failed_assertions=failed,
        )

    # Budget exhausted: return the last (failing) attempt without raising.
    return response, results


async def _safe_execute(
    execute_fn: "Callable[[], Awaitable[RunnerResponse]]",
) -> RunnerResponse:
    """Run ``execute_fn``, wrapping any raised exception in a ``RunnerResponse``.

    A runner-level exception (connection refused, transport error) must not
    abort the retry loop — it is a candidate condition to retry, exactly like a
    failing assertion. The exception is surfaced as ``RunnerResponse.error`` so
    the assertion engine still evaluates against a real response object.
    """
    try:
        return await execute_fn()
    except Exception as exc:  # noqa: BLE001 - retried, not propagated
        return RunnerResponse(error=str(exc))


async def resolve_response_and_results(
    test: "Test",
    runner: RunnerProtocol,
    store: VariableStore,
) -> tuple[RunnerResponse, list[AssertionResult] | None, int]:
    """Run a test once, or retry it when it declares an ``eventually`` block.

    This is the single composition point the CLI coordinator calls for both the
    httpx and fastmcp runners, so the retry behaviour lives here rather than in
    any runner (keeping each runner single-purpose).

    When ``test.eventually`` is set, ``execute`` + assertion evaluation are
    wrapped in :func:`run_with_retry` and the evaluated assertion results are
    returned alongside the final response. Otherwise the runner is executed once
    and ``None`` is returned for the results, signalling the caller to apply its
    normal (error-aware) single-attempt assertion path.

    Args:
        test: The already-rendered test definition.
        runner: The runner to execute the test against.
        store: The variable store (passed through to the runner).

    Returns:
        ``(response, results, attempts)`` where ``results`` is the list of
        assertion results for an ``eventually`` test, or ``None`` for a
        single-attempt test, and ``attempts`` is the number of ``execute`` calls
        made (1 without an ``eventually`` block, up to ``max_attempts`` with one).
    """
    if test.eventually is None:
        return await runner.execute(test, store), None, 1

    # A wrapper counts execute calls so the attempt count can be surfaced to the
    # diagnostics without changing ``run_with_retry``'s 2-tuple contract.
    attempts = 0

    async def execute_fn() -> RunnerResponse:
        nonlocal attempts
        attempts += 1
        return await runner.execute(test, store)

    def assert_fn(response: RunnerResponse) -> list[AssertionResult]:
        return evaluate_assertions(test.assert_, response.status_code, response.body)

    response, results = await run_with_retry(execute_fn, assert_fn, test.eventually, test.id)
    return response, results, attempts
