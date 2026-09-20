"""CLI integration tests for ``--rerun-failed`` (0.10.0).

After a red run, one command must re-execute setup plus only the files that had
failed or blocked tests in the MOST RECENT report for this product and target,
and say which report it read. A red run should cost one file's re-run, not the
whole suite.

Guarantees under test:

  * a red ``report.json`` selects exactly the failed + blocked stems (plus setup)
    and the report path is echoed.
  * a green report, or no report at all, runs NOTHING and exits 0 saying so.
  * the LATEST timestamp directory wins.
  * resolution is scoped to the run's own target — another isolate's report is
    invisible.
  * a failed setup file is re-run with the setup files that precede it, so the
    same selection rule covers both file selectors.
"""

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli

TARGET = "demotarget"


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _bash_test(test_id: str) -> dict:
    return {
        "id": test_id,
        "name": f"test {test_id}",
        "commands": [{"cmd": "true"}],
        "assert": {"last_exit_code": 0},
    }


def _file_doc(layer: str, groups: list[dict], requires: list[str] | None = None) -> dict:
    meta: dict = {"product": "demo", "layer": layer, "runner": "bash"}
    if requires is not None:
        meta["requires"] = requires
    return {"meta": meta, "groups": groups}


def _group(group_id: int, name: str, test_ids: list[str]) -> dict:
    return {
        "id": group_id,
        "name": name,
        "priority": "high",
        "tests": [_bash_test(t) for t in test_ids],
    }


def _suite(tmp_path: Path) -> Path:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _file_doc("setup", [_group(1, "Bootstrap", ["S.1"])]))
    _write_yaml(test_dir, "01_provider.yaml", _file_doc("api", [_group(5, "Provider", ["P.1"])]))
    _write_yaml(
        test_dir,
        "02_consumer.yaml",
        _file_doc("api", [_group(6, "Consumer", ["C.1"])], requires=["01_provider"]),
    )
    _write_yaml(
        test_dir, "03_independent.yaml", _file_doc("api", [_group(8, "Independent", ["I.1"])])
    )
    return test_dir


def _test_result(test_id: str, file_stem: str, **overrides: object) -> dict:
    row: dict = {
        "test_id": test_id,
        "test_name": f"test {test_id}",
        "group_name": "G",
        "passed": True,
        "skipped": False,
        "duration_ms": 1.0,
        "file_stem": file_stem,
    }
    row.update(overrides)
    return row


def _write_report(
    runs_dir: Path,
    timestamp: str,
    results: list[dict],
    target: str = TARGET,
) -> Path:
    run_dir = runs_dir / "demo" / target / timestamp
    run_dir.mkdir(parents=True)
    failed = sum(1 for r in results if not r["passed"] and not r["skipped"])
    blocked = sum(1 for r in results if r.get("blocked_by"))
    payload = {
        "product": "demo",
        "target": target,
        "regrun_version": "0.9.1",
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": failed,
        "skipped": sum(1 for r in results if r["skipped"]),
        "blocked": blocked,
        "errors": 0,
        "test_results": results,
    }
    report = run_dir / "report.json"
    report.write_text(json.dumps(payload, indent=2))
    return report


def _invoke(test_dir: Path, runs_dir: Path, *args: str):
    return CliRunner().invoke(
        cli,
        ["run", str(test_dir), "--rerun-failed", *args],
        env={"REGRUN_RUNS_DIR": str(runs_dir), "REGRUN_LOCK_TARGET": TARGET},
    )


# -------------------------------------------------------------------------- red report


def test_red_report_reruns_only_the_failed_stem_and_setup(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [
            _test_result("S.1", "00_setup"),
            _test_result("P.1", "01_provider", passed=False),
            _test_result("I.1", "03_independent"),
        ],
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert result.exit_code == 0, result.output
    assert "S.1" in result.output
    assert "P.1" in result.output
    assert "I.1" not in result.output


def test_red_report_path_is_echoed(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    report = _write_report(
        runs_dir,
        "20260101-120000",
        [_test_result("P.1", "01_provider", passed=False)],
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert str(report) in result.output, result.output


def test_blocked_stems_are_rerun_too(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [
            _test_result("P.1", "01_provider", passed=False),
            _test_result(
                "C.1", "02_consumer", passed=False, skipped=True, blocked_by="01_provider"
            ),
            _test_result("I.1", "03_independent"),
        ],
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert "P.1" in result.output
    assert "C.1" in result.output
    assert "I.1" not in result.output


def test_errored_stems_are_rerun(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [
            _test_result("P.1", "01_provider", passed=False, error="connection refused"),
            _test_result("I.1", "03_independent"),
        ],
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert "P.1" in result.output
    assert "I.1" not in result.output


def test_latest_timestamp_directory_wins(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [_test_result("P.1", "01_provider", passed=False)],
    )
    newer = _write_report(
        runs_dir,
        "20260102-090000",
        [_test_result("I.1", "03_independent", passed=False)],
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert str(newer) in result.output
    assert "I.1" in result.output
    assert "P.1" not in result.output


# ------------------------------------------------------------------- nothing to re-run


def test_green_report_runs_nothing_and_exits_zero(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [_test_result("P.1", "01_provider"), _test_result("I.1", "03_independent")],
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert result.exit_code == 0, result.output
    assert "nothing to re-run" in result.output.lower()
    assert "P.1" not in result.output


def test_no_previous_report_runs_nothing_and_exits_zero(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    result = _invoke(_suite(tmp_path), runs_dir)
    assert result.exit_code == 0, result.output
    assert "nothing to re-run" in result.output.lower()
    assert "P.1" not in result.output


def test_a_report_for_another_target_is_invisible(tmp_path: Path) -> None:
    """Two isolates of one product must never re-run each other's failures."""
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [_test_result("P.1", "01_provider", passed=False)],
        target="othertarget",
    )
    result = _invoke(_suite(tmp_path), runs_dir)
    assert result.exit_code == 0, result.output
    assert "nothing to re-run" in result.output.lower()


# ------------------------------------------------------------------------ setup layer


def _two_setup_suite(tmp_path: Path) -> Path:
    """A suite whose setup layer is two files, the later one reading the earlier's variable."""
    test_dir = tmp_path / "pair"
    test_dir.mkdir()
    first = _file_doc("setup", [_group(1, "Bootstrap", ["S.1"])])
    first["variables"] = {"SEED_TOKEN": "token-value"}
    _write_yaml(test_dir, "00_setup.yaml", first)

    second = _file_doc("setup", [_group(2, "Seed", ["S.2"])])
    second["groups"][0]["tests"][0]["commands"] = [{"cmd": "test -n '{{SEED_TOKEN}}'"}]
    _write_yaml(test_dir, "00g_seed.yaml", second)

    _write_yaml(test_dir, "01_provider.yaml", _file_doc("api", [_group(5, "Provider", ["P.1"])]))
    return test_dir


def test_a_failed_setup_file_reruns_with_the_setup_files_before_it(tmp_path: Path) -> None:
    """Re-running one setup file must carry the setup layer that precedes it."""
    runs_dir = tmp_path / "runs"
    _write_report(
        runs_dir,
        "20260101-120000",
        [
            _test_result("S.1", "00_setup"),
            _test_result("S.2", "00g_seed", passed=False),
            _test_result("P.1", "01_provider"),
        ],
    )
    result = _invoke(_two_setup_suite(tmp_path), runs_dir)
    assert result.exit_code == 0, result.output
    assert "S.1" in result.output
    assert "S.2" in result.output
    assert "P.1" not in result.output
