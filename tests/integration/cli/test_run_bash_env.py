"""CLI integration tests for bash child env injection (0.9.0).

Bash steps used to inherit only the ambient environment — the runner never told
them which stack was under test (RGRN-18), so a hardcoded host silently read
the wrong stack. The executor now injects the resolved
``REGRUN_API_ENDPOINT`` / ``REGRUN_MCP_ENDPOINT`` and the run's ``RUN_ID`` into
every bash child, proven here by echoing them back through assertions.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _suite(cmd: str, assertion: dict, variables: dict | None = None) -> dict:
    doc: dict = {
        "meta": {
            "product": "demo",
            "layer": "api",
            "runner": "bash",
            "endpoint": "http://demo.localhost",
            "mcp_endpoint": "http://demo-mcp.localhost",
        },
        "groups": [
            {
                "id": 5,
                "name": "Env",
                "priority": "high",
                "tests": [
                    {
                        "id": "E.1",
                        "name": "env probe",
                        "commands": [{"cmd": cmd}],
                        "assert": assertion,
                    }
                ],
            }
        ],
    }
    if variables:
        doc["variables"] = variables
    return doc


def _invoke(test_dir: Path, runs_dir: Path):
    return CliRunner().invoke(cli, ["run", str(test_dir)], env={"REGRUN_RUNS_DIR": str(runs_dir)})


def test_bash_child_sees_resolved_endpoints(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_api.yaml",
        _suite(
            'echo "$REGRUN_API_ENDPOINT $REGRUN_MCP_ENDPOINT"',
            {"contains": "http://demo.localhost http://demo-mcp.localhost"},
        ),
    )

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 0, result.output


def test_bash_child_sees_engine_run_id(tmp_path: Path) -> None:
    # No declared RUN_ID: the engine builtin must reach the bash child AND
    # match what {{RUN_ID}} renders to in the same run.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_api.yaml",
        _suite('test "$RUN_ID" = "{{RUN_ID}}" && test -n "$RUN_ID"', {"last_exit_code": 0}),
    )

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 0, result.output


def test_bash_child_sees_suite_declared_run_id(tmp_path: Path) -> None:
    # BACKCOMPAT: a suite-declared RUN_ID wins, in the env too.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_api.yaml",
        _suite(
            'test "$RUN_ID" = "suite-owned-42"',
            {"last_exit_code": 0},
            variables={"RUN_ID": "suite-owned-42"},
        ),
    )

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 0, result.output
