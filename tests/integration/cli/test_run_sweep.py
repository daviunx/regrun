"""CLI integration tests for the first-class ``sweep:`` block (0.9.0).

Marker-file pattern (mirror of test_run_preflight.py): a bash suite whose group
``touch``es a marker file, plus top-level ``preflight:`` / ``sweep:`` blocks:

  (a) passing sweep -> groups execute (marker created) + header prints the
      executed step count (``sweep: N steps completed``).
  (b) failing sweep -> instant abort: non-zero exit, NO group marker written,
      output names the failed step and prints ``SWEEP FAILED``.
  (c) ``--skip-sweep`` -> groups run despite a failing sweep block.
  (d) ordering: sweep runs AFTER preflight — a failing preflight means the
      sweep never executes.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _suite_doc(
    marker: Path,
    *,
    sweep_cmd: str,
    preflight_cmd: str | None = None,
    sweep_marker: Path | None = None,
) -> dict:
    doc: dict = {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "sweep": [
            {
                "name": "sweep-prior-fixtures",
                "runner": "bash",
                "commands": [{"cmd": sweep_cmd}],
                "assert": {"last_exit_code": 0},
            }
        ],
        "groups": [
            {
                "id": 5,
                "name": "API Surface",
                "priority": "high",
                "tests": [
                    {
                        "id": "API.1",
                        "name": "runs",
                        "commands": [{"cmd": f"touch {marker}"}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            }
        ],
    }
    if preflight_cmd is not None:
        doc["preflight"] = [
            {
                "name": "backend-health",
                "runner": "bash",
                "commands": [{"cmd": preflight_cmd}],
                "assert": {"last_exit_code": 0},
            }
        ]
    if sweep_marker is not None:
        doc["sweep"][0]["commands"] = [{"cmd": f"touch {sweep_marker}"}]
    return doc


def _invoke(test_dir: Path, runs_dir: Path, *args: str):
    return CliRunner().invoke(
        cli, ["run", str(test_dir), *args], env={"REGRUN_RUNS_DIR": str(runs_dir)}
    )


def test_passing_sweep_runs_groups_and_prints_count(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _suite_doc(marker, sweep_cmd="true"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 0, result.output
    assert marker.exists(), "group must execute when sweep passes"
    assert "sweep: 1" in result.output.lower(), result.output


def test_failing_sweep_aborts_with_zero_groups(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _suite_doc(marker, sweep_cmd="false"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert not marker.exists(), "no group may execute after a sweep failure"
    assert "SWEEP FAILED" in result.output
    assert "sweep-prior-fixtures" in result.output


def test_skip_sweep_bypasses_failing_sweep(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _suite_doc(marker, sweep_cmd="false"))

    result = _invoke(test_dir, tmp_path / "runs", "--skip-sweep")

    assert result.exit_code == 0, result.output
    assert marker.exists(), "groups must run under --skip-sweep"


def test_sweep_runs_after_preflight(tmp_path: Path) -> None:
    # A failing preflight aborts BEFORE the sweep executes.
    marker = tmp_path / "ran"
    sweep_marker = tmp_path / "swept"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "00_setup.yaml",
        _suite_doc(marker, sweep_cmd="true", preflight_cmd="false", sweep_marker=sweep_marker),
    )

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "PREFLIGHT FAILED" in result.output
    assert not sweep_marker.exists(), "sweep must not run when preflight fails"
    assert not marker.exists()


def test_sweep_with_capture_fails_parse(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    doc = _suite_doc(marker, sweep_cmd="true")
    doc["sweep"][0]["capture"] = {"LEAK": "stdout"}
    _write_yaml(test_dir, "00_setup.yaml", doc)

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code != 0, result.output
    assert "Failed to parse" in result.output
    assert "capture-independent" in result.output
