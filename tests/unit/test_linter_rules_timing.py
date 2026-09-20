"""Unit tests for the poll-budget lint rule W003 (under-budgeted eventually)."""

from pathlib import Path

from lint_helpers import api_doc as _api_doc
from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory


# --------------------------------------------------------------------------- W003


def test_w003_underbudget_eventually_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        {
                            "id": "A.1",
                            "name": "t",
                            "method": "GET",
                            "assert": {"status": 200},
                            "eventually": {"max_attempts": 20, "interval": 3.0},
                        }  # 57s < 75s
                    ],
                }
            ]
        ),
    )
    assert "W003" in _rules(lint_directory(tmp_path))


def test_w003_clean_when_over_floor(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        {
                            "id": "A.1",
                            "name": "t",
                            "method": "GET",
                            "assert": {"status": 200},
                            "eventually": {"max_attempts": 26, "interval": 3.0},
                        }  # 75s
                    ],
                }
            ]
        ),
    )
    assert "W003" not in _rules(lint_directory(tmp_path))


def test_w003_custom_floor(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        {
                            "id": "A.1",
                            "name": "t",
                            "method": "GET",
                            "assert": {"status": 200},
                            "eventually": {"max_attempts": 20, "interval": 3.0},
                        }  # 57s
                    ],
                }
            ]
        ),
    )
    assert "W003" not in _rules(lint_directory(tmp_path, budget_floor=30.0))
