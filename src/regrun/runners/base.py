"""Runner protocol and shared response model."""

from typing import Any, Protocol

from pydantic import BaseModel, Field

from regrun.engine.variables import VariableStore
from regrun.models import Test


class RequestEcho(BaseModel):
    """Echo of what a runner actually sent, after template rendering.

    Each runner populates the subset of fields relevant to its surface so a
    failure can be fully reproduced from the diagnostics block:

    * httpx: ``method``, ``url``, ``headers`` (auth redacted at capture),
      ``body``, ``query_params``
    * fastmcp: ``tool`` + ``args``
    * bash: ``commands`` (rendered command strings)
    * websocket: ``url``, ``send``, ``wait_for``
    """

    runner: str

    # httpx
    method: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    body: Any = None
    query_params: dict[str, Any] | None = None

    # fastmcp
    tool: str | None = None
    args: dict[str, Any] | None = None

    # bash
    commands: list[str] | None = None

    # sql
    sql: str | None = None

    # websocket
    send: Any = None
    wait_for: str | None = None


class RunnerResponse(BaseModel):
    """Standardized response from any runner execution."""

    status_code: int | None = None
    body: dict | list | str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    request_echo: RequestEcho | None = None

    # Resolved auth-token values, carried to the diagnostics builder so they can
    # be scrubbed from any echoed body. ``exclude=True`` keeps them out of every
    # serialization -- they must never reach output.
    secret_values: list[str] = Field(default_factory=list, exclude=True)


class RunnerProtocol(Protocol):
    """Interface that all test surface runners must satisfy."""

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Execute a single test and return the response.

        Args:
            test: The test definition (already template-rendered).
            variables: The current variable store (for auth resolution etc.).

        Returns:
            RunnerResponse with status, body, timing, and any error.
        """
        ...
