"""CLI integration tests for time budgets (0.10.0).

A run that exceeds a DECLARED time budget is red for that reason, and the report
names the file and the overrun. Budgets are OFF unless declared: a suite that
declares none behaves exactly as it did in 0.9.x, exit code included.

Guarantees under test:

  * ``meta.budget_seconds`` overrun -> non-zero exit, the file named.
  * ``--budget-seconds`` (whole run) overrun -> non-zero exit, the overrun named.
  * a budget that is respected does not change the exit code.
  * no budget declared -> no behaviour change.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli

SLEEP_SECONDS = 0.3

# The report's budget-breach marker, matched case-SENSITIVELY: a temp-dir path can
# itself contain the word "budget", so an uppercased haystack cannot tell the
# report's verdict from the fixture's own directory name.
BREACH_MARKER = "BUDGET BREACH"


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _bash_test(test_id: str, cmd: str = "true") -> dict:
    return {
        "id": test_id,
        "name": f"test {test_id}",
        "commands": [{"cmd": cmd}],
        "assert": {"last_exit_code": 0},
    }


def _suite(tmp_path: Path, budget_seconds: float | None = None) -> Path:
    """One slow api file (sleeps ~0.3s) that optionally declares a file budget."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    meta: dict = {"product": "demo", "layer": "api", "runner": "bash"}
    if budget_seconds is not None:
        meta["budget_seconds"] = budget_seconds
    _write_yaml(
        test_dir,
        "01_slow.yaml",
        {
            "meta": meta,
            "groups": [
                {
                    "id": 5,
                    "name": "Slow Surface",
                    "priority": "high",
                    "tests": [_bash_test("SL.1", cmd=f"sleep {SLEEP_SECONDS}")],
                }
            ],
        },
    )
    return test_dir


def _invoke(test_dir: Path, tmp_path: Path, *args: str):
    return CliRunner().invoke(
        cli,
        ["run", str(test_dir), *args],
        env={
            "REGRUN_RUNS_DIR": str(tmp_path / "runs"),
            "REGRUN_LOCK_TARGET": "demotarget",
        },
    )


# ------------------------------------------------------------------------- file budget


def test_file_budget_overrun_exits_non_zero(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, budget_seconds=0.01), tmp_path)
    assert result.exit_code != 0, result.output


def test_file_budget_overrun_names_the_file_and_the_overrun(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, budget_seconds=0.01), tmp_path)
    assert BREACH_MARKER in result.output
    assert "01_slow" in result.output


def test_file_budget_overrun_does_not_fail_the_test_itself(tmp_path: Path) -> None:
    """The test passed; the BUDGET is what went red. Both facts must be visible."""
    result = _invoke(_suite(tmp_path, budget_seconds=0.01), tmp_path)
    rows = [line for line in result.output.splitlines() if line.strip().startswith("SL.1")]
    assert rows, result.output
    assert "PASS" in rows[0]


def test_respected_file_budget_leaves_the_run_green(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, budget_seconds=60.0), tmp_path)
    assert result.exit_code == 0, result.output
    assert BREACH_MARKER not in result.output


# -------------------------------------------------------------------------- run budget


def test_run_budget_overrun_exits_non_zero(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path), tmp_path, "--budget-seconds", "0.01")
    assert result.exit_code != 0, result.output
    assert BREACH_MARKER in result.output


def test_respected_run_budget_leaves_the_run_green(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path), tmp_path, "--budget-seconds", "60")
    assert result.exit_code == 0, result.output
    assert BREACH_MARKER not in result.output


# --------------------------------------------------------------------------- off by default


def test_no_budget_declared_changes_nothing(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path), tmp_path)
    assert result.exit_code == 0, result.output
    assert BREACH_MARKER not in result.output


def test_a_real_failure_still_reds_a_run_within_budget(tmp_path: Path) -> None:
    """Budget accounting must not mask an ordinary failure."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_api.yaml",
        {
            "meta": {
                "product": "demo",
                "layer": "api",
                "runner": "bash",
                "budget_seconds": 60.0,
            },
            "groups": [
                {
                    "id": 5,
                    "name": "Surface",
                    "priority": "high",
                    "tests": [_bash_test("A.1", cmd="false")],
                }
            ],
        },
    )
    result = _invoke(test_dir, tmp_path)
    assert result.exit_code == 1, result.output
