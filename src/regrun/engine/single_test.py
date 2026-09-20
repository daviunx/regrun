"""Single-test execution: render, run, capture, assert.

Extracted from ``executor.py`` (size limits). One test in, one ``TestResult``
out — every failure mode of one test is adjudicated here, and nowhere else.
"""

import time

import structlog

from regrun.engine.assertions import evaluate_assertions
from regrun.engine.diagnostics import build_failure_diagnostics
from regrun.engine.reporter import TestResult
from regrun.engine.retry import resolve_response_and_results
from regrun.engine.runner_factory import Runner
from regrun.engine.variables import (
    UnresolvedVariableError,
    VariableStore,
    capture_from_response,
    render_test,
)
from regrun.models import Test
from regrun.runners.base import RunnerResponse

logger = structlog.get_logger()

__all__ = ["error_result", "execute_single_test"]


def error_result(test: Test, group_name: str, file_stem: str, error: str) -> TestResult:
    """A test that could not be executed at all: a suite defect, not a verdict.

    Used for the pre-execution guards (an auth profile this file never declared,
    an unsupported runner type). ``error`` is counted separately from ``failed``
    so a suite defect is never read as a product regression.
    """
    return TestResult(
        test_id=test.id,
        test_name=test.name,
        group_name=group_name,
        passed=False,
        error=error,
        file_stem=file_stem,
    )


async def execute_single_test(
    test: Test,
    group_name: str,
    runner: Runner,
    store: VariableStore,
    verbose: bool,
    file_stem: str = "",
) -> TestResult:
    """Execute a single test: render, run, capture, assert."""
    test_start = time.monotonic()

    try:
        # Render template variables
        rendered_test = render_test(test, store)

        # Execute via runner; an `eventually:` block retries execute+assert.
        response, results, attempts = await resolve_response_and_results(
            rendered_test, runner, store
        )
        duration_ms = (time.monotonic() - test_start) * 1000

        if verbose:
            logger.info(
                "test_detail",
                test_id=test.id,
                status_code=response.status_code,
                body=str(response.body)[:500] if response.body else None,
            )

        # Check for runner-level error (eventually path reports via assertions)
        if response.error and results is None:
            return TestResult(
                test_id=test.id,
                test_name=test.name,
                group_name=group_name,
                passed=False,
                error=response.error,
                duration_ms=duration_ms,
                file_stem=file_stem,
                diagnostics=build_failure_diagnostics(
                    request=response.request_echo,
                    response=response,
                    failed_assertions=[],
                    attempts=attempts,
                    secrets=response.secret_values,
                ),
            )

        # Capture variables from response
        if rendered_test.capture and response.body:
            captured = capture_from_response(rendered_test.capture, response.body)
            store.merge(captured)

        # Evaluate assertions (eventually path already ran them in the retry loop)
        assertion_results = results or evaluate_assertions(
            rendered_test.assert_,
            response.status_code,
            response.body,
        )

        # Zero evaluated assertions is a hard error, never a pass: ``all([])``
        # is True, so an assert block that matches no recognised key (e.g.
        # ``assert: {}`` or ``json_path: {}``) would otherwise report PASSED
        # having checked NOTHING — the same false-green family as
        # ``eventually.max_attempts: 0`` (closed in 0.8.3).
        if not assertion_results:
            error_msg = "zero assertions evaluated (assert block matched no recognised assertion)"
            logger.error("zero_assertions_evaluated", test_id=test.id)
            return TestResult(
                test_id=test.id,
                test_name=test.name,
                group_name=group_name,
                passed=False,
                error=error_msg,
                duration_ms=duration_ms,
                file_stem=file_stem,
                diagnostics=build_failure_diagnostics(
                    request=response.request_echo,
                    response=response,
                    failed_assertions=[],
                    attempts=attempts,
                    secrets=response.secret_values,
                ),
            )

        all_passed = all(ar.passed for ar in assertion_results)

        # Full diagnostics only on failure (passing tests stay terse).
        diagnostics = None
        if not all_passed:
            diagnostics = build_failure_diagnostics(
                request=response.request_echo,
                response=response,
                failed_assertions=[ar for ar in assertion_results if not ar.passed],
                attempts=attempts,
                secrets=response.secret_values,
            )

        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=all_passed,
            duration_ms=duration_ms,
            file_stem=file_stem,
            assertion_results=assertion_results,
            diagnostics=diagnostics,
        )

    except UnresolvedVariableError as e:
        # Strict-vars (default on): an unresolved {{VAR}} fails the test loudly,
        # naming the variable and the test — a literal "{{RUN_ID}}" fixture name
        # is byte-identical every run, a guaranteed cross-run collision.
        duration_ms = (time.monotonic() - test_start) * 1000
        error_msg = (
            f"unresolved variable in test {test.id}: {e.detail} "
            f"(template: {e.template!r}; opt out with meta.strict_vars: false "
            f"or --no-strict-vars)"
        )
        logger.error("unresolved_variable_strict", test_id=test.id, error=e.detail)
        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=False,
            error=error_msg,
            duration_ms=duration_ms,
            file_stem=file_stem,
            diagnostics=build_failure_diagnostics(
                request=None,
                response=RunnerResponse(error=error_msg),
                failed_assertions=[],
                attempts=1,
            ),
        )

    except Exception as e:
        duration_ms = (time.monotonic() - test_start) * 1000
        logger.error("test_execution_error", test_id=test.id, error=str(e))
        return TestResult(
            test_id=test.id,
            test_name=test.name,
            group_name=group_name,
            passed=False,
            error=str(e),
            duration_ms=duration_ms,
            file_stem=file_stem,
            diagnostics=build_failure_diagnostics(
                request=None,
                response=RunnerResponse(error=str(e)),
                failed_assertions=[],
                attempts=1,
            ),
        )
