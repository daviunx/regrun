"""Runner protocol and shared response model."""

from typing import Protocol

from pydantic import BaseModel

from regrun.engine.variables import VariableStore
from regrun.models import Test


class RunnerResponse(BaseModel):
    """Standardized response from any runner execution."""

    status_code: int | None = None
    body: dict | list | str | None = None
    error: str | None = None
    duration_ms: float = 0.0


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
