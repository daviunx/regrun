"""CLI integration tests for the `preflight:` primitive (Phase 2) — RED gate.

Marker-file pattern (mirror of test_run_cleanup_always.py): a bash suite whose
group `touch`es a marker file, plus a top-level `preflight:` block. We assert:

  (a) passing preflight -> groups execute (marker created) + header prints the
      executed check count (`preflight: N checks`).
  (b) failing preflight -> instant abort: non-zero exit, NO group marker written,
      output names the failed check and prints `PREFLIGHT FAILED`.
  (c) `--skip-preflight` -> groups run despite a failing preflight block.

All red against current regrun 0.7.0 (preflight key silently ignored; no
`--skip-preflight` flag; no `preflight:` header line).
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _suite_doc(marker: Path, *, preflight_cmd: str) -> dict:
    """A bash suite: one preflight check + one group touching ``marker``."""
    return {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "preflight": [
            {
                "name": "backend-health",
                "runner": "bash",
                "commands": [{"cmd": preflight_cmd}],
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


def _invoke(test_dir: Path, runs_dir: Path, *args: str):
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["run", str(test_dir), *args],
        env={"REGRUN_RUNS_DIR": str(runs_dir)},
    )


def test_passing_preflight_runs_groups_and_prints_count(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _suite_doc(marker, preflight_cmd="true"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 0, result.output
    assert marker.exists(), "group must execute when preflight passes"
    assert "preflight: 1" in result.output.lower(), result.output


def test_failing_preflight_aborts_before_groups(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _suite_doc(marker, preflight_cmd="false"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code != 0, result.output
    assert not marker.exists(), "no group may run after a failed preflight"
    assert "PREFLIGHT FAILED" in result.output, result.output
    assert "backend-health" in result.output, result.output


def test_skip_preflight_runs_groups_despite_failure(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _suite_doc(marker, preflight_cmd="false"))

    result = _invoke(test_dir, tmp_path / "runs", "--skip-preflight")

    assert result.exit_code == 0, result.output
    assert marker.exists(), "--skip-preflight must bypass the failing check and run groups"
