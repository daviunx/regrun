"""Assertion evaluation engine for regression test results."""

import re
from typing import Any

import structlog
from jsonpath_ng import parse as jsonpath_parse
from pydantic import BaseModel

from regrun.models import Assertion

logger = structlog.get_logger()


class AssertionResult(BaseModel):
    """Result of evaluating a single assertion."""

    passed: bool
    assertion_type: str
    expected: Any = None
    actual: Any = None
    message: str = ""


def evaluate_assertions(
    assertion: Assertion,
    status_code: int | None,
    response_body: dict | list | str | None,
) -> list[AssertionResult]:
    """Evaluate all assertions against a test response.

    Args:
        assertion: The assertion specification from the test.
        status_code: HTTP status code (None for non-HTTP runners).
        response_body: Parsed response body (dict for JSON, str for raw).

    Returns:
        List of assertion results, one per evaluated assertion.
    """
    results: list[AssertionResult] = []

    if assertion.status is not None:
        results.append(_evaluate_status(assertion.status, status_code))

    if assertion.is_error is not None:
        results.append(_evaluate_is_error(assertion.is_error, response_body))

    if assertion.has_error is not None:
        results.append(_evaluate_has_error(assertion.has_error, response_body))

    if assertion.last_exit_code is not None:
        results.append(_evaluate_exit_code(assertion.last_exit_code, status_code))

    if assertion.json_path is not None:
        results.extend(_evaluate_json_paths(assertion.json_path, response_body))

    if assertion.contains is not None:
        results.append(_evaluate_contains(assertion.contains, response_body))

    return results


def _evaluate_status(
    expected: int | list[int],
    actual: int | None,
) -> AssertionResult:
    """Check HTTP status code matches expected value(s)."""
    if actual is None:
        return AssertionResult(
            passed=False,
            assertion_type="status",
            expected=expected,
            actual=None,
            message="No status code received",
        )

    if isinstance(expected, list):
        passed = actual in expected
        return AssertionResult(
            passed=passed,
            assertion_type="status",
            expected=expected,
            actual=actual,
            message=f"Status {actual} {'in' if passed else 'not in'} {expected}",
        )

    passed = actual == expected
    return AssertionResult(
        passed=passed,
        assertion_type="status",
        expected=expected,
        actual=actual,
        message=f"Status {actual} {'==' if passed else '!='} {expected}",
    )


def _evaluate_is_error(
    expected: bool,
    response_body: dict | list | str | None,
) -> AssertionResult:
    """Check MCP is_error field."""
    actual = None
    if isinstance(response_body, dict):
        actual = response_body.get("is_error", response_body.get("isError"))

    passed = actual == expected
    return AssertionResult(
        passed=passed,
        assertion_type="is_error",
        expected=expected,
        actual=actual,
        message=f"is_error {actual} {'==' if passed else '!='} {expected}",
    )


def _evaluate_has_error(
    expected: bool,
    response_body: dict | list | str | None,
) -> AssertionResult:
    """Check aggregated error field from WebSocket response."""
    actual = None
    if isinstance(response_body, dict):
        actual = response_body.get("error")

    has_error = bool(actual)
    passed = has_error == expected
    return AssertionResult(
        passed=passed,
        assertion_type="has_error",
        expected=expected,
        actual=has_error,
        message=f"has_error {has_error} {'==' if passed else '!='} {expected}",
    )


def _evaluate_exit_code(
    expected: int,
    actual: int | None,
) -> AssertionResult:
    """Check bash command exit code."""
    if actual is None:
        return AssertionResult(
            passed=False,
            assertion_type="last_exit_code",
            expected=expected,
            actual=None,
            message="No exit code received",
        )

    passed = actual == expected
    return AssertionResult(
        passed=passed,
        assertion_type="last_exit_code",
        expected=expected,
        actual=actual,
        message=f"Exit code {actual} {'==' if passed else '!='} {expected}",
    )


def _evaluate_contains(
    expected_substring: str,
    response_body: dict | list | str | None,
) -> AssertionResult:
    """Check that the response body (as string) contains a substring."""
    body_str = str(response_body) if response_body is not None else ""
    passed = expected_substring in body_str
    return AssertionResult(
        passed=passed,
        assertion_type="contains",
        expected=expected_substring,
        actual=body_str[:200] if not passed else expected_substring,
        message=f"Body {'contains' if passed else 'does not contain'} '{expected_substring}'",
    )


def _evaluate_json_paths(
    json_path_assertions: dict[str, dict],
    response_body: dict | list | str | None,
) -> list[AssertionResult]:
    """Evaluate all JSONPath assertions against the response body."""
    results: list[AssertionResult] = []

    if not isinstance(response_body, (dict, list)):
        for path, condition in json_path_assertions.items():
            results.append(AssertionResult(
                passed=False,
                assertion_type=f"json_path({path})",
                expected=condition,
                actual=None,
                message="Response body is not a JSON object",
            ))
        return results

    for path, condition in json_path_assertions.items():
        results.append(_evaluate_single_json_path(path, condition, response_body))

    return results


