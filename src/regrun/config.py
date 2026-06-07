"""Regression runner configuration via Pydantic settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Regression runner settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="REGRUN_",
        case_sensitive=False,
    )

    timeout: int = 30
    """Per-test HTTP timeout in seconds."""

    mcp_timeout: int = 60
    """Per-test MCP call timeout in seconds."""

    ws_timeout: int = 30
    """Per-test WebSocket timeout in seconds (used when test doesn't specify timeout)."""

    verbose: bool = False
    """Log full request/response bodies."""

    api_endpoint: str | None = None
    """Override meta.endpoint in all YAML files. Set REGRUN_API_ENDPOINT for CI."""

    mcp_endpoint: str | None = None
    """Override meta.mcp_endpoint in all YAML files. Set REGRUN_MCP_ENDPOINT for CI."""


settings = Settings()
