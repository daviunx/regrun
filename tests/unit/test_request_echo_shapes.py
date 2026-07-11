"""Unit tests (TDD-RED) for the per-runner RequestEcho shape.

``RequestEcho`` echoes what each runner actually sent (after template
rendering). The heavier fastmcp/websocket runners are NOT driven over the
network here -- their echo shape is pinned at the model level (analysis.md §6
item 7). The httpx capture-time header redaction is verified against the real
``HttpxRunner._build_headers`` output (no network), and the bash echo carries
the rendered command list.

New symbols imported inside each test so their absence is a per-test FAILURE.
"""

from regrun.engine.variables import VariableStore
from regrun.models import AuthConfig, Test


def _httpx_test() -> Test:
    return Test.model_validate(
        {
            "id": "H.1",
            "name": "whoami",
            "method": "GET",
            "path": "/me",
            "auth": "user",
            "assert": {"status": 200},
        }
    )


def test_httpx_request_echo_redacts_captured_auth_header() -> None:
    """The auth header the httpx runner really builds is redacted at capture."""
    from regrun.engine.diagnostics import redact_headers
    from regrun.runners.httpx_runner import HttpxRunner

    runner = HttpxRunner(
        base_url="http://demo.localhost",
        auth_configs={"user": AuthConfig(type="bearer", token="SECRET-TOKEN")},
        default_auth="user",
    )
    headers = runner._build_headers(_httpx_test(), VariableStore())

    # Sanity: the runner really does place the bearer token in the header.
    assert headers["Authorization"] == "Bearer SECRET-TOKEN"

    redacted = redact_headers(headers)
    assert redacted["Authorization"] == "[REDACTED]"
    assert "SECRET-TOKEN" not in str(redacted)
    assert redacted["Content-Type"] == "application/json"


def test_bash_request_echo_carries_command_list() -> None:
    from regrun.runners.base import RequestEcho

    echo = RequestEcho(
        runner="bash",
        commands=["echo hello", "ls -la /tmp"],
    )
    assert echo.runner == "bash"
    assert echo.commands == ["echo hello", "ls -la /tmp"]


def test_fastmcp_request_echo_carries_tool_and_args() -> None:
    from regrun.runners.base import RequestEcho

    echo = RequestEcho(
        runner="fastmcp",
        tool="list_tools",
        args={"limit": 5},
    )
    assert echo.runner == "fastmcp"
    assert echo.tool == "list_tools"
    assert echo.args == {"limit": 5}


def test_websocket_request_echo_carries_url_send_and_wait_for() -> None:
    from regrun.runners.base import RequestEcho

    echo = RequestEcho(
        runner="websocket",
        url="ws://demo.localhost/chat",
        send={"message": "hi"},
        wait_for="text_delta",
    )
    assert echo.runner == "websocket"
    assert echo.url == "ws://demo.localhost/chat"
    assert echo.send == {"message": "hi"}
    assert echo.wait_for == "text_delta"


def test_runner_response_gains_request_echo_field() -> None:
    """RunnerResponse carries the RequestEcho so the executor can build
    diagnostics from the response already in hand."""
    from regrun.runners.base import RequestEcho, RunnerResponse

    echo = RequestEcho(runner="httpx", method="GET", url="http://x/y")
    resp = RunnerResponse(status_code=200, body={"ok": True}, request_echo=echo)

    assert resp.request_echo is echo
