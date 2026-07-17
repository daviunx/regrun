"""Unit tests for every regrun lint rule (violating + clean fixture)."""

from pathlib import Path

import yaml

from regrun.engine.linter import lint_directory, lint_exit_code


def _write(directory: Path, name: str, doc: dict) -> None:
    (directory / name).write_text(yaml.safe_dump(doc, sort_keys=False))


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


def _api_doc(groups: list[dict]) -> dict:
    return {"meta": {"product": "demo", "layer": "api", "runner": "httpx"}, "groups": groups}


def _mcp_doc(groups: list[dict]) -> dict:
    return {"meta": {"product": "demo", "layer": "mcp", "runner": "fastmcp"}, "groups": groups}


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


# --------------------------------------------------------------------------- E003


def test_e003_null_auth_flagged(tmp_path: Path) -> None:
    # auth: with an explicit null value (the `auth: none` trap).
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1\n        name: t\n        auth:\n        assert:\n          status: 200\n"
    )
    findings = lint_directory(tmp_path)
    assert "E003" in _rules(findings)


def test_e003_clean_string_none(tmp_path: Path) -> None:
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1\n        name: t\n        auth: none\n        assert:\n          status: 200\n"
    )
    assert "E003" not in _rules(lint_directory(tmp_path))


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


# --------------------------------------------------------------------------- W004


def test_w004_post_without_run_id_flagged(tmp_path: Path) -> None:
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
                            "method": "POST",
                            "path": "/companies",
                            "body": {"slug": "acme-corp"},
                            "assert": {"status": 201},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" in _rules(lint_directory(tmp_path))


def test_w004_clean_with_run_id(tmp_path: Path) -> None:
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
                            "method": "POST",
                            "path": "/companies",
                            "body": {"slug": "regr-co-{{RUN_ID}}"},
                            "assert": {"status": 201},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_negative_4xx_test(tmp_path: Path) -> None:
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
                            "method": "POST",
                            "path": "/companies",
                            "body": {"slug": "bad"},
                            "assert": {"status": 400},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_mcp_create_args(tmp_path: Path) -> None:
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
                            "args": {"name": "Acme"},
                            "assert": {"is_error": False, "json_path": {"$.id": {"exists": True}}},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" in _rules(lint_directory(tmp_path))


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
                    "tests": [{"id": "A.1", "name": "t", "method": "GET", "assert": {"status": 200}}],
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
