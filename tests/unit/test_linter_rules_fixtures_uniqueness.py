"""Unit tests for the fixture-uniqueness lint rule W004, and its exemptions.

W004 fires on a create-shaped test whose body carries no run-scoped value,
so its fixtures collide across runs. Most of this file is the exemption
surface: negative tests, read and mutation verbs, searches, auth and
bodyless POSTs, and the two inline suppressions.
"""

from pathlib import Path

from lint_helpers import api_doc as _api_doc
from lint_helpers import create_test as _create_test
from lint_helpers import mcp_doc as _mcp_doc
from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory


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


def test_w004_skips_mcp_read_get_with_slug(tmp_path: Path) -> None:
    # An MCP READ (`action: get`) that happens to carry a `slug` filter arg is
    # not a create — W004 must not fire (real-world NTX.2 false positive).
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
                            "name": "get one",
                            "tool": "company_get",
                            "args": {"action": "get", "slug": "some-fixed-slug"},
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.slug": {"exists": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_mcp_read_list_with_name(tmp_path: Path) -> None:
    # An MCP READ (`action: list`) with a `name` filter arg is not a create —
    # W004 must not fire (real-world PV.3 false positive).
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
                            "name": "list filtered",
                            "tool": "company_list",
                            "args": {"action": "list", "name": "filter-value"},
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.items": {"exists": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_mcp_negative_is_error_test(tmp_path: Path) -> None:
    # An MCP negative test (asserts `is_error: true`) carrying a create-ish key
    # is not exercising the create path — W004 must not fire. HTTP-status 4xx
    # negatives are already skipped; MCP is_error negatives are the same case.
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
                            "name": "rejects bad input",
                            "tool": "company_manage",
                            "args": {"name": "no-run-id-here"},
                            "assert": {"is_error": True},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_still_fires_on_mcp_create_action_guard(tmp_path: Path) -> None:
    # GUARD: a genuine MCP create (`action: create`) with no {{RUN_ID}} MUST
    # still trip W004 — proves the read/negative carve-outs don't neuter it.
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
                            "name": "create one",
                            "tool": "company_manage",
                            "args": {"action": "create", "name": "no-run-id-here"},
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.id": {"exists": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" in _rules(lint_directory(tmp_path))


def test_w004_skips_mcp_update_restore_with_slug(tmp_path: Path) -> None:
    # An MCP in-place mutation (`action: update`) restoring a seeded row by its
    # fixed slug is not a create — W004 must not fire (real-world PV.5/NTX.15).
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
                            "name": "restore seeded row",
                            "tool": "providers_manage",
                            "args": {
                                "action": "update",
                                "slug": "anthropic",
                                "display_name": "Anthropic",
                            },
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.slug": {"equals": "anthropic"}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_mcp_list_missing_with_locale(tmp_path: Path) -> None:
    # `action: list_missing` is a read/diagnostic, not a create — W004 must not
    # fire (real-world MP.5/MP.6/NTX.18/NTX.20).
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
                            "name": "list missing translations",
                            "tool": "translations_manage",
                            "args": {
                                "action": "list_missing",
                                "entity_type": "post",
                                "locale": "es",
                            },
                            "assert": {
                                "is_error": False,
                                "json_path": {"$.items": {"exists": True}},
                            },
                        }
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


# --- W004 HTTP-side exemptions (0.8.2) ------------------------------------


def _post_test(tid: str, path: str, body: dict | None) -> dict:
    t: dict = {"id": tid, "name": tid, "method": "POST", "path": path, "assert": {"status": 200}}
    if body is not None:
        t["body"] = body
    return t


