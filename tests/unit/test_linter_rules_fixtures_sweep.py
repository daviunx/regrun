"""Unit tests for the sweep-coverage lint rules W005, W007 and W011.

A sweep must be capture-independent (W005), must sort before the first
create (W007), and must cover every fixture family the suite creates (W011).
"""

from pathlib import Path

from lint_helpers import api_doc as _api_doc
from lint_helpers import create_test as _create_test
from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory


# --------------------------------------------------------------------------- W005


def test_w005_capture_dependent_cleanup_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "02_mcp.yaml",
        {
            "meta": {"product": "demo", "layer": "mcp", "runner": "bash"},
            "groups": [
                {
                    "id": 10,
                    "name": "Seed",
                    "tests": [
                        {
                            "id": "S.1",
                            "name": "seed",
                            "tool": "post_create",
                            "args": {"name": "x"},
                            "capture": {"POST_ID": "$.id"},
                            "assert": {"is_error": False, "json_path": {"$.id": {"exists": True}}},
                        }
                    ],
                },
                {
                    "id": 11,
                    "name": "Cleanup",
                    "cleanup": True,
                    "tests": [
                        {
                            "id": "CL.1",
                            "name": "del",
                            "commands": [{"cmd": "psql -c \"delete where id='{{POST_ID}}'\""}],
                            "assert": {"last_exit_code": 0},
                        }
                    ],
                },
            ],
        },
    )
    assert "W005" in _rules(lint_directory(tmp_path))


def test_w005_clean_pattern_sweep(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "02_mcp.yaml",
        {
            "meta": {"product": "demo", "layer": "mcp", "runner": "bash"},
            "groups": [
                {
                    "id": 11,
                    "name": "Cleanup",
                    "cleanup": True,
                    "tests": [
                        {
                            "id": "CL.1",
                            "name": "sweep",
                            "commands": [{"cmd": "psql -c \"delete where slug like 'regr-%'\""}],
                            "assert": {"last_exit_code": 0},
                        }
                    ],
                },
            ],
        },
    )
    assert "W005" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- W007


def _cleanup_group(gid: int) -> dict:
    return {
        "id": gid,
        "name": "Sweep",
        "cleanup": True,
        "tests": [
            {
                "id": f"CL.{gid}",
                "name": "sweep families",
                "commands": [{"cmd": "echo 'DELETE regr-co-%'"}],
                "assert": {"last_exit_code": 0},
            }
        ],
    }


def test_w007_create_without_any_cleanup_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc([{"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{RUN_ID}}"})]}]),
    )
    assert "W007" in _rules(lint_directory(tmp_path))


def test_w007_cleanup_after_first_create_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{RUN_ID}}"})]},
                _cleanup_group(6),
            ]
        ),
    )
    assert "W007" in _rules(lint_directory(tmp_path))


def test_w007_clean_cleanup_before_first_create(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                _cleanup_group(4),
                {"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{RUN_ID}}"})]},
            ]
        ),
    )
    assert "W007" not in _rules(lint_directory(tmp_path))


def test_w007_clean_with_sweep_block(tmp_path: Path) -> None:
    doc = _api_doc(
        [{"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{RUN_ID}}"})]}]
    )
    doc["sweep"] = [
        {
            "name": "sweep-co",
            "runner": "bash",
            "commands": [{"cmd": "echo 'DELETE regr-co-%'"}],
            "assert": {"last_exit_code": 0},
        }
    ]
    _write(tmp_path, "01_api.yaml", doc)
    assert "W007" not in _rules(lint_directory(tmp_path))


def test_w007_clean_when_nothing_creates(tmp_path: Path) -> None:
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
                            "path": "/x",
                            "assert": {"status": 200},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W007" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- W011


def test_w011_orphan_family_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                _cleanup_group(4),  # sweeps regr-co-% only
                {"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{RUN_ID}}"})]},
                {
                    "id": 6,
                    "name": "B",
                    "tests": [
                        {
                            "id": "B.1",
                            "name": "orphan create",
                            "method": "POST",
                            "path": "/tags",
                            "body": {"slug": "regr-tag-{{RUN_ID}}"},
                            "assert": {"status": 201},
                        }
                    ],
                },
            ]
        ),
    )
    findings = lint_directory(tmp_path)
    w011 = [f for f in findings if f.rule == "W011"]
    assert len(w011) == 1
    assert "regr-tag-" in w011[0].message
    assert "regr-co-" not in w011[0].message


def test_w011_clean_when_sweep_covers_family(tmp_path: Path) -> None:
    doc = _api_doc(
        [{"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{RUN_ID}}"})]}]
    )
    doc["sweep"] = [
        {
            "name": "sweep-co",
            "runner": "bash",
            "commands": [{"cmd": "echo 'DELETE FROM companies WHERE slug LIKE ''regr-co-%'''"}],
            "assert": {"last_exit_code": 0},
        }
    ]
    _write(tmp_path, "01_api.yaml", doc)
    assert "W011" not in _rules(lint_directory(tmp_path))
