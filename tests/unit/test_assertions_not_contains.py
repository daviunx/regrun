"""Unit tests for the ``not_contains`` JSONPath assertion (array exclusion).

``not_contains`` passes when NONE of the values matched by the JSONPath equals
the expected value. Designed for cross-tenant isolation checks like
"a forbidden id must be absent from ``$.results[*].id``", which must hold
regardless of how many (own-account) results the query returns — including an
empty result set, which passes vacuously.
"""

from regrun.engine.assertions import evaluate_assertions
from regrun.models import Assertion


def _run(json_path: dict) -> list:
    return evaluate_assertions(
        Assertion(json_path=json_path),
        status_code=200,
        response_body={
            "results": [
                {"id": 101, "name": "Own Agent A"},
                {"id": 102, "name": "Own Agent B"},
            ]
        },
    )


def test_not_contains_passes_when_value_absent():
    [r] = _run({"$.results[*].id": {"not_contains": 999}})
    assert r.passed
    assert r.assertion_type == "json_path($.results[*].id).not_contains"


def test_not_contains_fails_when_value_present():
    [r] = _run({"$.results[*].id": {"not_contains": 102}})
    assert not r.passed


def test_not_contains_string_coerced_equality():
    # "102" (string, as a {{VAR}} substitution would produce) must match int 102.
    [r] = _run({"$.results[*].id": {"not_contains": "102"}})
    assert not r.passed


def test_not_contains_passes_on_empty_result_set():
    # No results -> the forbidden value is vacuously absent -> PASS.
    results = evaluate_assertions(
        Assertion(json_path={"$.results[*].id": {"not_contains": 102}}),
        status_code=200,
        response_body={"results": []},
    )
    assert len(results) == 1
    assert results[0].passed


def test_not_contains_passes_when_path_missing():
    # Path absent entirely -> nothing to contain -> PASS.
    results = evaluate_assertions(
        Assertion(json_path={"$.results[*].id": {"not_contains": 102}}),
        status_code=200,
        response_body={"other": 1},
    )
    assert len(results) == 1
    assert results[0].passed
