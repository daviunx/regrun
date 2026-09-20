"""CLI integration tests: a schema-invalid suite file is loud at both entrypoints.

Every schema model forbids extra keys, so the engine refuses a file carrying an
undeclared key. The linter reads raw dictionaries, which made it structurally
possible for it to be BLINDER than the engine: the file satisfied every
text-level rule and reported ``0 errors`` while ``regrun run`` could not load it.
Rule E006 closes that gap.

Guarantees under test:

  * ``regrun lint`` exits non-zero with an E006 finding naming the undeclared key.
  * ``regrun run`` fails on the same file having executed nothing.
  * the abort covers the whole directory, including a ``--file`` run that did not
    select the offending file: every discovered file is parsed and validated
    before selection is applied, so a narrowed green can never come out of a
    suite holding a file the engine cannot load.
  * neither is triggered by a valid suite (no false E006).
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write(directory: Path, name: str, doc: dict) -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _bash_test(test_id: str, command: dict) -> dict:
    return {
        "id": test_id,
        "name": f"test {test_id}",
        "commands": [command],
        "assert": {"last_exit_code": 0},
    }


def _suite(tmp_path: Path, command: dict) -> Path:
    """A one-file suite whose single bash command is caller-supplied."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write(
        test_dir,
        "00_setup.yaml",
        {
            "meta": {"product": "demo", "layer": "setup", "runner": "bash"},
            "groups": [
                {
                    "id": 1,
                    "name": "Bootstrap",
                    "priority": "high",
                    "tests": [_bash_test("S.1", command)],
                }
            ],
        },
    )
    return test_dir


# A per-command ``assert:`` is not part of the schema: only the test-level block
# is evaluated, so the condition written here never ran.
MISPLACED_KEY_COMMAND = {"cmd": "true", "assert": {"contains": "NEVER_CHECKED"}}
VALID_COMMAND = {"cmd": "true"}


def _run(test_dir: Path, tmp_path: Path, *args: str):
    return CliRunner().invoke(
        cli,
        ["run", str(test_dir), *args],
        env={
            "REGRUN_RUNS_DIR": str(tmp_path / "runs"),
            "REGRUN_LOCK_TARGET": "demotarget",
        },
    )


def _mixed_suite(tmp_path: Path) -> Path:
    """A valid api file next to an UNRELATED schema-invalid api file."""
    test_dir = _suite(tmp_path, VALID_COMMAND)
    _write(
        test_dir,
        "01_valid.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "groups": [
                {
                    "id": 5,
                    "name": "Valid",
                    "priority": "high",
                    "tests": [_bash_test("V.1", VALID_COMMAND)],
                }
            ],
        },
    )
    _write(
        test_dir,
        "02_invalid.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "groups": [
                {
                    "id": 6,
                    "name": "Invalid",
                    "priority": "high",
                    "tests": [_bash_test("X.1", MISPLACED_KEY_COMMAND)],
                }
            ],
        },
    )
    return test_dir


# ------------------------------------------------------------------------------ lint


def test_lint_reports_e006_on_a_schema_invalid_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["lint", str(_suite(tmp_path, MISPLACED_KEY_COMMAND))])
    assert result.exit_code == 1, result.output
    assert "E006" in result.output
    assert "1 error(s)" in result.output


def test_the_e006_finding_names_the_key_and_the_test(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["lint", str(_suite(tmp_path, MISPLACED_KEY_COMMAND))])
    assert "groups.0.tests.0.commands.0.assert" in result.output
    assert "S.1" in result.output


def test_lint_reports_no_e006_on_a_valid_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["lint", str(_suite(tmp_path, VALID_COMMAND))])
    assert "E006" not in result.output


# ------------------------------------------------------------------------------- run


def test_run_fails_on_a_schema_invalid_file(tmp_path: Path) -> None:
    result = _run(_suite(tmp_path, MISPLACED_KEY_COMMAND), tmp_path)
    assert result.exit_code != 0, result.output
    assert "00_setup.yaml" in result.output


def test_run_executes_nothing_on_a_schema_invalid_file(tmp_path: Path) -> None:
    result = _run(_suite(tmp_path, MISPLACED_KEY_COMMAND), tmp_path)
    assert "S.1" not in result.output
    assert not (tmp_path / "runs").exists()


def test_run_succeeds_on_the_same_suite_without_the_misplaced_key(tmp_path: Path) -> None:
    result = _run(_suite(tmp_path, VALID_COMMAND), tmp_path)
    assert result.exit_code == 0, result.output
    assert "S.1" in result.output


# --------------------------------------------------------- blast radius: whole directory


def test_a_file_run_aborts_over_an_unselected_invalid_file(tmp_path: Path) -> None:
    """Intended: every discovered file is validated before selection is applied.

    A suite holding a file the engine cannot load is not a suite a narrowed green
    can be trusted from, so the abort is not scoped to the selection.
    """
    result = _run(_mixed_suite(tmp_path), tmp_path, "--file", "01_valid")
    assert result.exit_code != 0, result.output
    assert "02_invalid.yaml" in result.output


def test_that_abort_executes_nothing_and_writes_no_run_dir(tmp_path: Path) -> None:
    result = _run(_mixed_suite(tmp_path), tmp_path, "--file", "01_valid")
    assert "V.1" not in result.output
    assert "S.1" not in result.output
    assert not (tmp_path / "runs").exists()
