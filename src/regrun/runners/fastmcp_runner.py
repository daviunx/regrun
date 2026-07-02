"""MCP tool test runner via an in-process, persistent fastmcp Client.

Replaces the previous per-test ``uvx fastmcp call`` subprocess (which paid CLI
startup + a fresh HTTP connection + MCP ``initialize`` handshake on EVERY test).
This runner opens ONE fastmcp ``Client`` per auth context and reuses the session
across all tests, collapsing ~1.6s/test of pure overhead. The asserted response
body is mapped through the same normalizer the CLI path used, so existing
JSONPath / is_error assertions and captures are unchanged.
"""

import asyncio
import time

import structlog
from fastmcp import Client

from regrun.engine.variables import VariableStore
from regrun.models import AuthConfig, Test
from regrun.runners.base import RunnerResponse
from regrun.runners.mcp_response import normalize_call_tool_result

logger = structlog.get_logger()


class FastMcpRunner:
    """Runner for MCP surface tests using an in-process fastmcp Client.

    One persistent ``Client`` is opened per distinct auth context (e.g. the
    ``prod`` and ``fresh`` api-key tokens) and reused for every tool call. Call
    ``aclose()`` when the run finishes to close the sessions.
    """

    def __init__(
        self,
        server_url: str,
        auth_configs: dict[str, AuthConfig],
        timeout: int = 60,
        default_auth: str | None = None,
    ) -> None:
        self._server_url = server_url
        self._auth_configs = auth_configs
        self._timeout = timeout
        self._default_auth = default_auth
        # One open Client per resolved auth token (None = unauthenticated).
        self._clients: dict[str | None, Client] = {}

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Execute an MCP tool call via the persistent in-process client.

        Args:
            test: Test definition with tool name and args.
            variables: Current variable store for auth token resolution.

        Returns:
            RunnerResponse with normalized MCP body and is_error metadata
            embedded in the body dict for assertion evaluation.
        """
        if not test.tool:
            return RunnerResponse(error="MCP test missing 'tool' field")

        auth_key = self._resolve_auth_key(test, variables, self._default_auth)

        # Per-test ``timeout`` (seconds) overrides the runner default when set.
        # This bounds the outer ``wait_for``, which is the effective ceiling on
        # the call. The persistent Client's own timeout stays at the runner
        # default because that client is cached and shared across tests.
        timeout = test.timeout if test.timeout is not None else self._timeout

        logger.debug(
            "mcp_request",
            tool=test.tool,
            server=self._server_url,
            auth=test.auth,
            has_args=test.args is not None,
        )

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._call_tool(auth_key, test),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("mcp_timeout", tool=test.tool, timeout=timeout)
            return RunnerResponse(
                error=f"MCP call timed out after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            # Transport / connection / client-side validation error. Surface as a
            # synthetic is_error body so the assertion engine can evaluate
            # is_error assertions (matches the old CLI client-error behavior).
            duration_ms = (time.monotonic() - start) * 1000
            logger.debug("mcp_client_error", tool=test.tool, error=str(exc))
            return RunnerResponse(
                status_code=None,
                body={"is_error": True, "error": str(exc)},
                duration_ms=duration_ms,
            )

        duration_ms = (time.monotonic() - start) * 1000

        mcp_response = normalize_call_tool_result(result)

        # Embed is_error for assertion-engine compatibility (it reads "is_error"
        # inside the response body dict).
        body = _embed_is_error(mcp_response.body, mcp_response.is_error)

        logger.debug(
            "mcp_response",
            tool=test.tool,
            is_error=mcp_response.is_error,
            duration_ms=round(duration_ms, 1),
            body_type=type(body).__name__,
        )

        return RunnerResponse(status_code=None, body=body, duration_ms=duration_ms)

    async def _call_tool(self, auth_key: str | None, test: Test):
        """Resolve/open the client for this auth context and call the tool.

        ``raise_on_error=False`` so a tool returning ``is_error: true`` yields a
        result (with its content) for assertion, instead of raising.
        """
        client = await self._client_for(auth_key)
        return await client.call_tool(
            test.tool,
            test.args or {},
            raise_on_error=False,
        )

    async def _client_for(self, auth_key: str | None) -> Client:
        """Get-or-open the persistent client for an auth context.

        A bearer-token string is passed as ``auth`` (fastmcp treats a ``str`` auth
        as a bearer token); an http URL transport is inferred from ``server_url``.
        """
        client = self._clients.get(auth_key)
        if client is None:
            client = Client(self._server_url, auth=auth_key, timeout=self._timeout)
            await client.__aenter__()  # open one persistent session (single initialize)
            self._clients[auth_key] = client
            logger.debug("mcp_client_opened", server=self._server_url, authenticated=bool(auth_key))
        return client

    async def aclose(self) -> None:
        """Close all open client sessions. Safe to call multiple times."""
        for client in self._clients.values():
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:  # never let cleanup mask the run result
                logger.warning("mcp_client_close_failed", error=str(exc))
        self._clients.clear()

    def _resolve_auth_key(
        self,
        test: Test,
        variables: VariableStore,
        default_auth: str | None = None,
    ) -> str | None:
        """Resolve the authentication token for the MCP call.

        Returns the resolved token string, or None if no auth is configured.
        """
        auth_name = test.auth or default_auth
        if not auth_name or auth_name == "none":
            return None

        auth_config = self._auth_configs.get(auth_name)
        if not auth_config:
            logger.warning("auth_config_missing", auth_name=auth_name)
            return None

        return variables.render_string(auth_config.token)


def _embed_is_error(body: dict | str | None, is_error: bool) -> dict | str | None:
    """Embed the ``is_error`` flag into the response body for the assertion engine.

    The assertion engine's ``_evaluate_is_error`` looks for an ``is_error`` key
    inside the response body dict. This ensures the key is present regardless of
    the MCP response pattern.

    If body is not a dict (string or None), wraps it in a dict to carry the flag.
    """
    if isinstance(body, dict):
        # Only set if not already present (don't overwrite tool-level is_error)
        if "is_error" not in body:
            return {**body, "is_error": is_error}
        return body

    if body is None:
        return {"is_error": is_error}

    # Body is a string -- wrap in dict
    return {"is_error": is_error, "_raw_text": body}
