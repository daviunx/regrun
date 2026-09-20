"""Unit tests for ``extra="forbid"`` across EVERY schema model.

``TestMeta``, ``Assertion`` and ``Test`` forbade extras already. The remaining
nine models did not, so pydantic's default ``extra="ignore"`` silently dropped a
misplaced or misspelt key: a top-level ``endpoint:`` (it belongs under ``meta:``)
never reached the runner, a group-level ``cleanupp:`` left the sweep unflagged,
a mistyped poll key left the poll on its defaults. The run stayed green and
``regrun lint`` stayed clean, which is the same silent false-pass family the
``Assertion`` tightening closed. Every model now rejects unknown keys at load.

Each rejection test asserts the offending KEY NAME appears in the error, since
that is what makes the failure actionable: pydantic reports an extra key at the
location that names it.
"""

import pytest
from pydantic import ValidationError

from regrun import models

# ---------------------------------------------------------------------------
# Building blocks reused by the rejection tests.
# ---------------------------------------------------------------------------

MINIMAL_ASSERT = {"status": 200}
MINIMAL_TEST = {
    "id": "A.1",
    "name": "read an item",
    "method": "GET",
    "path": "/api/v1/items",
    "assert": MINIMAL_ASSERT,
}
MINIMAL_GROUP = {"id": 1, "name": "API surface", "tests": [MINIMAL_TEST]}
MINIMAL_META = {"product": "demo", "layer": "api", "runner": "httpx"}


def _file_doc(**overrides) -> dict:
    doc: dict = {"meta": dict(MINIMAL_META), "groups": [dict(MINIMAL_GROUP)]}
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# One rejection test per model.
# ---------------------------------------------------------------------------


def test_test_file_rejects_misplaced_endpoint() -> None:
    """``endpoint:`` belongs under ``meta:``; at the top level it reached nothing."""
    with pytest.raises(ValidationError, match="endpoint"):
        models.TestFile.model_validate(_file_doc(endpoint="http://api.example.com"))


def test_group_rejects_typoed_cleanup_flag() -> None:
    with pytest.raises(ValidationError, match="cleanupp"):
        models.Group.model_validate({**MINIMAL_GROUP, "cleanupp": True})


def test_preflight_check_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="retries"):
        models.PreflightCheck.model_validate(
            {"name": "api-reachable", "assert": MINIMAL_ASSERT, "retries": 3}
        )


def test_sweep_step_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="patern"):
        models.SweepStep.model_validate(
            {"name": "sweep items", "assert": {"last_exit_code": 0}, "patern": "demo-%"}
        )


def test_auth_config_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="header"):
        models.AuthConfig.model_validate(
            {"type": "bearer", "token": "{{APP_JWT}}", "header": "X-Org-Slug"}
        )


def test_bash_command_rejects_per_command_assert() -> None:
    """A per-command ``assert:`` is not a thing: only the test-level block runs."""
    with pytest.raises(ValidationError, match="assert"):
        models.BashCommand.model_validate({"cmd": "true", "assert": {"last_exit_code": 0}})


def test_eventually_config_rejects_typoed_key() -> None:
    with pytest.raises(ValidationError, match="max_attemps"):
        models.EventuallyConfig.model_validate({"max_attemps": 5})


def test_websocket_config_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="text_feild"):
        models.WebSocketConfig.model_validate({"text_feild": "data.delta"})


def test_sql_connection_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError, match="dsn"):
        models.SqlConnection.model_validate(
            {
                "docker_container": "demo-db-1",
                "docker_user": "postgres",
                "database": "demo_test",
                "fallback_dsn": "postgres://postgres@localhost:5432/demo_test",
                "dsn": "postgres://postgres@localhost:5432/demo_test",
            }
        )


# ---------------------------------------------------------------------------
# Every documented key still loads.
# ---------------------------------------------------------------------------


