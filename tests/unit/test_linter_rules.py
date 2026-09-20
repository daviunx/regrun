"""Unit tests for the suite-structure lint rules E001, E002 and W006.

E001 and E002 are file/directory shape errors; W006 is the preflight
adoption nudge. The remaining rule families live in the sibling
``test_linter_rules_*.py`` files, one per source rule module.
"""

from pathlib import Path

from lint_helpers import api_doc as _api_doc
from lint_helpers import mcp_doc as _mcp_doc
from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory, lint_exit_code


# --------------------------------------------------------------------------- E001


def test_e001_duplicate_group_id_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [{"id": "A.1", "name": "t", "assert": {"status": 200}}],
                },
                {
                    "id": 5,
                    "name": "B",
                    "tests": [{"id": "B.1", "name": "t", "assert": {"status": 200}}],
                },
            ]
        ),
    )
    findings = lint_directory(tmp_path)
    assert "E001" in _rules(findings)
    assert lint_exit_code(findings) == 1


def test_e001_clean_unique_ids(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [{"id": "A.1", "name": "t", "assert": {"status": 200}}],
                },
                {
                    "id": 6,
                    "name": "B",
                    "tests": [{"id": "B.1", "name": "t", "assert": {"status": 200}}],
                },
            ]
        ),
    )
    assert "E001" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- E002


def test_e002_mcp_file_after_cleanup_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "17_cleanup.yaml",
        _api_doc(
            [
                {
                    "id": 90,
                    "name": "Clean",
                    "tests": [{"id": "C.1", "name": "t", "assert": {"status": 200}}],
                }
            ]
        ),
    )
    _write(
        tmp_path,
        "18_mcp_extra.yaml",
        _mcp_doc(
            [
                {
                    "id": 91,
                    "name": "M",
                    "tests": [
                        {"id": "M.1", "name": "t", "tool": "x", "assert": {"is_error": False}}
                    ],
                }
            ]
        ),
    )
    findings = lint_directory(tmp_path)
    assert "E002" in _rules(findings)


def test_e002_clean_mcp_before_cleanup(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "16b_mcp.yaml",
        _mcp_doc(
            [
                {
                    "id": 91,
                    "name": "M",
                    "tests": [
                        {
                            "id": "M.1",
                            "name": "t",
                            "tool": "x",
                            "assert": {"is_error": False, "json_path": {"$.ok": {"exists": True}}},
                        }
                    ],
                }
            ]
        ),
    )
    _write(
        tmp_path,
        "17_cleanup.yaml",
        _api_doc(
            [
                {
                    "id": 90,
                    "name": "Clean",
                    "tests": [{"id": "C.1", "name": "t", "assert": {"status": 200}}],
                }
            ]
        ),
    )
    assert "E002" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- W006


def _preflight_block() -> list[dict]:
    return [
        {
            "name": "backend-health",
            "runner": "bash",
            "commands": [{"cmd": "true"}],
            "assert": {"last_exit_code": 0},
        }
    ]


def test_w006_no_preflight_in_suite_flagged(tmp_path: Path) -> None:
    # A suite directory with no `preflight:` block anywhere -> W006 (adoption nudge).
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        {"id": "A.1", "name": "t", "method": "GET", "assert": {"status": 200}}
                    ],
                }
            ]
        ),
    )
    assert "W006" in _rules(lint_directory(tmp_path))


def test_w006_clean_with_preflight(tmp_path: Path) -> None:
    # At least one file carries a `preflight:` block -> W006 does not fire.
    doc = _api_doc(
        [
            {
                "id": 5,
                "name": "A",
                "tests": [{"id": "A.1", "name": "t", "method": "GET", "assert": {"status": 200}}],
            }
        ]
    )
    doc["preflight"] = _preflight_block()
    _write(tmp_path, "01_api.yaml", doc)
    assert "W006" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- misc


def test_clean_suite_exit_zero(tmp_path: Path) -> None:
    doc = _api_doc(
        [
            {
                "id": 5,
                "name": "A",
                "tests": [
                    {
                        "id": "A.1",
                        "name": "t",
                        "method": "GET",
                        "path": "/x",
                        "assert": {"status": 200},
                    }
                ],
            }
        ]
    )
    # A fully-clean suite now carries a preflight block (else W006 fires).
    doc["preflight"] = _preflight_block()
    _write(tmp_path, "01_api.yaml", doc)
    findings = lint_directory(tmp_path)
    assert findings == []
    assert lint_exit_code(findings) == 0


def test_strict_elevates_warnings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "02_mcp.yaml",
        _mcp_doc(
            [
                {
                    "id": 10,
                    "name": "M",
                    "tests": [
                        {
                            "id": "M.1",
                            "name": "t",
                            "tool": "company_get",
                            "assert": {"is_error": False},
                        }
                    ],
                }
            ]
        ),
    )
    findings = lint_directory(tmp_path)
    assert lint_exit_code(findings, strict=False) == 0
    assert lint_exit_code(findings, strict=True) == 1


def test_empty_directory_no_findings(tmp_path: Path) -> None:
    assert lint_directory(tmp_path) == []
