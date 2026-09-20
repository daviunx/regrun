"""CLI integration tests: a schema-invalid suite file is loud at both entrypoints.

Every schema model forbids extra keys, so the engine refuses a file carrying an
undeclared key. The linter reads raw dictionaries, which made it structurally
possible for it to be BLINDER than the engine: the file satisfied every
text-level rule and reported ``0 errors`` while ``regrun run`` could not load it.
Rule E006 closes that gap.

Guarantees under test:

  * ``regrun lint`` exits non-zero with an E006 finding naming the undeclared key.
  * ``regrun run`` fails on the same file having executed nothing.
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


def _run(test_dir: Path, tmp_path: Path):
    return CliRunner().invoke(
        cli,
        ["run", str(test_dir)],
        env={
            "REGRUN_RUNS_DIR": str(tmp_path / "runs"),
            "REGRUN_LOCK_TARGET": "demotarget",
        },
    )


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
