"""Unit tests for the in-process FastMcpRunner.

Mocks the fastmcp ``Client`` so no live MCP server is needed. Verifies that the
in-process path produces the SAME normalized body shape the old CLI path did
(flat / envelope / string-wrapped patterns + is_error), the error/synthetic-body
path, per-auth-context client reuse, and aclose() teardown.

Plain sync tests driving the async runner via ``asyncio.run`` (no pytest-asyncio
dependency).
"""

import asyncio

from regrun import models
from regrun.engine.variables import VariableStore
from regrun.models import Assertion, AuthConfig
from regrun.runners import fastmcp_runner as fr
from regrun.runners.fastmcp_runner import FastMcpRunner


class _Block:
    """Stand-in for an MCP text content block (has a ``.text`` attribute)."""

    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    """Stand-in for a fastmcp CallToolResult."""

    def __init__(self, text: str | None, is_error: bool = False) -> None:
        self.content = [_Block(text)] if text is not None else []
        self.is_error = is_error
        self.data = None
        self.structured_content = None


class _FakeClient:
    """Records lifecycle + calls; returns the result produced by ``script``."""

    instances: list["_FakeClient"] = []
    script = staticmethod(lambda name, args: _Result('{"ok": true}'))

    def __init__(self, url, auth=None, timeout=None):
        self.url = url
        self.auth = auth
        self.timeout = timeout
        self.entered = 0
        self.exited = 0
        self.calls: list[tuple] = []
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc):
        self.exited += 1
        return False

    async def call_tool(self, name, arguments=None, *, raise_on_error=True):
        self.calls.append((name, arguments, raise_on_error))
        return _FakeClient.script(name, arguments)


def _install(monkeypatch, script, auth_configs=None) -> FastMcpRunner:
    _FakeClient.instances = []
    _FakeClient.script = staticmethod(script)
    monkeypatch.setattr(fr, "Client", _FakeClient)
    return FastMcpRunner(
        "http://mcp.localhost",
        auth_configs or {},
        timeout=5,
        default_auth=None,
    )


def _test(tool="tasks_create", args=None, auth=None) -> models.Test:
    return models.Test(id="T1", name="t", tool=tool, args=args or {}, auth=auth, assert_=Assertion())


def test_flat_dict_body(monkeypatch):
    runner = _install(monkeypatch, lambda n, a: _Result('{"task": {"id": "abc"}}'))
    resp = asyncio.run(runner.execute(_test(), VariableStore()))
    assert resp.body["task"]["id"] == "abc"
    assert resp.body["is_error"] is False
    assert resp.error is None


def test_envelope_unwrapped(monkeypatch):
    runner = _install(monkeypatch, lambda n, a: _Result('{"success": true, "data": {"task": {"id": "e"}}}'))
    resp = asyncio.run(runner.execute(_test(), VariableStore()))
    # envelope {success,data} must unwrap to data so $.task.id resolves
    assert resp.body["task"]["id"] == "e"
    assert "success" not in resp.body


def test_string_wrapped_parsed(monkeypatch):
    runner = _install(monkeypatch, lambda n, a: _Result('{"result": "{\\"task\\": {\\"id\\": \\"s\\"}}"}'))
    resp = asyncio.run(runner.execute(_test(), VariableStore()))
    assert resp.body["task"]["id"] == "s"


def test_is_error_flag_preserved(monkeypatch):
    runner = _install(monkeypatch, lambda n, a: _Result('{"detail": "nope"}', is_error=True))
    resp = asyncio.run(runner.execute(_test(), VariableStore()))
    assert resp.body["is_error"] is True
    assert resp.body["detail"] == "nope"


def test_client_exception_becomes_synthetic_error_body(monkeypatch):
    def boom(n, a):
        raise RuntimeError("connection refused")

    runner = _install(monkeypatch, boom)
    resp = asyncio.run(runner.execute(_test(), VariableStore()))
    assert resp.body["is_error"] is True
    assert "connection refused" in resp.body["error"]


def test_missing_tool_returns_error():
    runner = FastMcpRunner("http://mcp.localhost", {}, timeout=5)
    bad = models.Test(id="T1", name="t", tool=None, assert_=Assertion())
    resp = asyncio.run(runner.execute(bad, VariableStore()))
    assert resp.error == "MCP test missing 'tool' field"


def test_one_client_per_auth_context_and_reuse(monkeypatch):
    auth = {
        "prod": AuthConfig(type="api_key", token="PRODKEY"),
        "fresh": AuthConfig(type="api_key", token="FRESHKEY"),
    }
    runner = _install(monkeypatch, lambda n, a: _Result('{"ok": true}'), auth_configs=auth)
    store = VariableStore()
    asyncio.run(runner.execute(_test(auth="prod"), store))
    asyncio.run(runner.execute(_test(auth="prod"), store))  # reuse prod client
    asyncio.run(runner.execute(_test(auth="fresh"), store))  # new client
    # 2 distinct clients (prod, fresh); prod reused (not reopened)
    assert len(_FakeClient.instances) == 2
    tokens = sorted(c.auth for c in _FakeClient.instances)
    assert tokens == ["FRESHKEY", "PRODKEY"]
    assert all(c.entered == 1 for c in _FakeClient.instances)


def test_aclose_closes_all_clients(monkeypatch):
    runner = _install(monkeypatch, lambda n, a: _Result('{"ok": true}'))
    asyncio.run(runner.execute(_test(), VariableStore()))
    assert len(_FakeClient.instances) == 1
    asyncio.run(runner.aclose())
    assert _FakeClient.instances[0].exited == 1
    assert runner._clients == {}
