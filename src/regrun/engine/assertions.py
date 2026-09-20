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
            results.append(
                AssertionResult(
                    passed=False,
                    assertion_type=f"json_path({path})",
                    expected=condition,
                    actual=None,
                    message="Response body is not a JSON object",
                )
            )
        return results

    for path, condition in json_path_assertions.items():
        results.extend(_evaluate_json_path_condition(path, condition, response_body))

    return results


# Operators that answer an EMPTY match set themselves, so they are evaluated
# without the path-not-found guard. ``exists``/``not_empty`` report the absence
# as their own outcome, ``not_contains`` passes vacuously and ``any_contains``
# fails (a presence check against zero matches means the target is absent).
_EMPTY_SET_OPERATORS = ("exists", "not_empty", "not_contains", "any_contains")

# Operators that need a value to inspect: with no match they cannot be answered,
# so the path-not-found failure stands in for all of them (emitted once).
_MATCH_REQUIRING_OPERATORS = (
    "equals",
    "contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "starts_with",
    "matches",
)

_RECOGNISED_OPERATORS = _EMPTY_SET_OPERATORS + _MATCH_REQUIRING_OPERATORS


def _evaluate_json_path_condition(
    path: str,
    condition: dict,
    response_body: dict | list,
) -> list[AssertionResult]:
    """Evaluate EVERY operator carried by one JSONPath condition.

    A condition dict may hold several operators (``{not_empty: true,
    not_contains: banned}``). Each recognised operator produces its own
    ``AssertionResult``, so the test passes only when all of them pass. An
    unrecognised key produces a failure of its own, so no key is ever dropped
    without a signal.
    """
    try:
        parsed_expr = jsonpath_parse(path)
        matches = parsed_expr.find(response_body)
    except Exception as e:
        return [
            AssertionResult(
                passed=False,
                assertion_type=f"json_path({path})",
                expected=condition,
                actual=None,
                message=f"Invalid JSONPath expression: {e}",
            )
        ]

    actual_value = matches[0].value if matches else None
    has_match = len(matches) > 0
    results: list[AssertionResult] = []

    for operator in _EMPTY_SET_OPERATORS:
        if operator in condition:
            results.append(
                _check_empty_set_operator(
                    path, operator, condition[operator], matches, actual_value, has_match
                )
            )

    match_requiring = [op for op in _MATCH_REQUIRING_OPERATORS if op in condition]
    if match_requiring and not has_match:
        # One not-found failure covers every match-requiring operator: the cause
        # is the path, not the individual comparisons.
        results.append(
            AssertionResult(
                passed=False,
                assertion_type=f"json_path({path})",
                expected=condition,
                actual=None,
                message="Path not found in response",
            )
        )
    else:
        for operator in match_requiring:
            results.append(_check_value_operator(path, operator, condition[operator], actual_value))

    unknown = [key for key in condition if key not in _RECOGNISED_OPERATORS]
    if unknown or not results:
        results.append(
            AssertionResult(
                passed=False,
                assertion_type=f"json_path({path})",
                expected=condition,
                actual=actual_value,
                message=f"Unknown condition type: {unknown}",
            )
        )

    return results


