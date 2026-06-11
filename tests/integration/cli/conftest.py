"""Shared YAML fixture builders for CLI integration tests.

Builds minimal-but-valid regression test files (validated against
`regrun.models.TestFile`) into a temporary directory, so the `run` command
can be exercised end to end via `CliRunner` with real files (no mocks).
"""

from pathlib import Path

import pytest
import yaml


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _test(test_id: str) -> dict:
    """A minimal valid test case (bash, no assertions of substance)."""
    return {
        "id": test_id,
        "name": f"test {test_id}",
        "commands": [{"cmd": "true"}],
        "assert": {"last_exit_code": 0},
    }


def _group(group_id: int, name: str, priority: str, test_ids: list[str]) -> dict:
    return {
        "id": group_id,
        "name": name,
        "priority": priority,
        "tests": [_test(t) for t in test_ids],
    }


def setup_file_doc() -> dict:
    """Setup-layer file mirroring the real 00_setup.yaml shape.

    Groups 1-2, priority high, capturing variables downstream layers need.
    """
    return {
        "meta": {"product": "demo", "layer": "setup", "runner": "bash"},
        "variables": {"RUN_ID": "{{timestamp}}"},
        "groups": [
            _group(1, "Production Context Setup", "high", ["P.1", "P.2"]),
            _group(2, "Auth Bootstrap", "high", ["A.1"]),
        ],
    }


def api_file_doc() -> dict:
    """API-layer file with higher group IDs and mixed priorities."""
    return {
        "meta": {
            "product": "demo",
            "layer": "api",
            "runner": "httpx",
            "endpoint": "http://demo.localhost",
        },
        "groups": [
            _group(5, "API Surface", "medium", ["API.1", "API.2"]),
            _group(6, "API Errors", "low", ["API.3"]),
        ],
    }


def mcp_file_doc() -> dict:
    """MCP-layer file with a distinct high group ID."""
    return {
        "meta": {
            "product": "demo",
            "layer": "mcp",
            "runner": "fastmcp",
            "mcp_endpoint": "http://demo-mcp.localhost",
        },
        "groups": [
            _group(16, "MCP Surface", "medium", ["MCP.1", "MCP.2"]),
        ],
    }


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """A temp directory holding setup + api + mcp YAML fixture files."""
    _write_yaml(tmp_path, "00_setup.yaml", setup_file_doc())
    _write_yaml(tmp_path, "01_api_surface.yaml", api_file_doc())
    _write_yaml(tmp_path, "02_mcp_surface.yaml", mcp_file_doc())
    return tmp_path
