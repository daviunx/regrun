"""Unit tests for the ``sweep:`` schema primitive (0.9.0).

Contract:

  * ``TestFile.sweep`` parses a list of ``SweepStep``.
  * a ``SweepStep`` carrying ``capture:`` (test-level OR per-command) is
    REJECTED at validation — sweeps must be capture-independent.
  * ``eventually:`` is likewise rejected (a sweep is a delete, not a poll).
  * a clean step parses; per-step ``timeout`` defaults to 60.0; runner is
    restricted to bash / sql / httpx.
"""

import pytest
from pydantic import ValidationError

from regrun import models


def _clean_step() -> dict:
    return {
        "name": "sweep-fixtures",
        "runner": "bash",
        "commands": [{"cmd": "true"}],
        "assert": {"last_exit_code": 0},
    }


def test_sweep_step_clean_parses() -> None:
    step = models.SweepStep.model_validate(_clean_step())
    assert step.name == "sweep-fixtures"


def test_sweep_step_timeout_defaults_to_sixty() -> None:
    step = models.SweepStep.model_validate(_clean_step())
    assert step.timeout == 60.0


def test_sweep_step_rejects_capture() -> None:
    doc = _clean_step()
    doc["capture"] = {"SOME_VAR": "stdout"}
    with pytest.raises(ValidationError, match="capture-independent"):
        models.SweepStep.model_validate(doc)


def test_sweep_step_rejects_per_command_capture() -> None:
    doc = _clean_step()
    doc["commands"] = [{"cmd": "echo x", "capture": {"SOME_VAR": "stdout"}}]
    with pytest.raises(ValidationError, match="capture-independent"):
        models.SweepStep.model_validate(doc)


def test_sweep_step_rejects_eventually() -> None:
    doc = _clean_step()
    doc["eventually"] = {"max_attempts": 3, "interval": 2.0}
    with pytest.raises(ValidationError):
        models.SweepStep.model_validate(doc)


def test_sweep_step_rejects_fastmcp_runner() -> None:
    doc = _clean_step()
    doc["runner"] = "fastmcp"
    with pytest.raises(ValidationError):
        models.SweepStep.model_validate(doc)


def test_sweep_step_as_test_carries_shape() -> None:
    test = models.SweepStep.model_validate(_clean_step()).as_test()
    assert test.id == "sweep:sweep-fixtures"
    assert test.timeout == 60


def test_test_file_parses_sweep_list() -> None:
    tf = models.TestFile.model_validate(
        {
            "meta": {"product": "demo", "layer": "setup", "runner": "bash"},
            "sweep": [_clean_step()],
            "groups": [
                {
                    "id": 1,
                    "name": "G",
                    "tests": [
                        {
                            "id": "A.1",
                            "name": "t",
                            "commands": [{"cmd": "true"}],
                            "assert": {"last_exit_code": 0},
                        }
                    ],
                }
            ],
        }
    )
    assert tf.sweep is not None and len(tf.sweep) == 1
