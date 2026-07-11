"""WebSocket streaming test runner using the websockets library."""

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from regrun.engine.variables import VariableStore
from regrun.models import AuthConfig, Test, WebSocketConfig
from regrun.runners.base import RequestEcho, RunnerResponse

logger = structlog.get_logger()

# Default config used when test.ws_config is None
_DEFAULT_WS_CONFIG = WebSocketConfig()


class WebSocketRunner:
    """Runner for WebSocket streaming tests.

    Connects to a WebSocket URL, sends a JSON frame, collects streamed
    events until a termination event is received (or timeout), aggregates
    them into a result dict, and returns a ``RunnerResponse`` for the
    existing assertion engine.
    """

    def __init__(
        self,
        auth_configs: dict[str, AuthConfig],
        timeout: int = 30,
        default_auth: str | None = None,
    ) -> None:
        self._auth_configs = auth_configs
        self._timeout = timeout
        self._default_auth = default_auth

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Execute a WebSocket streaming test.

        Args:
            test: The test definition with url, send, wait_for, etc.
            variables: Current variable store for auth token resolution.

        Returns:
            RunnerResponse with aggregated event data as body.
        """
        echo = RequestEcho(
            runner="websocket",
            url=test.url,
            send=test.send,
            wait_for=test.wait_for,
        )

        if not test.url:
            return RunnerResponse(error="WebSocket test missing 'url' field", request_echo=echo)

        if not test.send:
            return RunnerResponse(error="WebSocket test missing 'send' field", request_echo=echo)

        wait_for = test.wait_for
        if not wait_for:
            return RunnerResponse(
                error="WebSocket test missing 'wait_for' field", request_echo=echo
            )

        ws_config = test.ws_config or _DEFAULT_WS_CONFIG

        # Resolve timeout: per-test (ms) -> runner default (seconds)
        timeout_s = test.timeout / 1000.0 if test.timeout else float(self._timeout)

        headers = self._build_headers(test, variables)
        secret_values = self._resolve_secret_values(test, variables)

        logger.debug(
            "ws_request",
            url=test.url,
            wait_for=wait_for,
            timeout_s=timeout_s,
            auth=test.auth,
        )

        start = time.monotonic()
        try:
            body = await asyncio.wait_for(
                self._stream(test.url, test.send, wait_for, ws_config, headers),
                timeout=timeout_s,
            )
            duration_ms = (time.monotonic() - start) * 1000
            body["duration_ms"] = round(duration_ms, 1)

            logger.debug(
                "ws_response",
                event_count=body.get("event_count"),
                duration_ms=round(duration_ms, 1),
                has_error=body.get("error") is not None,
            )

            return RunnerResponse(
                body=body,
                duration_ms=duration_ms,
                request_echo=echo,
                secret_values=secret_values,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("ws_timeout", url=test.url, timeout_s=timeout_s)
            return RunnerResponse(
                body={
                    "response_text": "",
                    "events": [],
                    "event_count": 0,
                    "tool_calls": [],
                    "duration_ms": round(duration_ms, 1),
                    "error": f"WebSocket timed out after {timeout_s}s",
                },
                duration_ms=duration_ms,
                request_echo=echo,
                secret_values=secret_values,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("ws_error", url=test.url, error=str(e))
            return RunnerResponse(
                body={
                    "response_text": "",
                    "events": [],
                    "event_count": 0,
                    "tool_calls": [],
                    "duration_ms": round(duration_ms, 1),
                    "error": f"WebSocket error: {e}",
                },
                duration_ms=duration_ms,
                request_echo=echo,
                secret_values=secret_values,
            )

    async def _stream(
        self,
        url: str,
        payload: dict,
        wait_for: str,
        ws_config: WebSocketConfig,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Connect, send, and collect events until termination.

        Args:
            url: Full WebSocket URL.
            payload: JSON payload to send as a single frame.
            wait_for: Event type that terminates collection.
            ws_config: Event parsing configuration.
            headers: HTTP headers for the connection (auth, etc.).

        Returns:
            Aggregated result dict.
        """
        text_parts: list[str] = []
        event_types: list[str] = []
        tool_calls: list[str] = []
        error_msg: str | None = None

        async with websockets.connect(url, additional_headers=headers) as ws:
            await ws.send(json.dumps(payload))

            async for raw_message in ws:
                try:
                    data = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError):
                    continue

                event_type = _get_event_type(data, ws_config)
                if not event_type:
                    continue

                event_types.append(event_type)

                if event_type == ws_config.text_event:
                    text = _extract_dot_path(data, ws_config.text_field)
                    if text:
                        text_parts.append(str(text))

                elif event_type == ws_config.tool_call_event:
                    tool_name = _extract_dot_path(data, ws_config.tool_name_field)
                    if tool_name:
                        tool_calls.append(str(tool_name))

                elif event_type == ws_config.error_event:
                    error_content = _extract_dot_path(data, ws_config.error_field)
                    error_msg = str(error_content) if error_content else "Unknown error"

                if event_type == wait_for:
                    break

        return {
            "response_text": "".join(text_parts),
            "events": event_types,
            "event_count": len(event_types),
            "tool_calls": tool_calls,
            "duration_ms": 0,  # Placeholder, caller overwrites
            "error": error_msg,
        }

    def _resolve_secret_values(self, test: Test, variables: VariableStore) -> list[str]:
        """Resolve the auth-token value(s) for body scrubbing (never rendered)."""
        auth_name = test.auth or self._default_auth
        if not auth_name or auth_name == "none":
            return []
        auth_config = self._auth_configs.get(auth_name)
        if not auth_config:
            return []
        token = variables.render_string(auth_config.token)
        return [token] if token else []

    def _build_headers(self, test: Test, variables: VariableStore) -> dict[str, str]:
        """Build HTTP headers from test auth config and overrides."""
        headers: dict[str, str] = {}

        auth_name = test.auth or self._default_auth
        if auth_name and auth_name != "none":
            auth_config = self._auth_configs.get(auth_name)
            if auth_config:
                token = variables.render_string(auth_config.token)
                if auth_config.type == "bearer":
                    headers["Authorization"] = f"Bearer {token}"
                elif auth_config.type == "api_key":
                    headers["X-API-Key"] = token

                if auth_config.org_header and test.org_header is not False:
                    org_value = variables.render_string(auth_config.org_header)
                    headers["X-Org-Slug"] = org_value
            else:
                logger.warning("auth_config_missing", auth_name=auth_name)

        return headers


def _get_event_type(data: dict, ws_config: WebSocketConfig) -> str | None:
    """Extract the event type from a WebSocket message.

    Tries the primary field first, then the fallback.
    """
    event_type = data.get(ws_config.event_type_field)
    if event_type is None:
        event_type = data.get(ws_config.event_type_fallback)
    return str(event_type) if event_type is not None else None


def _extract_dot_path(data: dict, dot_path: str) -> Any:
    """Extract a value from a nested dict using a dot-separated path.

    Args:
        data: The source dictionary.
        dot_path: Dot-separated path like ``data.delta`` or ``data.tool_name``.

    Returns:
        The value at the path, or None if any segment is missing.
    """
    current: Any = data
    for segment in dot_path.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
        else:
            return None
    return current
