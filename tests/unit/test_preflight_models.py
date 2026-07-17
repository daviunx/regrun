"""Unit tests for the `preflight:` schema primitive (Phase 2) — RED gate.

Asserts the planned 0.8.0 contract:

  * `TestFile.preflight` parses a list of `PreflightCheck`.
  * a `PreflightCheck` carrying `eventually:` is REJECTED at validation.
  * a `PreflightCheck` carrying `capture:` is REJECTED at validation.
  * a clean check parses and its per-check `timeout` defaults to 10.0.

Written before the model exists — attribute/validation failures are the red
signal until Phase 2 lands.
"""

import pytest
from pydantic import ValidationError

from regrun import models


def _clean_check() -> dict:
    return {
        "name": "backend-health",
        "runner": "bash",
        "commands": [{"cmd": "true"}],
        "assert": {"last_exit_code": 0},
    }


def test_preflight_check_clean_parses() -> None:
    pc = models.PreflightCheck.model_validate(_clean_check())
    assert pc.name == "backend-health"


def test_preflight_check_timeout_defaults_to_ten() -> None:
    pc = models.PreflightCheck.model_validate(_clean_check())
    assert pc.timeout == 10.0


def test_preflight_check_rejects_eventually() -> None:
    doc = _clean_check()
    doc["eventually"] = {"max_attempts": 3, "interval": 2.0}
    with pytest.raises(ValidationError):
        models.PreflightCheck.model_validate(doc)


def test_preflight_check_rejects_capture() -> None:
    doc = _clean_check()
    doc["capture"] = {"SOME_VAR": "stdout"}
    with pytest.raises(ValidationError):
        models.PreflightCheck.model_validate(doc)


def test_test_file_parses_preflight_list() -> None:
    tf = models.TestFile.model_validate(
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "preflight": [_clean_check()],
            "groups": [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [{"id": "A.1", "name": "t", "commands": [{"cmd": "true"}], "assert": {"last_exit_code": 0}}],
                }
            ],
        }
    )
    assert tf.preflight is not None
    assert len(tf.preflight) == 1
    assert tf.preflight[0].name == "backend-health"
