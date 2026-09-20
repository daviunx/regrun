"""Unit tests for the assertion-quality lint rules W001, W002, W009 and W010.

Each of these catches an assert that passes without checking anything
meaningful: is_error-only, a rank-0 positional match, an exists: true
satisfied by null, and a path that can never match a normalized MCP body.
"""

from pathlib import Path

from lint_helpers import api_doc as _api_doc
from lint_helpers import mcp_doc as _mcp_doc
from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory


# --------------------------------------------------------------------------- W001


def test_w001_is_error_only_flagged(tmp_path: Path) -> None:
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
    assert "W001" in _rules(lint_directory(tmp_path))


def test_w001_clean_with_json_path(tmp_path: Path) -> None:
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
                            "assert": {"is_error": False, "json_path": {"$.id": {"exists": True}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W001" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- W002


def test_w002_positional_equals_flagged(tmp_path: Path) -> None:
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
                            "assert": {"json_path": {"$.results[0].id": {"equals": "x"}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W002" in _rules(lint_directory(tmp_path))


def test_w002_clean_any_contains(tmp_path: Path) -> None:
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
                            "assert": {"json_path": {"$.results[*].id": {"any_contains": "x"}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W002" not in _rules(lint_directory(tmp_path))


def test_w002_inline_suppression(tmp_path: Path) -> None:
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1  # lint: allow-positional\n        name: t\n        method: GET\n"
        "        assert:\n          json_path:\n            $.results[0].id:\n              equals: x\n"
    )
    assert "W002" not in _rules(lint_directory(tmp_path))


def test_w002_file_glob_suppression(tmp_path: Path) -> None:
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
                            "assert": {"json_path": {"$.results[0].id": {"equals": "x"}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W002" not in _rules(lint_directory(tmp_path, allow_positional=("01_api.yaml",)))


# --------------------------------------------------------------------------- W009


def test_w009_exists_only_flagged(tmp_path: Path) -> None:
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
                            "assert": {"status": 200, "json_path": {"$.summary": {"exists": True}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W009" in _rules(lint_directory(tmp_path))


def test_w009_clean_not_empty(tmp_path: Path) -> None:
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
                            "assert": {"json_path": {"$.summary": {"not_empty": True}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W009" not in _rules(lint_directory(tmp_path))


def test_w009_clean_exists_false(tmp_path: Path) -> None:
    # exists: false is a genuine absence assertion — not the null trap.
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
                            "assert": {"json_path": {"$.deleted": {"exists": False}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W009" not in _rules(lint_directory(tmp_path))


def test_w009_inline_suppression(tmp_path: Path) -> None:
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1  # lint: allow-exists\n        name: t\n        method: GET\n"
        "        assert:\n          json_path:\n            $.maybe_absent:\n              exists: true\n"
    )
    assert "W009" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- W010


def test_w010_data_path_assert_on_mcp_file_flagged(tmp_path: Path) -> None:
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
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.data.id": {"not_empty": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W010" in _rules(lint_directory(tmp_path))


def test_w010_data_path_capture_on_mcp_file_flagged(tmp_path: Path) -> None:
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
                            "tool": "company_manage",
                            "args": {"action": "get", "slug": "x"},
                            "capture": {"CO_ID": "$.data.id"},
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.id": {"not_empty": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W010" in _rules(lint_directory(tmp_path))


def test_w010_clean_top_level_path_on_mcp_file(tmp_path: Path) -> None:
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
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.id": {"not_empty": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W010" not in _rules(lint_directory(tmp_path))


def test_w010_data_path_fine_on_api_file(tmp_path: Path) -> None:
    # HTTP responses are not normalized — $.data.* is legitimate on api files.
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
                            "assert": {"json_path": {"$.data.items": {"not_empty": True}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W010" not in _rules(lint_directory(tmp_path))


def test_w010_database_field_not_a_false_hit(tmp_path: Path) -> None:
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
                            "tool": "health_check",
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.database": {"not_empty": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W010" not in _rules(lint_directory(tmp_path))