def _check_empty_set_operator(
    path: str,
    operator: str,
    expected: Any,
    matches: list,
    actual_value: Any,
    has_match: bool,
) -> AssertionResult:
    """Evaluate one operator that defines its own behaviour on an empty match set."""
    if operator == "exists":
        passed = has_match == expected
        return AssertionResult(
            passed=passed,
            assertion_type=f"json_path({path}).exists",
            expected=expected,
            actual=has_match,
            message=f"Path {'exists' if has_match else 'missing'}, expected {'exists' if expected else 'missing'}",
        )

    if operator == "not_empty":
        is_not_empty = (
            has_match and actual_value is not None and actual_value != "" and actual_value != []
        )
        passed = is_not_empty == expected
        return AssertionResult(
            passed=passed,
            assertion_type=f"json_path({path}).not_empty",
            expected=expected,
            actual=is_not_empty,
            message=f"Value {'is not empty' if is_not_empty else 'is empty'}, expected {'not empty' if expected else 'empty'}",
        )

    match_values = [m.value for m in matches]

    # not_contains checks every matched value, not just the first, and an empty
    # match set (e.g. ``$.results[*].id`` against zero results) PASSES: the
    # value is vacuously absent.
    if operator == "not_contains":
        return _check_not_contains(path, expected, match_values)

    # any_contains is the ALL-MATCHES positive counterpart to not_contains: it
    # scans EVERY matched value (not just matches[0], as ``contains`` does) and
    # passes when at least one value's string form holds the substring. Use it
    # for order-INDEPENDENT presence on an array path like
    # ``$.results[*].metadata.entity_id`` where the target may not be rank 0
    # (ranking-fragile writes-then-read probes). Its empty-set rule is the
    # OPPOSITE of not_contains': zero matches is a fail, never a vacuous pass.
    return _check_any_contains(path, expected, match_values)


def _check_value_operator(
    path: str,
    operator: str,
    expected: Any,
    actual_value: Any,
) -> AssertionResult:
    """Evaluate one operator that inspects the first matched value."""
    if operator == "equals":
        return _check_equals(path, expected, actual_value)

    if operator == "contains":
        return _check_contains(path, expected, actual_value)

    if operator in ("gt", "gte", "lt", "lte"):
        return _check_comparison(path, operator, expected, actual_value)

    if operator == "starts_with":
        return _check_starts_with(path, expected, actual_value)

    return _check_matches(path, expected, actual_value)


def _loose_eq(a: Any, b: Any) -> bool:
    """Equality with a string-coerced fallback for JSON type mismatches.

    e.g. int ``42`` equals ``"42"`` produced by a ``{{VAR}}`` substitution.
    Shared by ``equals`` and ``not_contains``.
    """
    if a == b:
        return True
    try:
        return str(a) == str(b)
    except (TypeError, ValueError):
        return False


def _check_equals(path: str, expected: Any, actual: Any) -> AssertionResult:
    """Check exact equality, with string-coerced fallback for type mismatches."""
    passed = _loose_eq(actual, expected)
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


def _check_not_contains(path: str, expected: Any, values: list[Any]) -> AssertionResult:
    """Check that NONE of the values matched by the JSONPath equals ``expected``.

    Used for array exclusion, e.g. asserting a forbidden id is absent from
    ``$.results[*].id``. Uses the same string-coerced equality fallback as
    ``equals`` so ``42`` and ``"42"`` compare equal. An empty value set passes.
    """
    found = any(_loose_eq(v, expected) for v in values)
    passed = not found
    return AssertionResult(
        passed=passed,
        assertion_type=f"json_path({path}).not_contains",
        expected=expected,
        actual=values,
        message=f"Value set {'does not contain' if passed else 'contains'} {expected!r} (n={len(values)})",
    )


def _check_any_contains(path: str, substring: Any, values: list[Any]) -> AssertionResult:
    """Check that AT LEAST ONE matched value's string form holds ``substring``.

    The all-matches positive counterpart of ``contains`` (which inspects only
    ``matches[0]``). Scans every value produced by an array JSONPath (e.g.
    ``$.results[*].content_preview``) and passes when any one contains the
    substring — order-independent presence. Mirrors ``contains``' substring
    (not equality) semantics: ``str(value)`` is tested with Python ``in``.
    An EMPTY value set FAILS (a presence assertion against zero matches means
    the target is absent), the opposite of ``not_contains``' vacuous pass.
    """
    needle = str(substring)
    found = any(needle in str(v) for v in values)
    return AssertionResult(
        passed=found,
        assertion_type=f"json_path({path}).any_contains",
        expected=substring,
        actual=values,
        message=(
            f"Value set {'contains' if found else 'does not contain'} "
            f"{substring!r} in some element (n={len(values)})"
        ),
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
