"""Unit tests for ``extra="forbid"`` on ``Assertion`` and ``Test`` (0.9.0).

A typo'd assertion key (``statuss``, ``jsonpath``) used to be silently ignored
(pydantic default ``extra="ignore"``): the block evaluated zero assertions and
the test reported PASSED having checked nothing. Both models now reject unknown
keys at load time.
"""

import pytest
from pydantic import ValidationError

from regrun import models


def _test_doc(**overrides) -> dict:
    doc = {
        "id": "A.1",
        "name": "t",
        "method": "POST",
        "path": "/companies",
        "body": {"slug": "regr-co-{{RUN_ID}}"},
        "assert": {"status": 201},
    }
    doc.update(overrides)
    return doc


def test_assertion_rejects_typoed_key() -> None:
    with pytest.raises(ValidationError, match="statuss"):
        models.Assertion.model_validate({"statuss": 201})


def test_assertion_rejects_typoed_jsonpath_key() -> None:
    with pytest.raises(ValidationError, match="jsonpath"):
        models.Assertion.model_validate({"jsonpath": {"$.id": {"exists": True}}})


def test_assertion_accepts_all_recognised_keys() -> None:
    a = models.Assertion.model_validate(
        {
            "status": [200, 201],
            "is_error": False,
            "has_error": False,
            "last_exit_code": 0,
            "json_path": {"$.id": {"exists": True}},
            "contains": "ok",
        }
    )
    assert a.status == [200, 201]


def test_test_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bodyy"):
        models.Test.model_validate(_test_doc(bodyy={"slug": "x"}))


def test_test_rejects_json_instead_of_body() -> None:
    with pytest.raises(ValidationError, match="json"):
        models.Test.model_validate(_test_doc(json={"slug": "x"}))


def test_test_accepts_full_legitimate_shape() -> None:
    t = models.Test.model_validate(
        _test_doc(
            auth="none",
            org_header=False,
            query_params={"q": "x"},
            capture={"CO_ID": "$.id"},
            eventually={"max_attempts": 3, "interval": 0.0},
            timeout=5,
            runner="httpx",
        )
    )
    assert t.assert_.status == 201
