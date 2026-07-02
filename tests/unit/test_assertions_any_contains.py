"""Unit tests for the ``any_contains`` JSONPath assertion (all-matches presence).

``any_contains`` is the ALL-MATCHES positive counterpart of ``contains`` (which
inspects only ``matches[0]``). It scans EVERY value produced by an array
JSONPath and passes when at least one value's string form holds the substring.
Designed for order-INDEPENDENT presence checks like "the write-then-read probe
appears SOMEWHERE in ``$.results[*].content_preview``", which must hold even
when the probe is not ranked first. Unlike ``not_contains``, an EMPTY match set
FAILS (a presence assertion against zero matches means the target is absent).
"""

from regrun.engine.assertions import evaluate_assertions
from regrun.models import Assertion


def _run(json_path: dict, body: dict) -> list:
    return evaluate_assertions(
        Assertion(json_path=json_path),
        status_code=200,
        response_body=body,
    )


_BODY = {
    "results": [
        {"content_preview": "Some Other Company 111"},
        {"content_preview": "Regression Search Probe RID999 Indexable probe"},
    ]
}


def test_any_contains_passes_when_present_in_NON_first_element():
    # The key property: the target is at index 1, NOT matches[0]. `contains`
    # (matches[0] only) would MISS it; `any_contains` scans all -> PASS.
    [r] = _run({"$.results[*].content_preview": {"any_contains": "RID999"}}, _BODY)
    assert r.passed
    assert r.assertion_type == "json_path($.results[*].content_preview).any_contains"


def test_any_contains_fails_when_absent_from_every_element():
    [r] = _run({"$.results[*].content_preview": {"any_contains": "RID_MISSING"}}, _BODY)
    assert not r.passed


def test_any_contains_substring_semantics_not_equality():
    # Substring, not full-value equality: "Probe" is a fragment of element[1].
    [r] = _run({"$.results[*].content_preview": {"any_contains": "Probe"}}, _BODY)
    assert r.passed


def test_any_contains_fails_on_empty_result_set():
    # Opposite of not_contains: a presence check against zero matches FAILS.
    [r] = _run(
        {"$.results[*].content_preview": {"any_contains": "RID999"}},
        {"results": []},
    )
    assert not r.passed


def test_any_contains_fails_when_path_missing():
    # Path absent entirely -> target cannot be present -> FAIL.
    [r] = _run(
        {"$.results[*].content_preview": {"any_contains": "RID999"}},
        {"other": 1},
    )
    assert not r.passed


def test_any_contains_string_coerced_over_non_string_values():
    # Non-string matched values are str()-coerced before the substring test.
    body = {"results": [{"id": 101}, {"id": 999}]}
    [r] = _run({"$.results[*].id": {"any_contains": "999"}}, body)
    assert r.passed
