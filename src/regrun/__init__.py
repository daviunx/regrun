"""regrun — YAML-driven regression test runner for HTTP APIs, MCP servers, and shell commands."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed package metadata, i.e. `version` in
    # pyproject.toml. Hardcoding it here drifts -- this said "0.1.0" through
    # eight releases while the CLI (which already read metadata) reported the
    # real version, so `regrun.__version__` silently lied to any consumer.
    __version__ = version("regrun")
except PackageNotFoundError:  # pragma: no cover - source checkout, not installed
    __version__ = "unknown"

__all__ = ["__version__"]
