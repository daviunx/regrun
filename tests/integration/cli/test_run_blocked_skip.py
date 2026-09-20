"""CLI integration tests for blocked-skip (0.10.0).

A developer whose run hit a failure in something later files depend on must see
ONE failure and every dependent test reported as BLOCKED naming its blocker —
not a cascade of 30 indistinguishable failures.

Guarantees under test:

  * a file whose ``requires:`` closure contains a FAILED file does not run; its
    tests are reported blocked, naming the blocker.
  * ``cleanup: true`` groups in a blocked file STILL run (the 0.5.0 contract).
  * an independent file still runs and passes.
  * a failing setup-layer file blocks every later file.
  * blocking is transitive.
  * the summary and ``report.json`` carry a separate blocked count plus per-test
    ``blocked_by``; JUnit emits ``<skipped message="blocked by …">``.
  * the exit code stays 1 — driven by the provider's REAL failure, never
    green-by-omission.
"""

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


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


def _file_doc(layer: str, groups: list[dict], requires: list[str] | None = None) -> dict:
    meta: dict = {"product": "demo", "layer": layer, "runner": "bash"}
    if requires is not None:
        meta["requires"] = requires
    return {"meta": meta, "groups": groups}


def _group(group_id: int, name: str, tests: list[dict], cleanup: bool = False) -> dict:
    group: dict = {"id": group_id, "name": name, "priority": "high", "tests": tests}
    if cleanup:
        group["cleanup"] = True
    return group


def _invoke(test_dir: Path, tmp_path: Path, *args: str, output: str = "text"):
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["run", str(test_dir), "--output", output, *args],
        env={
            "REGRUN_RUNS_DIR": str(tmp_path / "runs"),
            "REGRUN_LOCK_TARGET": "demotarget",
        },
    )


def _suite(tmp_path: Path) -> tuple[Path, Path]:
    """setup (green) + provider (one failing test) + dependent + independent.

    Each non-blocked test touches a marker file, so "did it actually run" is
    observed rather than inferred from the report.
    """
    markers = tmp_path / "markers"
    markers.mkdir()
    test_dir = tmp_path / "suite"
    test_dir.mkdir()

    _write_yaml(
        test_dir,
        "00_setup.yaml",
        _file_doc("setup", [_group(1, "Bootstrap", [_bash_test("S.1")])]),
    )
    _write_yaml(
        test_dir,
        "01_provider.yaml",
        _file_doc(
            "api",
            [_group(5, "Provider", [_bash_test("P.1", cmd="false"), _bash_test("P.2")])],
        ),
    )
    _write_yaml(
        test_dir,
        "02_dependent.yaml",
        _file_doc(
            "api",
            [
                _group(6, "Dependent", [_bash_test("D.1", cmd=f"touch {markers}/dependent")]),
                _group(
                    7,
                    "Dependent Cleanup",
                    [_bash_test("CL.1", cmd=f"touch {markers}/cleanup")],
                    cleanup=True,
                ),
            ],
            requires=["01_provider"],
        ),
    )
    _write_yaml(
        test_dir,
        "03_independent.yaml",
        _file_doc(
            "api",
            [_group(8, "Independent", [_bash_test("I.1", cmd=f"touch {markers}/independent")])],
        ),
    )
    return test_dir, markers


def _json(output: str) -> dict:
    return json.loads(output[output.index("{") : output.rindex("}") + 1])


def _row(output: str, test_id: str) -> str:
    rows = [line for line in output.splitlines() if line.strip().startswith(test_id)]
    assert rows, f"no table row for {test_id} in:\n{output}"
    return rows[0]


# ----------------------------------------------------------------- blocked, not failed


def test_dependent_tests_are_blocked_and_name_the_blocker(tmp_path: Path) -> None:
    test_dir, _markers = _suite(tmp_path)
    result = _invoke(test_dir, tmp_path)
    row = _row(result.output, "D.1")
    assert "BLOCKED" in row, result.output
    assert "01_provider" in row, result.output


def test_a_blocked_file_does_not_execute_its_tests(tmp_path: Path) -> None:
    test_dir, markers = _suite(tmp_path)
    _invoke(test_dir, tmp_path)
    assert not (markers / "dependent").exists()


def test_cleanup_groups_in_a_blocked_file_still_run(tmp_path: Path) -> None:
    test_dir, markers = _suite(tmp_path)
    _invoke(test_dir, tmp_path)
    assert not (markers / "dependent").exists(), "the dependent file was not blocked at all"
    assert (markers / "cleanup").exists(), "a blocked file must still sweep"


