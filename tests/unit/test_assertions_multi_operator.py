"""Unit tests for multi-operator JSONPath conditions.

One JSONPath key may carry several operators. EVERY recognised operator is
evaluated and each produces its own ``AssertionResult``, so the test passes only
when all of them pass. A dict that carries an unrecognised key fails on that
key in addition to the operators it does recognise, so no key is ever silently
dropped.
"""

from regrun.engine.assertions import AssertionResult, evaluate_assertions
from regrun.models import Assertion


def _run(json_path: dict, body: dict) -> list[AssertionResult]:
    return evaluate_assertions(
        Assertion(json_path=json_path),
        status_code=200,
        response_body=body,
    )


def _types(results: list[AssertionResult]) -> list[str]:
    return [r.assertion_type for r in results]


def _failed(results: list[AssertionResult]) -> list[str]:
    return [r.assertion_type for r in results if not r.passed]


# ------------------------------------------------- empty-set-tolerant operators


def test_not_empty_plus_failing_not_contains_both_evaluated() -> None:
    results = _run(
        {"$.tags[*]": {"not_empty": True, "not_contains": "banned"}},
        {"tags": ["alpha", "banned"]},
    )
    assert _types(results) == [
        "json_path($.tags[*]).not_empty",
        "json_path($.tags[*]).not_contains",
    ]
    assert _failed(results) == ["json_path($.tags[*]).not_contains"]


def test_exists_plus_failing_starts_with_both_evaluated() -> None:
    results = _run(
        {"$.slug": {"exists": True, "starts_with": "myapp-"}},
        {"slug": "other-42"},
    )
    assert _types(results) == [
        "json_path($.slug).exists",
        "json_path($.slug).starts_with",
    ]
    assert _failed(results) == ["json_path($.slug).starts_with"]


def test_not_contains_plus_any_contains_failing_on_non_empty_match_set() -> None:
    results = _run(
        {"$.items[*].name": {"not_contains": "banned", "any_contains": "wanted"}},
        {"items": [{"name": "alpha"}, {"name": "beta"}]},
    )
    assert _types(results) == [
        "json_path($.items[*].name).not_contains",
        "json_path($.items[*].name).any_contains",
    ]
    # not_contains passes (no banned value), any_contains fails (no wanted value).
    assert _failed(results) == ["json_path($.items[*].name).any_contains"]


def test_not_contains_plus_any_contains_on_empty_match_set_keeps_opposite_rules() -> None:
    # The two operators disagree on an empty match set by design: not_contains
    # passes vacuously, any_contains fails because the target is absent.
    results = _run(
        {"$.items[*].name": {"not_contains": "banned", "any_contains": "wanted"}},
        {"items": []},
    )
    assert _types(results) == [
        "json_path($.items[*].name).not_contains",
        "json_path($.items[*].name).any_contains",
    ]
    assert _failed(results) == ["json_path($.items[*].name).any_contains"]


# ------------------------------------------------------ match-requiring operators


def test_equals_plus_gte_both_evaluated_second_can_fail() -> None:
    results = _run({"$.count": {"equals": 2, "gte": 5}}, {"count": 2})
    assert _types(results) == [
        "json_path($.count).equals",
        "json_path($.count).gte",
    ]
    assert _failed(results) == ["json_path($.count).gte"]


def test_two_operators_both_passing() -> None:
    results = _run({"$.count": {"equals": 7, "gte": 5}}, {"count": 7})
    assert _types(results) == [
        "json_path($.count).equals",
        "json_path($.count).gte",
    ]
    assert all(r.passed for r in results)


def test_missing_path_with_two_match_requiring_operators_reports_one_not_found() -> None:
    results = _run({"$.absent": {"equals": 1, "starts_with": "x"}}, {"present": 1})
    assert len(results) == 1
    assert results[0].assertion_type == "json_path($.absent)"
    assert results[0].passed is False
    assert results[0].message == "Path not found in response"


def test_missing_path_still_evaluates_empty_set_tolerant_operators() -> None:
    # exists/not_empty answer the missing path themselves, so they are reported
    # alongside the single not-found result for the match-requiring operator.
    results = _run({"$.absent": {"exists": False, "equals": 1}}, {"present": 1})
    assert _types(results) == [
        "json_path($.absent).exists",
        "json_path($.absent)",
    ]
    assert _failed(results) == ["json_path($.absent)"]


# ----------------------------------------------------------- unrecognised keys


def test_recognised_plus_unknown_key_evaluates_one_and_fails_on_the_other() -> None:
    results = _run({"$.name": {"equals": "myapp", "bogus_op": 1}}, {"name": "myapp"})
    assert _types(results) == [
        "json_path($.name).equals",
        "json_path($.name)",
    ]
    assert results[0].passed is True
    assert results[1].passed is False
    assert "bogus_op" in results[1].message


def test_no_recognised_operator_fails_with_unknown_condition_type() -> None:
    results = _run({"$.name": {"bogus_op": 1}}, {"name": "myapp"})
    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].message == "Unknown condition type: ['bogus_op']"


def test_unknown_only_condition_on_missing_path_names_the_unknown_key() -> None:
    # The cause is the key, not the path: a condition with no recognised
    # operator could not be answered by any match, so it reports the unknown
    # key rather than the path.
    results = _run({"$.absent": {"bogus_op": 1}}, {"present": 1})
    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].message == "Unknown condition type: ['bogus_op']"


def test_empty_condition_fails_with_unknown_condition_type() -> None:
    results = _run({"$.name": {}}, {"name": "myapp"})
    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].message == "Unknown condition type: []"
