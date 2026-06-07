"""Pydantic models for YAML regression test file schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TestMeta(BaseModel):
    """Top-level metadata for a test file."""

    model_config = ConfigDict(strict=True)

    product: str
    layer: Literal["api", "mcp", "setup", "chat"]
    runner: Literal["httpx", "fastmcp", "bash", "websocket"]
    endpoint: str | None = None
    mcp_endpoint: str | None = None
    default_auth: str | None = None
    env_file: str | None = None


class AuthConfig(BaseModel):
    """Authentication configuration for a named auth context."""

    model_config = ConfigDict(strict=True)

    type: Literal["bearer", "api_key"]
    token: str
    org_header: str | None = None


class Assertion(BaseModel):
    """Assertion block for a test. Mapped from the YAML 'assert' key."""

    model_config = ConfigDict(strict=False)

    status: int | list[int] | None = None
    is_error: bool | None = None
    has_error: bool | None = None
    last_exit_code: int | None = None
    json_path: dict[str, dict] | None = None
    contains: str | None = None


class BashCommand(BaseModel):
    """A single command in a bash test."""

    model_config = ConfigDict(strict=False)

    cmd: str
    capture: dict[str, str] | None = None


class WebSocketConfig(BaseModel):
    """Product-agnostic WebSocket event parsing configuration."""

    model_config = ConfigDict(strict=False)

    event_type_field: str = "event_type"
    event_type_fallback: str = "type"
    text_event: str = "text_delta"
    text_field: str = "data.delta"
    tool_call_event: str = "tool_call"
    tool_name_field: str = "data.tool_name"
    error_event: str = "error"
    error_field: str = "data.content"


class Test(BaseModel):
    """A single test case within a group."""

    model_config = ConfigDict(strict=False, populate_by_name=True)

    id: str
    name: str

    # API-specific fields
    method: str | None = None
    path: str | None = None
    auth: str | None = None
    org_header: bool | None = None
    body: dict | None = None
    query_params: dict[str, str] | None = None

    # MCP-specific fields
    tool: str | None = None
    args: dict | None = None

    # Bash-specific fields
    commands: list[BashCommand] | None = None

    # WebSocket-specific fields
    url: str | None = None
    send: dict | None = None
    wait_for: str | None = None
    timeout: int | None = None
    ws_config: WebSocketConfig | None = None

    # Per-test runner override (used when meta.runner differs, e.g. setup file)
    runner: Literal["httpx", "fastmcp", "bash", "websocket"] | None = None

    # Common fields
    assert_: Assertion = Field(alias="assert")
    capture: dict[str, str] | None = None


class Group(BaseModel):
    """A named group of tests."""

    model_config = ConfigDict(strict=False)

    name: str
    id: int
    priority: Literal["high", "medium", "low"] = "medium"
    context: Literal["prod", "fresh", "both"] = "prod"
    tests: list[Test]


class TestFile(BaseModel):
    """Top-level model representing a parsed YAML test file."""

    model_config = ConfigDict(strict=False)

    meta: TestMeta
    variables: dict[str, str] = Field(default_factory=dict)
    auth: dict[str, AuthConfig] = Field(default_factory=dict)
    groups: list[Group]
