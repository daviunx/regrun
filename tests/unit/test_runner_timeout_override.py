"""Unit tests for the per-test ``timeout`` override across runners.

Regression guard for the bug where BashRunner / HttpxRunner / FastMcpRunner
ignored the per-test ``timeout:`` YAML field and always used the
construction-time default. Long-running polls (worker re-index waits in
``11_search_e2e.yaml``) declare a wider budget in YAML; before the fix every
command was capped at the default and killed mid-poll.

Semantics under test: ``test.timeout`` is in SECONDS (matches config.py's
``timeout``/``mcp_timeout`` docs and the values regression YAML uses, e.g.
75/120). It is NOT milliseconds. A ``None`` per-test timeout falls back to the
runner default.

Plain sync tests driving the async runners via ``asyncio.run`` (no
pytest-asyncio dependency), mirroring test_fastmcp_runner.py.
"""

import asyncio

from regrun import models
from regrun.engine.variables import VariableStore
from regrun.models import Assertion, BashCommand
from regrun.runners import fastmcp_runner as fr
from regrun.runners.bash_runner import BashRunner
from regrun.runners.fastmcp_runner import FastMcpRunner


# ---------------------------------------------------------------------------
# BashRunner — real subprocess, deterministic sleeps
# ---------------------------------------------------------------------------


def _bash_test(cmd: str, timeout: int | None = None) -> models.Test:
    return models.Test(
        id="B1",
        name="bash",
        commands=[BashCommand(cmd=cmd)],
        timeout=timeout,
        assert_=Assertion(),
    )


def test_bash_per_test_timeout_extends_default():
    """A command longer than the default but within the per-test override
    completes with exit 0 — proving the override is honored, not the default."""
    # Default 1s would kill a 2s sleep; per-test timeout=5 lets it finish.
    runner = BashRunner(cwd=".", timeout=1)
    test = _bash_test("sleep 2 && echo done", timeout=5)
    resp = asyncio.run(runner.execute(test, VariableStore()))
    assert resp.status_code == 0, resp.error
    assert resp.body == "done"


def test_bash_per_test_timeout_still_enforced():
    """A command exceeding the per-test override is killed (exit 124) — the
    override widens but does not disable the timeout."""
    runner = BashRunner(cwd=".", timeout=30)
    test = _bash_test("sleep 3 && echo done", timeout=1)
    resp = asyncio.run(runner.execute(test, VariableStore()))
    assert resp.status_code == 124
    assert "timed out after 1s" in (resp.error or "")


def test_bash_falls_back_to_default_when_no_override():
    """With no per-test timeout, the construction-time default caps the command."""
    runner = BashRunner(cwd=".", timeout=1)
    test = _bash_test("sleep 3 && echo done")  # timeout=None
    resp = asyncio.run(runner.execute(test, VariableStore()))
    assert resp.status_code == 124
    assert "timed out after 1s" in (resp.error or "")


def test_bash_timeout_is_seconds_not_milliseconds():
    """A per-test timeout of 2 must mean 2 SECONDS. If it were misread as ms
    (0.002s), a trivially fast command would spuriously time out."""
    runner = BashRunner(cwd=".", timeout=30)
    test = _bash_test("echo quick", timeout=2)
    resp = asyncio.run(runner.execute(test, VariableStore()))
    assert resp.status_code == 0, resp.error
    assert resp.body == "quick"


# ---------------------------------------------------------------------------
# HttpxRunner — capture the AsyncClient timeout kwarg
# ---------------------------------------------------------------------------


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"ok": true}'

    def json(self):
        return {"ok": True}

    @property
    def text(self):
        return '{"ok": true}'


class _FakeAsyncClient:
    """Captures the ``timeout`` it was constructed with."""

    last_timeout: object = None

    def __init__(self, timeout=None):
        _FakeAsyncClient.last_timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, **kwargs):
        return _FakeResponse()


def _http_test(timeout: int | None = None) -> models.Test:
    return models.Test(
        id="H1",
        name="http",
        method="GET",
        path="/ping",
        auth="none",
        timeout=timeout,
        assert_=Assertion(),
    )


def _http_runner(monkeypatch):
    from regrun.runners import httpx_runner as hr

    monkeypatch.setattr(hr.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_timeout = None
    return hr.HttpxRunner(base_url="http://api.localhost", auth_configs={}, timeout=7)


def test_http_per_test_timeout_used(monkeypatch):
    runner = _http_runner(monkeypatch)
    asyncio.run(runner.execute(_http_test(timeout=42), VariableStore()))
    assert _FakeAsyncClient.last_timeout == 42


def test_http_falls_back_to_default(monkeypatch):
    runner = _http_runner(monkeypatch)
    asyncio.run(runner.execute(_http_test(), VariableStore()))
    assert _FakeAsyncClient.last_timeout == 7


# ---------------------------------------------------------------------------
# FastMcpRunner — the outer wait_for bound must use the per-test timeout
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]
        self.is_error = False
        self.data = None
        self.structured_content = None


class _SlowClient:
    """A client whose call_tool sleeps, to exercise the wait_for bound."""

    def __init__(self, url, auth=None, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, arguments=None, *, raise_on_error=True):
        await asyncio.sleep(0.3)
        return _Result('{"ok": true}')


def _mcp_test(timeout: int | None = None) -> models.Test:
    return models.Test(
        id="M1",
        name="mcp",
        tool="tasks_create",
        args={},
        timeout=timeout,
        assert_=Assertion(),
    )


def test_mcp_per_test_timeout_allows_slow_call(monkeypatch):
    """A 0.3s call that would be killed by a sub-0.3s default succeeds when the
    per-test timeout widens the outer wait_for bound."""
    monkeypatch.setattr(fr, "Client", _SlowClient)
    # Default 0s would time out immediately; per-test timeout=5 lets it run.
    runner = FastMcpRunner("http://mcp.localhost", {}, timeout=0, default_auth=None)
    resp = asyncio.run(runner.execute(_mcp_test(timeout=5), VariableStore()))
    assert resp.body["ok"] is True
    assert resp.body["is_error"] is False


def test_mcp_default_timeout_still_enforced(monkeypatch):
    """With no per-test override, a default shorter than the call kills it —
    proving the fallback path still bounds the call."""
    monkeypatch.setattr(fr, "Client", _SlowClient)
    runner = FastMcpRunner("http://mcp.localhost", {}, timeout=0, default_auth=None)
    resp = asyncio.run(runner.execute(_mcp_test(), VariableStore()))  # timeout=None
    assert "timed out after 0s" in (resp.error or "")
