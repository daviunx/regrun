"""MCP tool test runner via fastmcp CLI subprocess."""

import asyncio
import json
import time

import structlog

from regrun.engine.variables import VariableStore
from regrun.models import AuthConfig, Test
from regrun.runners.base import RunnerResponse
from regrun.runners.mcp_response import normalize_mcp_response

logger = structlog.get_logger()


class FastMcpRunner:
    """Runner for MCP surface tests using the fastmcp CLI subprocess.

    Executes MCP tool calls by spawning ``uvx fastmcp call`` as an async
    subprocess, then normalizes the JSON output through the MCP response
    parser before returning results for assertion evaluation.
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

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Execute an MCP tool call via the fastmcp CLI.

        Args:
            test: Test definition with tool name and args.
            variables: Current variable store for auth token resolution.

        Returns:
            RunnerResponse with normalized MCP body and is_error metadata
            embedded in the body dict for assertion evaluation.
        """
        if not test.tool:
            return RunnerResponse(
                error="MCP test missing 'tool' field",
            )

        auth_key = self._resolve_auth_key(test, variables, self._default_auth)
        cmd = self._build_command(test, auth_key)

        logger.debug(
            "mcp_request",
            tool=test.tool,
            server=self._server_url,
            auth=test.auth,
            has_args=test.args is not None,
        )

        start = time.monotonic()
        try:
            stdout, stderr, returncode = await self._run_subprocess(cmd)
            duration_ms = (time.monotonic() - start) * 1000
        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "mcp_timeout",
                tool=test.tool,
                timeout=self._timeout,
            )
            return RunnerResponse(
                error=f"MCP call timed out after {self._timeout}s",
                duration_ms=duration_ms,
            )

        # Non-zero exit code from fastmcp CLI.
        # fastmcp exits 1 when the MCP tool returns is_error: true, but
        # still writes valid JSON to stdout. Try to parse stdout first --
        # only treat as a subprocess error if stdout is empty or not JSON.
        if returncode != 0:
            duration_ms = (time.monotonic() - start) * 1000 if duration_ms == 0.0 else duration_ms
            if stdout and stdout.strip().startswith("{"):
                logger.debug(
                    "mcp_error_with_json",
                    tool=test.tool,
                    returncode=returncode,
                )
                # Fall through to normal JSON parsing below
            else:
                # Non-JSON error output (e.g. fastmcp client-side validation).
                # Construct a synthetic error body so the assertion engine can
                # evaluate is_error assertions instead of treating it as a
                # runner-level error.
                logger.debug(
                    "mcp_client_error",
                    tool=test.tool,
                    returncode=returncode,
                    stdout_preview=stdout[:200] if stdout else None,
                )
                error_text = stdout.strip() if stdout else (
                    stderr.strip() if stderr else f"fastmcp exited with code {returncode}"
                )
                return RunnerResponse(
                    status_code=None,
                    body={"is_error": True, "error": error_text},
                    duration_ms=duration_ms,
                )

        # Parse and normalize the MCP response
        try:
            mcp_response = normalize_mcp_response(stdout)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(
                "mcp_parse_error",
                tool=test.tool,
                error=str(e),
                stdout_preview=stdout[:500] if stdout else None,
            )
            return RunnerResponse(
                error=f"Failed to parse MCP response: {e}",
                duration_ms=duration_ms,
            )

        # Build body with is_error embedded for assertion engine compatibility.
        # The assertion engine's _evaluate_is_error looks for "is_error" key
        # inside the response body dict.
        body = _embed_is_error(mcp_response.body, mcp_response.is_error)

        logger.debug(
            "mcp_response",
            tool=test.tool,
            is_error=mcp_response.is_error,
            duration_ms=round(duration_ms, 1),
            body_type=type(body).__name__,
        )

        return RunnerResponse(
            status_code=None,
            body=body,
            duration_ms=duration_ms,
        )

    def _resolve_auth_key(
        self, test: Test, variables: VariableStore, default_auth: str | None = None,
    ) -> str | None:
        """Resolve the authentication key/token for the MCP call.

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

    def _build_command(self, test: Test, auth_key: str | None) -> list[str]:
        """Build the fastmcp CLI command as a list of arguments.

        Uses ``uvx fastmcp call`` with ``--json`` for parseable output.
        """
        cmd: list[str] = [
            "uvx",
            "fastmcp",
            "call",
            "--server-spec",
            self._server_url,
            "--transport",
            "http",
        ]

        if auth_key:
            cmd.extend(["--auth", auth_key])

        cmd.extend(["--target", test.tool])

        # Serialize args to JSON -- empty dict if None
        args_json = json.dumps(test.args or {})
        cmd.extend(["--input-json", args_json])

        cmd.append("--json")

        return cmd

    async def _run_subprocess(self, cmd: list[str]) -> tuple[str, str, int]:
        """Execute the fastmcp CLI as an async subprocess with timeout.

        Args:
            cmd: Command arguments list.

        Returns:
            Tuple of (stdout, stderr, return_code).

        Raises:
            asyncio.TimeoutError: If the subprocess exceeds the configured timeout.
        """
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            # Kill the subprocess on timeout
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        return stdout, stderr, process.returncode or 0


def _embed_is_error(body: dict | str | None, is_error: bool) -> dict | str | None:
    """Embed the ``is_error`` flag into the response body for assertion engine.

    The assertion engine's ``_evaluate_is_error`` looks for an ``is_error``
    key inside the response body dict. This function ensures that key is
    present regardless of MCP response pattern.

    If body is not a dict (string or None), wraps it in a dict to carry
    the is_error flag alongside the content.

    Args:
        body: Normalized MCP response body from pattern detection.
        is_error: Top-level is_error from the MCP response.

    Returns:
        Body dict with ``is_error`` key embedded.
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
