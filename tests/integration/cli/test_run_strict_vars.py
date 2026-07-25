"""CLI integration tests for strict variable resolution (0.9.0).

The third false-green door (with test_run_false_green_doors.py): an unresolved
``{{VAR}}`` used to render as the raw literal with only a WARN log. Now, by
default, it FAILS the test naming the variable and the test id. Opt-outs:
``meta.strict_vars: false`` per file, ``--no-strict-vars`` per run.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _suite(strict_vars: bool | None = None) -> dict:
    meta: dict = {"product": "demo", "layer": "api", "runner": "bash"}
    if strict_vars is not None:
        meta["strict_vars"] = strict_vars
    return {
        "meta": meta,
        "groups": [
            {
                "id": 5,
                "name": "Vars",
                "priority": "high",
                "tests": [
                    {
                        "id": "V.1",
                        "name": "uses an undefined variable",
                        "commands": [{"cmd": "echo regr-x-{{NEVER_CAPTURED}}"}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            }
        ],
    }


def _invoke(test_dir: Path, runs_dir: Path, *args: str):
    return CliRunner().invoke(
        cli, ["run", str(test_dir), *args], env={"REGRUN_RUNS_DIR": str(runs_dir)}
    )


def test_unresolved_var_fails_test_naming_var_and_test(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _suite())

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "NEVER_CAPTURED" in result.output
    assert "V.1" in result.output


def test_meta_strict_vars_false_opts_out(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _suite(strict_vars=False))

    result = _invoke(test_dir, tmp_path / "runs")

    # Old behaviour: the literal renders, the command exits 0, the run passes.
    assert result.exit_code == 0, result.output


def test_no_strict_vars_flag_opts_out(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _suite())

    result = _invoke(test_dir, tmp_path / "runs", "--no-strict-vars")

    assert result.exit_code == 0, result.output


def test_unresolved_file_variable_aborts_run(tmp_path: Path) -> None:
    # A `variables:` declaration referencing an undefined variable is a suite
    # defect surfaced before any group executes.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    doc = _suite()
    doc["variables"] = {"BROKEN": "{{UNDECLARED_BASE}}-x"}
    _write_yaml(test_dir, "01_api.yaml", doc)

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "UNDECLARED_BASE" in result.output
    assert "UNRESOLVED VARIABLE" in result.output


def test_declared_variable_may_reference_earlier_declaration(tmp_path: Path) -> None:
    # Sequential merge: a later declaration can reference an earlier one.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    doc = _suite()
    doc["variables"] = {"BASE": "regr", "TAG": "{{BASE}}-vis"}
    doc["groups"][0]["tests"][0]["commands"] = [{"cmd": "test '{{TAG}}' = 'regr-vis'"}]
    _write_yaml(test_dir, "01_api.yaml", doc)

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 0, result.output
