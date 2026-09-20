"""Runner selection: which runner executes a test, and its credentials contract.

Extracted from ``executor.py`` (size limits) so the run loop stays a coordinator
and runner construction / auth-reference validation live in one place.
"""

from pathlib import Path

import structlog

from regrun.config import settings
from regrun.engine.variables import VariableStore
from regrun.models import Test, TestFile
from regrun.runners.bash_runner import BashRunner
from regrun.runners.fastmcp_runner import FastMcpRunner
from regrun.runners.httpx_runner import HttpxRunner
from regrun.runners.sql_runner import SqlRunner
from regrun.runners.websocket_runner import WebSocketRunner

logger = structlog.get_logger()

Runner = HttpxRunner | FastMcpRunner | BashRunner | WebSocketRunner | SqlRunner

# Runner types whose requests carry auth credentials — the only ones where a
# dangling auth-profile reference silently weakens the request (bash/sql
# runners never read auth config).
AUTH_CONSUMING_RUNNERS = frozenset({"httpx", "fastmcp", "websocket"})

__all__ = [
    "AUTH_CONSUMING_RUNNERS",
    "Runner",
    "close_runners",
    "create_runner_for_type",
    "get_runner_for_test",
    "unknown_auth_profile_error",
]


def create_runner_for_type(
    runner_type: str,
    test_file: TestFile,
    store: VariableStore | None = None,
) -> Runner | None:
    """Create a runner instance for the given runner type.

    ``store`` (when provided) feeds the bash child environment its ``RUN_ID``.
    """
    if runner_type == "httpx":
        endpoint = test_file.meta.endpoint
        if not endpoint:
            logger.error("missing_endpoint", runner=runner_type)
            return None
        return HttpxRunner(
            base_url=endpoint,
            auth_configs=test_file.auth,
            timeout=settings.timeout,
            default_auth=test_file.meta.default_auth,
        )

    if runner_type == "fastmcp":
        endpoint = test_file.meta.mcp_endpoint or test_file.meta.endpoint
        if not endpoint:
            logger.error("missing_endpoint", runner=runner_type)
            return None
        return FastMcpRunner(
            server_url=endpoint,
            auth_configs=test_file.auth,
            timeout=settings.mcp_timeout,
            default_auth=test_file.meta.default_auth,
        )

    if runner_type == "bash":
        # Use the current working directory as the cwd for bash commands.
        # This is where regrun was invoked from.
        # The bash child always knows the stack under test: the resolved
        # endpoints and the run's effective RUN_ID ride the environment, so
        # ${REGRUN_API_ENDPOINT} is always correct and hardcoding a host is
        # unnecessary rather than merely discouraged.
        extra_env: dict[str, str] = {}
        if test_file.meta.endpoint:
            extra_env["REGRUN_API_ENDPOINT"] = test_file.meta.endpoint
        if test_file.meta.mcp_endpoint:
            extra_env["REGRUN_MCP_ENDPOINT"] = test_file.meta.mcp_endpoint
        if store is not None:
            extra_env["RUN_ID"] = store.effective_run_id
        return BashRunner(cwd=str(Path.cwd()), timeout=settings.timeout, env=extra_env)

    if runner_type == "sql":
        return SqlRunner(
            sql_connection=test_file.meta.sql_connection,
            cwd=str(Path.cwd()),
            timeout=settings.timeout,
        )

    if runner_type == "websocket":
        return WebSocketRunner(
            auth_configs=test_file.auth,
            timeout=settings.ws_timeout,
            default_auth=test_file.meta.default_auth,
        )

    logger.warning("unsupported_runner", runner=runner_type)
    return None


def unknown_auth_profile_error(test: Test, test_file: TestFile) -> str | None:
    """Return an error string when the test references an undefined auth profile.

    Auth profiles are PER-FILE (only captured variables propagate cross-file via
    the VariableStore). Before 0.9.1 an undefined ``auth:`` reference degraded
    to a warning and the request went out with NO credentials — surfacing as a
    confusing 401-instead-of-403 that reads like a product bug. Same closed-world
    doctrine as strict-vars: a dangling reference fails loudly, never silently
    weakens the request.
    """
    runner_type = test.runner or test_file.meta.runner
    if runner_type not in AUTH_CONSUMING_RUNNERS:
        return None
    auth_name = test.auth or test_file.meta.default_auth
    if not auth_name or auth_name == "none":
        return None
    if auth_name in test_file.auth:
        return None
    defined = ", ".join(sorted(test_file.auth)) or "<none>"
    source = "auth" if test.auth else "meta.default_auth"
    return (
        f"unknown auth profile in test {test.id}: {source}={auth_name!r} is not "
        f"defined in this file's auth: block (defined: {defined}). Auth profiles "
        f"are per-file — redeclare the profile in this file (its token variable "
        f"still propagates via the VariableStore), or use 'none'."
    )


def get_runner_for_test(
    test: Test,
    test_file: TestFile,
    runner_cache: dict[str, Runner],
    store: VariableStore | None = None,
) -> Runner | None:
    """Get the runner for a test, respecting per-test runner overrides."""
    runner_type = test.runner or test_file.meta.runner
    if runner_type not in runner_cache:
        runner = create_runner_for_type(runner_type, test_file, store)
        if runner is not None:
            runner_cache[runner_type] = runner
        else:
            return None
    return runner_cache[runner_type]


async def close_runners(runner_cache: dict[str, Runner]) -> None:
    """Close any runners holding persistent connections (e.g. the in-process
    fastmcp client). Best-effort: a close failure must not fail the run."""
    for runner in runner_cache.values():
        aclose = getattr(runner, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask results
            logger.warning("runner_close_failed", error=str(exc))
