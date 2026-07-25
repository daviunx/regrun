"""CLI integration tests proving the 0.9.0 false-green doors are CLOSED.

Three silent-pass mechanisms of the same family as ``max_attempts: 0`` (closed
in 0.8.3), each proven to now FAIL loudly via the real ``regrun run`` command:

  * an ``assert:`` block that evaluates ZERO assertions (``assert: {}`` or
    ``json_path: {}``) -> the test errors with "zero assertions evaluated";
  * a typo'd assertion key (``statuss``) -> the file fails to parse
    (``Assertion`` is ``extra="forbid"``).

The third door — an unresolved ``{{VAR}}`` silently rendering as a literal —
is covered in test_run_strict_vars.py.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _bash_suite(assertion: dict, cmd: str = "echo ok") -> dict:
    return {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "groups": [
            {
                "id": 5,
                "name": "Doors",
                "priority": "high",
                "tests": [
                    {"id": "D.1", "name": "door", "commands": [{"cmd": cmd}], "assert": assertion}
                ],
            }
        ],
    }


def _invoke(test_dir: Path, runs_dir: Path, *args: str):
    return CliRunner().invoke(
        cli, ["run", str(test_dir), *args], env={"REGRUN_RUNS_DIR": str(runs_dir)}
    )


def test_empty_assert_block_fails_run(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _bash_suite({}))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "zero assertions evaluated" in result.output


def test_empty_json_path_block_fails_run(tmp_path: Path) -> None:
    # ``json_path: {}`` passes model validation (the key IS recognised) but
    # evaluates zero conditions — the executor door must catch it.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _bash_suite({"json_path": {}}, cmd="echo '{}'"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "zero assertions evaluated" in result.output


def test_typoed_assertion_key_fails_parse(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _bash_suite({"statuss": 201}))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code != 0, result.output
    assert "Failed to parse" in result.output
    assert "statuss" in result.output
