"""CLI integration tests for the cleanup-always-runs behavior.

Mirror of test_run_setup_always.py for the cleanup side. Groups flagged
``cleanup: true`` are the mirror of the setup layer: they must survive
``--group`` / ``--priority`` filtering within every included file, and they
must still EXECUTE when ``--fail-fast`` aborts the run (today the abort path
marks every remaining test skipped — cleanup included — which is how filtered
and aborted runs leak fixtures).

Behavior matrix (from analysis.md, rally-regression-flake-hardening):

| Invocation                      | Non-cleanup groups   | cleanup: true groups        |
|---------------------------------|----------------------|-----------------------------|
| --group 5                       | only group 5         | included (all files in run) |
| --priority high                 | only high            | included, any priority      |
| --group 5 --skip-cleanup        | only group 5         | excluded                    |
| --fail-fast, failure mid-run    | skipped after abort  | still executed              |
| no filters                      | unchanged            | unchanged                   |
"""

import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from regrun.cli import cli
from regrun.models import Group


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


def _api_file_with_cleanup_doc() -> dict:
    """API-layer file: normal groups 5/6 plus a cleanup-flagged group 9."""
    return {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "groups": [
            {"id": 5, "name": "API Surface", "priority": "medium", "tests": [_bash_test("API.1")]},
            {"id": 6, "name": "API Errors", "priority": "high", "tests": [_bash_test("API.2")]},
            {
                "id": 9,
                "name": "Cleanup",
                "priority": "medium",
                "cleanup": True,
                "tests": [_bash_test("CL.1")],
            },
        ],
    }


@pytest.fixture
def cleanup_test_dir(tmp_path: Path) -> Path:
    """A temp directory with one api-layer file carrying a cleanup group."""
    _write_yaml(tmp_path, "01_api_surface.yaml", _api_file_with_cleanup_doc())
    return tmp_path


def _run_dry(test_dir: Path, *args: str):
    runner = CliRunner()
    return runner.invoke(cli, ["run", str(test_dir), "--dry-run", *args])


def _groups_in_plan(output: str) -> set[int]:
    return {int(g) for g in re.findall(r"Group (\d+):", output)}


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


def test_group_model_has_cleanup_flag_default_false() -> None:
    """Group model exposes a ``cleanup`` bool defaulting to False."""
    group = Group.model_validate(
        {"id": 1, "name": "g", "tests": [{"id": "T.1", "name": "t", "assert": {"status": 200}}]}
    )
    assert group.cleanup is False

    flagged = Group.model_validate(
        {
            "id": 9,
            "name": "Cleanup",
            "cleanup": True,
            "tests": [{"id": "C.1", "name": "c", "assert": {"status": 200}}],
        }
    )
    assert flagged.cleanup is True


# ---------------------------------------------------------------------------
# Filtered runs include cleanup groups (dry-run plan)
# ---------------------------------------------------------------------------


def test_group_filter_keeps_cleanup_group(cleanup_test_dir: Path) -> None:
    """--group 5 -> group 5 plus the cleanup-flagged group 9; group 6 dropped."""
    result = _run_dry(cleanup_test_dir, "--group", "5")
    assert result.exit_code == 0, result.output
    assert _groups_in_plan(result.output) == {5, 9}


def test_priority_filter_keeps_cleanup_group(cleanup_test_dir: Path) -> None:
    """--priority high -> group 6 plus cleanup group 9 (medium) survives."""
    result = _run_dry(cleanup_test_dir, "--priority", "high")
    assert result.exit_code == 0, result.output
    assert _groups_in_plan(result.output) == {6, 9}


def test_skip_cleanup_excludes_cleanup_group(cleanup_test_dir: Path) -> None:
    """--group 5 --skip-cleanup -> only group 5; cleanup exemption suppressed."""
    result = _run_dry(cleanup_test_dir, "--group", "5", "--skip-cleanup")
    assert result.exit_code == 0, result.output
    assert _groups_in_plan(result.output) == {5}


def test_no_filters_plan_unchanged(cleanup_test_dir: Path) -> None:
    """No filters -> all groups, cleanup flag changes nothing."""
    result = _run_dry(cleanup_test_dir)
    assert result.exit_code == 0, result.output
    assert _groups_in_plan(result.output) == {5, 6, 9}


# ---------------------------------------------------------------------------
# --fail-fast abort still executes cleanup groups (real execution, bash runner)
# ---------------------------------------------------------------------------


def test_fail_fast_still_executes_cleanup_groups(tmp_path: Path) -> None:
    """A mid-run failure with --fail-fast skips later normal tests but still
    runs cleanup-flagged groups — in the failing file AND in later files."""
    markers = tmp_path / "markers"
    markers.mkdir()

    file_a = {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "groups": [
            {
                "id": 1,
                "name": "Failing Group",
                "priority": "high",
                "tests": [_bash_test("F.1", cmd="false")],
            },
            {
                "id": 2,
                "name": "Normal After Failure",
                "priority": "high",
                "tests": [_bash_test("N.1", cmd=f"touch {markers}/normal_a")],
            },
            {
                "id": 3,
                "name": "Cleanup A",
                "priority": "high",
                "cleanup": True,
                "tests": [_bash_test("CL.A", cmd=f"touch {markers}/cleanup_a")],
            },
        ],
    }
    file_b = {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "groups": [
            {
                "id": 4,
                "name": "Normal In Later File",
                "priority": "high",
                "tests": [_bash_test("N.2", cmd=f"touch {markers}/normal_b")],
            },
            {
                "id": 7,
                "name": "Cleanup B",
                "priority": "high",
                "cleanup": True,
                "tests": [_bash_test("CL.B", cmd=f"touch {markers}/cleanup_b")],
            },
        ],
    }
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_first.yaml", file_a)
    _write_yaml(test_dir, "02_second.yaml", file_b)

    runner = CliRunner()
    result = runner.invoke(cli, ["run", str(test_dir), "--fail-fast"])

    # The run failed (F.1) — cleanup must not mask the failure exit code.
    assert result.exit_code == 1, result.output

    # Normal tests after the abort were skipped.
    assert not (markers / "normal_a").exists(), "normal test after abort must be skipped"
    assert not (markers / "normal_b").exists(), "normal test in later file must be skipped"

    # Cleanup groups still executed — same file and later files.
    assert (markers / "cleanup_a").exists(), "cleanup group in failing file must still run"
    assert (markers / "cleanup_b").exists(), "cleanup group in later file must still run"


def test_fail_fast_with_skip_cleanup_skips_everything(tmp_path: Path) -> None:
    """--fail-fast --skip-cleanup: abort skips cleanup groups too."""
    markers = tmp_path / "markers"
    markers.mkdir()

    doc = {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "groups": [
            {
                "id": 1,
                "name": "Failing Group",
                "priority": "high",
                "tests": [_bash_test("F.1", cmd="false")],
            },
            {
                "id": 3,
                "name": "Cleanup",
                "priority": "high",
                "cleanup": True,
                "tests": [_bash_test("CL.1", cmd=f"touch {markers}/cleanup")],
            },
        ],
    }
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_first.yaml", doc)

    runner = CliRunner()
    result = runner.invoke(cli, ["run", str(test_dir), "--fail-fast", "--skip-cleanup"])

    assert result.exit_code == 1, result.output
    assert not (markers / "cleanup").exists(), "--skip-cleanup must suppress cleanup-always"
