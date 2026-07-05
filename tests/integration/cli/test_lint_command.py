"""CLI integration test for `regrun lint` via CliRunner against temp fixtures."""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write(directory: Path, name: str, doc: dict) -> None:
    (directory / name).write_text(yaml.safe_dump(doc, sort_keys=False))


def test_lint_exits_1_on_error(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "httpx"},
            "groups": [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [{"id": "A.1", "name": "t", "assert": {"status": 200}}],
                },
                {
                    "id": 5,
                    "name": "B",
                    "tests": [{"id": "B.1", "name": "t", "assert": {"status": 200}}],
                },
            ],
        },
    )
    result = CliRunner().invoke(cli, ["lint", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "E001" in result.output
    assert "duplicate group id 5" in result.output
    assert "1 error(s)" in result.output


def test_lint_exits_0_clean(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_api.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "httpx"},
            "groups": [
                {
                    "id": 5,
                    "name": "A",
                    "tests": [
                        {
                            "id": "A.1",
                            "name": "t",
                            "method": "GET",
                            "path": "/x",
                            "assert": {"status": 200},
                        }
                    ],
                },
            ],
        },
    )
    result = CliRunner().invoke(cli, ["lint", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_lint_strict_fails_on_warning(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "02_mcp.yaml",
        {
            "meta": {"product": "demo", "layer": "mcp", "runner": "fastmcp"},
            "groups": [
                {
                    "id": 10,
                    "name": "M",
                    "tests": [
                        {
                            "id": "M.1",
                            "name": "t",
                            "tool": "company_get",
                            "assert": {"is_error": False},
                        }
                    ],
                },
            ],
        },
    )
    ok = CliRunner().invoke(cli, ["lint", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    assert "W001" in ok.output

    strict = CliRunner().invoke(cli, ["lint", str(tmp_path), "--strict"])
    assert strict.exit_code == 1, strict.output