def _evaluate_single_json_path(
    path: str,
    condition: dict,
    response_body: dict | list,
) -> AssertionResult:
    """Evaluate a single JSONPath condition against the response body."""
    try:
        parsed_expr = jsonpath_parse(path)
        matches = parsed_expr.find(response_body)
    except Exception as e:
        return AssertionResult(
            passed=False,
            assertion_type=f"json_path({path})",
            expected=condition,
            actual=None,
            message=f"Invalid JSONPath expression: {e}",
        )

    actual_value = matches[0].value if matches else None
    has_match = len(matches) > 0

    # Process each condition type
    if "exists" in condition:
        expected_exists = condition["exists"]
        passed = has_match == expected_exists
        return AssertionResult(
            passed=passed,
            assertion_type=f"json_path({path}).exists",
            expected=expected_exists,
            actual=has_match,
            message=f"Path {'exists' if has_match else 'missing'}, expected {'exists' if expected_exists else 'missing'}",
        )

    if "not_empty" in condition:
        expected_not_empty = condition["not_empty"]
        is_not_empty = has_match and actual_value is not None and actual_value != "" and actual_value != []
        passed = is_not_empty == expected_not_empty
        return AssertionResult(
            passed=passed,
            assertion_type=f"json_path({path}).not_empty",
            expected=expected_not_empty,
            actual=is_not_empty,
            message=f"Value {'is not empty' if is_not_empty else 'is empty'}, expected {'not empty' if expected_not_empty else 'empty'}",
        )

    if not has_match:
        return AssertionResult(
            passed=False,
            assertion_type=f"json_path({path})",
            expected=condition,
            actual=None,
            message="Path not found in response",
        )

    if "equals" in condition:
        return _check_equals(path, condition["equals"], actual_value)

    if "contains" in condition:
        return _check_contains(path, condition["contains"], actual_value)

    if "gt" in condition:
        return _check_comparison(path, "gt", condition["gt"], actual_value)

    if "gte" in condition:
        return _check_comparison(path, "gte", condition["gte"], actual_value)

    if "lt" in condition:
        return _check_comparison(path, "lt", condition["lt"], actual_value)

    if "lte" in condition:
        return _check_comparison(path, "lte", condition["lte"], actual_value)

    if "starts_with" in condition:
        return _check_starts_with(path, condition["starts_with"], actual_value)

    if "matches" in condition:
        return _check_matches(path, condition["matches"], actual_value)

    return AssertionResult(
        passed=False,
        assertion_type=f"json_path({path})",
        expected=condition,
        actual=actual_value,
        message=f"Unknown condition type: {list(condition.keys())}",
    )


def _check_equals(path: str, expected: Any, actual: Any) -> AssertionResult:
    """Check exact equality, with string-coerced fallback for type mismatches."""
    passed = actual == expected
    if not passed:
        try:
            passed = str(actual) == str(expected)
        except (TypeError, ValueError):
            pass
    return AssertionResult(
        passed=passed,
        assertion_type=f"json_path({path}).equals",
        expected=expected,
        actual=actual,
        message=f"Value {'==' if passed else '!='} expected",
    )


def _check_contains(path: str, substring: str, actual: Any) -> AssertionResult:
    """Check string contains substring."""
    actual_str = str(actual)
    passed = substring in actual_str
    return AssertionResult(
        passed=passed,
        assertion_type=f"json_path({path}).contains",
        expected=substring,
        actual=actual_str,
        message=f"Value {'contains' if passed else 'does not contain'} '{substring}'",
    )


def _check_comparison(
    path: str,
    operator: str,
    expected: int | float,
    actual: Any,
) -> AssertionResult:
    """Check numeric comparison (gt, gte, lt, lte)."""
    try:
        actual_num = float(actual)
    except (TypeError, ValueError):
        return AssertionResult(
            passed=False,
            assertion_type=f"json_path({path}).{operator}",
            expected=expected,
            actual=actual,
            message=f"Value '{actual}' is not numeric",
        )

    ops = {
        "gt": actual_num > expected,
        "gte": actual_num >= expected,
        "lt": actual_num < expected,
        "lte": actual_num <= expected,
    }
    passed = ops[operator]
    op_symbols = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    return AssertionResult(
        passed=passed,
        assertion_type=f"json_path({path}).{operator}",
        expected=expected,
        actual=actual_num,
        message=f"{actual_num} {op_symbols[operator]} {expected} is {passed}",
    )


def _check_starts_with(path: str, prefix: str, actual: Any) -> AssertionResult:
    """Check string starts with prefix."""
    actual_str = str(actual)
    passed = actual_str.startswith(prefix)
    return AssertionResult(
        passed=passed,
        assertion_type=f"json_path({path}).starts_with",
        expected=prefix,
        actual=actual_str,
        message=f"Value {'starts with' if passed else 'does not start with'} '{prefix}'",
    )


def _check_matches(path: str, pattern: str, actual: Any) -> AssertionResult:
    """Check string matches regex pattern."""
    actual_str = str(actual)
    try:
        passed = bool(re.search(pattern, actual_str))
    except re.error as e:
        return AssertionResult(
            passed=False,
            assertion_type=f"json_path({path}).matches",
            expected=pattern,
            actual=actual_str,
            message=f"Invalid regex pattern: {e}",
        )
    return AssertionResult(
        passed=passed,
        assertion_type=f"json_path({path}).matches",
        expected=pattern,
        actual=actual_str,
        message=f"Value {'matches' if passed else 'does not match'} pattern '{pattern}'",
    )