def test_an_independent_file_still_runs_and_passes(tmp_path: Path) -> None:
    test_dir, markers = _suite(tmp_path)
    result = _invoke(test_dir, tmp_path)
    assert (markers / "independent").exists()
    assert "PASS" in _row(result.output, "I.1")


def test_the_providers_own_failure_is_still_a_failure(tmp_path: Path) -> None:
    """The provider's own red test is reported red — never reclassified as blocked."""
    test_dir, _markers = _suite(tmp_path)
    result = _invoke(test_dir, tmp_path, output="json")
    data = _json(result.output)
    provider = {tr["test_id"]: tr for tr in data["test_results"]}["P.1"]
    assert provider["passed"] is False
    assert provider["skipped"] is False
    assert "blocked_by" not in provider
    assert data["failed"] + data["errors"] == 1


def test_exit_code_is_one_because_of_the_real_failure(tmp_path: Path) -> None:
    test_dir, _markers = _suite(tmp_path)
    result = _invoke(test_dir, tmp_path)
    assert result.exit_code == 1, result.output


# -------------------------------------------------------------------------- summary/json


def test_summary_counts_blocked_separately(tmp_path: Path) -> None:
    test_dir, _markers = _suite(tmp_path)
    result = _invoke(test_dir, tmp_path)
    assert "Blocked: 1" in result.output, result.output


def test_json_report_carries_blocked_count_and_blocked_by(tmp_path: Path) -> None:
    test_dir, _markers = _suite(tmp_path)
    result = _invoke(test_dir, tmp_path, output="json")
    data = _json(result.output)
    assert data["blocked"] == 1
    by_id = {tr["test_id"]: tr for tr in data["test_results"]}
    assert by_id["D.1"]["blocked_by"] == "01_provider"
    assert by_id["D.1"]["skipped"] is True


def test_junit_marks_blocked_tests_with_the_blocker(tmp_path: Path) -> None:
    test_dir, _markers = _suite(tmp_path)
    _invoke(test_dir, tmp_path)
    junits = list((tmp_path / "runs").rglob("junit.xml"))
    assert junits, "run artifacts were not persisted"
    xml = junits[0].read_text()
    assert '<skipped message="blocked by 01_provider"' in xml


# --------------------------------------------------------------------- setup + transitive


def test_a_failing_setup_file_blocks_every_later_file(tmp_path: Path) -> None:
    markers = tmp_path / "markers"
    markers.mkdir()
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "00_setup.yaml",
        _file_doc("setup", [_group(1, "Bootstrap", [_bash_test("S.1", cmd="false")])]),
    )
    _write_yaml(
        test_dir,
        "01_api.yaml",
        _file_doc(
            "api",
            [
                _group(5, "Surface", [_bash_test("A.1", cmd=f"touch {markers}/api")]),
                _group(
                    6,
                    "Sweep",
                    [_bash_test("CL.1", cmd=f"touch {markers}/cleanup")],
                    cleanup=True,
                ),
            ],
        ),
    )

    result = _invoke(test_dir, tmp_path)

    assert result.exit_code == 1, result.output
    row = _row(result.output, "A.1")
    assert "BLOCKED" in row
    assert "00_setup" in row
    assert not (markers / "api").exists()
    assert (markers / "cleanup").exists(), "a blocked file must still sweep"


def test_blocking_is_transitive(tmp_path: Path) -> None:
    """A fails; B requires A; C requires B — both B and C are blocked, not failed."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_a.yaml",
        _file_doc("api", [_group(5, "A", [_bash_test("A.1", cmd="false")])]),
    )
    _write_yaml(
        test_dir,
        "02_b.yaml",
        _file_doc("api", [_group(6, "B", [_bash_test("B.1")])], requires=["01_a"]),
    )
    _write_yaml(
        test_dir,
        "03_c.yaml",
        _file_doc("api", [_group(7, "C", [_bash_test("C.1")])], requires=["02_b"]),
    )

    result = _invoke(test_dir, tmp_path, output="json")
    data = _json(result.output)

    by_id = {tr["test_id"]: tr for tr in data["test_results"]}
    assert by_id["B.1"]["blocked_by"] == "01_a"
    assert by_id["C.1"]["blocked_by"] in {"01_a", "02_b"}
    assert data["blocked"] == 2
    assert data["failed"] + data["errors"] == 1