def _fully_populated_doc() -> dict:
    """A suite file carrying every key the schema reference documents.

    Hand-built from the schema reference so it is an independent statement of
    the contract rather than a restatement of the models, and the tightening
    cannot pass by narrowing the schema.
    """
    return {
        "meta": {
            "product": "demo",
            "layer": "api",
            "runner": "httpx",
            "endpoint": "http://api.example.com",
            "mcp_endpoint": "http://mcp.example.com",
            "default_auth": "prod",
            "env_file": ".env.test",
            "strict_vars": True,
            "requires": ["01_provider"],
            "serial": False,
            "budget_seconds": 60.0,
            "health_path": "/health",
            "mcp_health_path": "/mcp/health",
            "sql_connection": {
                "docker_container": "demo-db-1",
                "docker_user": "postgres",
                "database": "demo_test",
                "fallback_dsn": "postgres://postgres@localhost:5432/demo_test",
            },
        },
        "variables": {
            "RUN_ID": "{{timestamp}}",
            "TODAY": "{{date}}",
            "REQUEST_ID": "{{uuid}}",
            "BASE_EMAIL": "admin@example.com",
        },
        "auth": {
            "prod": {"type": "bearer", "token": "{{APP_JWT}}", "org_header": "demo"},
            "service_key": {"type": "api_key", "token": "{{SERVICE_API_KEY}}"},
        },
        "preflight": [
            {
                "name": "api-reachable",
                "runner": "httpx",
                "method": "GET",
                "path": "/health",
                "auth": "none",
                "org_header": False,
                "query_params": {"verbose": "1"},
                "timeout": 10.0,
                "assert": {"status": 200},
            },
            {
                "name": "db-reachable",
                "runner": "sql",
                "sql": "SELECT 1;",
                "assert": {"last_exit_code": 0},
            },
            {
                "name": "tool-reachable",
                "runner": "fastmcp",
                "tool": "health_check",
                "args": {"verbose": True},
                "assert": {"is_error": False},
            },
            {
                "name": "shell-reachable",
                "runner": "bash",
                "commands": [{"cmd": "true"}],
                "body": {"unused": "accepted on any runner"},
                "assert": {"last_exit_code": 0},
            },
        ],
        "sweep": [
            {
                "name": "sweep items",
                "runner": "sql",
                "sql": "DELETE FROM items WHERE slug LIKE 'demo-%';",
                "timeout": 60.0,
                "assert": {"last_exit_code": 0},
            },
            {
                "name": "sweep via api",
                "runner": "httpx",
                "method": "POST",
                "path": "/api/v1/items/purge",
                "auth": "prod",
                "org_header": True,
                "body": {"prefix": "demo-"},
                "query_params": {"dry_run": "false"},
                "assert": {"status": 200},
            },
            {
                "name": "sweep via shell",
                "runner": "bash",
                "commands": [{"cmd": "true"}],
                "assert": {"last_exit_code": 0},
            },
        ],
        "groups": [
            {
                "id": 1,
                "name": "API surface",
                "priority": "high",
                "context": "both",
                "cleanup": False,
                "tests": [
                    {
                        "id": "A.1",
                        "name": "Create item",
                        "method": "POST",
                        "path": "/api/v1/items",
                        "auth": "prod",
                        "org_header": True,
                        "body": {"name": "Widget {{RUN_ID}}", "price": 9.99},
                        "query_params": {"expand": "metadata"},
                        "runner": "httpx",
                        "timeout": 30,
                        "assert": {
                            "status": [200, 201],
                            "contains": "Widget",
                            "json_path": {
                                "$.id": {"exists": True},
                                "$.name": {"starts_with": "Widget"},
                            },
                        },
                        "capture": {"ITEM_ID": "$.id"},
                        "eventually": {
                            "max_attempts": 10,
                            "interval": 2.0,
                            "backoff": 1.0,
                            "initial_delay": 0.0,
                        },
                    },
                    {
                        "id": "M.1",
                        "name": "List items via MCP",
                        "runner": "fastmcp",
                        "tool": "items_list",
                        "args": {"status": "active", "limit": 10},
                        "auth": "service_key",
                        "assert": {
                            "is_error": False,
                            "json_path": {"$": {"not_empty": True}},
                        },
                        "capture": {"FIRST_ITEM_ID": "$[0].id"},
                    },
                    {
                        "id": "S.1",
                        "name": "Seed a row",
                        "runner": "bash",
                        "commands": [{"cmd": "echo INSERT", "capture": {"RAW": "stdout"}}],
                        "assert": {"last_exit_code": 0, "contains": "INSERT"},
                    },
                    {
                        "id": "Q.1",
                        "name": "Count rows",
                        "runner": "sql",
                        "sql": "SELECT count(*) FROM items;",
                        "assert": {"last_exit_code": 0},
                    },
                    {
                        "id": "C.1",
                        "name": "Chat session produces a response",
                        "runner": "websocket",
                        "url": "ws://api.example.com/api/v1/ws/chat",
                        "send": {"message": "hello"},
                        "wait_for": "agent_completed",
                        "timeout": 60000,
                        "ws_config": {
                            "event_type_field": "event_type",
                            "event_type_fallback": "type",
                            "text_event": "text_delta",
                            "text_field": "data.delta",
                            "tool_call_event": "tool_call",
                            "tool_name_field": "data.tool_name",
                            "error_event": "error",
                            "error_field": "data.content",
                        },
                        "assert": {
                            "has_error": False,
                            "json_path": {"$.event_count": {"gt": 1}},
                        },
                        "capture": {"CHAT_RESPONSE": "$.response_text"},
                    },
                ],
            },
            {
                "id": 2,
                "name": "Environment sweep",
                "priority": "low",
                "context": "prod",
                "cleanup": True,
                "tests": [
                    {
                        "id": "Z.1",
                        "name": "Delete fixtures",
                        "runner": "bash",
                        "commands": [{"cmd": "true"}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            },
        ],
    }


def test_every_documented_key_still_loads() -> None:
    test_file = models.TestFile.model_validate(_fully_populated_doc())

    assert test_file.meta.sql_connection is not None
    assert test_file.meta.health_path == "/health"
    assert set(test_file.auth) == {"prod", "service_key"}
    assert test_file.preflight is not None and len(test_file.preflight) == 4
    assert test_file.sweep is not None and len(test_file.sweep) == 3
    assert [g.id for g in test_file.groups] == [1, 2]
    assert test_file.groups[1].cleanup is True

    first = test_file.groups[0].tests[0]
    assert first.eventually is not None and first.eventually.max_attempts == 10
    ws = test_file.groups[0].tests[4]
    assert ws.ws_config is not None and ws.ws_config.text_field == "data.delta"
    bash = test_file.groups[0].tests[2]
    assert bash.commands is not None and bash.commands[0].capture == {"RAW": "stdout"}