def test_w004_skips_search_post(tmp_path: Path) -> None:
    # HTTP POST to a `search` endpoint is a query, not a create — no per-run
    # uniqueness applies; flagging one is a false positive.
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        _post_test(
                            "A.1",
                            "/api/v1/knowledge/documents/search",
                            {"query": "documentation", "mode": "hybrid", "limit": 10},
                        )
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_underscore_search_and_query_post(tmp_path: Path) -> None:
    # `_search` (OpenSearch convention) and a `query` segment are both reads.
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        _post_test("A.1", "/entities/_search", {"q": "x"}),
                        _post_test("A.2", "/api/v1/query", {"q": "y"}),
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_login_post(tmp_path: Path) -> None:
    # HTTP POST to an auth endpoint uses fixed credentials, not a per-run row
    # (a login POST is a false positive).
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        _post_test(
                            "A.1",
                            "/api/v1/login/access-token",
                            {"email": "known@example.com", "password": "fixed-pass"},
                        )
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_oauth_token_post(tmp_path: Path) -> None:
    # A `token` segment (e.g. /oauth/token) is an auth exchange, not a create.
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [_post_test("A.1", "/oauth/token", {"grant_type": "password"})],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_bulk_delete_post(tmp_path: Path) -> None:
    # HTTP POST to a bulk/targeted delete endpoint has delete semantics, not
    # create, so flagging one is a false positive.
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        _post_test(
                            "A.1",
                            "/api/v1/entities/bulk-delete",
                            {"entity_ids": ["a", "b", "c"]},
                        )
                    ],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_skips_bodyless_post(tmp_path: Path) -> None:
    # A bodyless HTTP POST (action toggle) carries nothing to hold a RUN_ID
    # (set-default / clear-default toggles are false positives).
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [_post_test("A.1", "/api/v1/chat-themes/xyz/set-default", None)],
                }
            ]
        ),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_allow_nocreate_suppression(tmp_path: Path) -> None:
    # Inline `# lint: allow-nocreate` escape hatch suppresses W004 on an
    # irreducible create (server-derived name, key-hint on an update tool).
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1  # lint: allow-nocreate — server derives name\n"
        "        name: t\n        method: POST\n        path: /api/v1/knowledge\n"
        "        body:\n          data_type: auto\n        assert:\n          status: 201\n"
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_still_fires_on_genuine_create_post_guard(tmp_path: Path) -> None:
    # GUARD: a genuine HTTP POST create (non-exempt path, body with a name,
    # no {{RUN_ID}}) MUST still trip W004 — the carve-outs don't neuter it.
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [_post_test("A.1", "/api/v1/tools", {"name": "my-tool"})],
                }
            ]
        ),
    )
    assert "W004" in _rules(lint_directory(tmp_path))


def test_w004_search_match_is_segment_exact_not_substring_guard(tmp_path: Path) -> None:
    # GUARD: `search` is matched as a whole path SEGMENT, not a substring — a
    # POST creating a `searchable-widgets` resource still fires (no substring soup).
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [_post_test("A.1", "/api/v1/searchable-widgets", {"name": "w"})],
                }
            ]
        ),
    )
    assert "W004" in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- W004 (0.9.0 tightening)


def test_w004_inline_timestamp_no_longer_satisfies(tmp_path: Path) -> None:
    # {{timestamp}} is recomputed per render: the value exists nowhere in the
    # store, so the fixture is uncapturable AND unsweepable (RGRN-13).
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc(
            [{"id": 5, "name": "A", "tests": [_create_test({"slug": "regr-co-{{timestamp}}"})]}]
        ),
    )
    assert "W004" in _rules(lint_directory(tmp_path))


def test_w004_satisfied_by_declared_derived_variable(tmp_path: Path) -> None:
    doc = _api_doc([{"id": 5, "name": "A", "tests": [_create_test({"slug": "{{CO_SLUG}}"})]}])
    doc["variables"] = {"CO_SLUG": "regr-co-{{RUN_ID}}"}
    _write(tmp_path, "01_api.yaml", doc)
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_derived_variable_declared_in_setup_file(tmp_path: Path) -> None:
    # Cross-file: RUN_ID-derived variables declared in 00_setup cover creates
    # in later files (VariableStore propagates them at run time).
    setup = {
        "meta": {"product": "demo", "layer": "setup", "runner": "bash"},
        "variables": {"RUN_ID": "{{timestamp}}", "CO_SLUG": "regr-co-{{RUN_ID}}"},
        "groups": [
            {
                "id": 1,
                "name": "S",
                "tests": [
                    {
                        "id": "S.1",
                        "name": "t",
                        "commands": [{"cmd": "true"}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            }
        ],
    }
    _write(tmp_path, "00_setup.yaml", setup)
    _write(
        tmp_path,
        "01_api.yaml",
        _api_doc([{"id": 5, "name": "A", "tests": [_create_test({"slug": "{{CO_SLUG}}"})]}]),
    )
    assert "W004" not in _rules(lint_directory(tmp_path))


def test_w004_non_derived_variable_does_not_satisfy(tmp_path: Path) -> None:
    doc = _api_doc([{"id": 5, "name": "A", "tests": [_create_test({"slug": "{{CO_SLUG}}"})]}])
    doc["variables"] = {"CO_SLUG": "fixed-slug-every-run"}
    _write(tmp_path, "01_api.yaml", doc)
    assert "W004" in _rules(lint_directory(tmp_path))
