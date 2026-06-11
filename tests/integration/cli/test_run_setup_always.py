"""CLI integration tests for the setup-always-runs behavior.

Exercises the `run` command via `click.testing.CliRunner` with `--dry-run`
against real temp YAML fixtures (see conftest). Asserts which files and groups
appear in the dry-run plan for each row of the documented behavior matrix.

Behavior matrix (from analysis.md):

| Invocation                              | Setup file       | Other files    |
|-----------------------------------------|------------------|----------------|
| --layer mcp --group 16                  | full setup runs  | only group 16  |
| --group 5 (no layer)                    | full setup runs  | only group 5   |
| --priority medium                       | full setup runs  | only medium    |
| --layer setup --group 1                 | filtered grp 1   | excluded       |
| --layer mcp --group 16 --skip-setup     | excluded         | only group 16  |
| no filters                              | unchanged        | unchanged      |
"""

import re
from pathlib import Path

from click.testing import CliRunner

from regrun.cli import cli

SETUP_GROUPS = {1, 2}


def _run_dry(test_dir: Path, *args: str):
    """Invoke `regrun run <test_dir> --dry-run [args]`."""
    runner = CliRunner()
    return runner.invoke(cli, ["run", str(test_dir), "--dry-run", *args])


def _files_in_plan(output: str) -> set[str]:
    """Extract the set of YAML filenames present in the dry-run plan."""
    return set(re.findall(r"File: (\S+\.yaml)", output))


def _groups_in_plan(output: str) -> set[int]:
    """Extract the set of group IDs present in the dry-run plan."""
    return {int(g) for g in re.findall(r"Group (\d+):", output)}


def test_layer_mcp_group_16_keeps_full_setup(test_dir: Path) -> None:
    """--layer mcp --group 16 -> full setup (groups 1,2) + only MCP group 16."""
    result = _run_dry(test_dir, "--layer", "mcp", "--group", "16")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == {"00_setup.yaml", "02_mcp_surface.yaml"}
    assert _groups_in_plan(result.output) == SETUP_GROUPS | {16}


def test_group_5_no_layer_keeps_full_setup(test_dir: Path) -> None:
    """--group 5 (no layer) -> full setup + only group 5 in other files."""
    result = _run_dry(test_dir, "--group", "5")
    assert result.exit_code == 0, result.output
    # api file has group 5; mcp file (group 16) is dropped; setup stays full
    assert _files_in_plan(result.output) == {"00_setup.yaml", "01_api_surface.yaml"}
    assert _groups_in_plan(result.output) == SETUP_GROUPS | {5}


def test_priority_medium_keeps_full_setup(test_dir: Path) -> None:
    """--priority medium -> setup retained despite high-priority groups."""
    result = _run_dry(test_dir, "--priority", "medium")
    assert result.exit_code == 0, result.output
    files = _files_in_plan(result.output)
    groups = _groups_in_plan(result.output)
    # setup (high groups) kept in full; only medium groups elsewhere (5, 16)
    assert "00_setup.yaml" in files
    assert SETUP_GROUPS.issubset(groups)
    assert 5 in groups  # api medium
    assert 16 in groups  # mcp medium
    assert 6 not in groups  # api low filtered out


def test_layer_setup_group_1_filters_setup_and_excludes_others(test_dir: Path) -> None:
    """--layer setup --group 1 -> setup filtered to group 1, other layers excluded."""
    result = _run_dry(test_dir, "--layer", "setup", "--group", "1")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == {"00_setup.yaml"}
    assert _groups_in_plan(result.output) == {1}


def test_skip_setup_excludes_setup_file(test_dir: Path) -> None:
    """--layer mcp --group 16 --skip-setup -> no setup file, only group 16."""
    result = _run_dry(test_dir, "--layer", "mcp", "--group", "16", "--skip-setup")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == {"02_mcp_surface.yaml"}
    assert _groups_in_plan(result.output) == {16}


def test_no_filters_includes_everything(test_dir: Path) -> None:
    """No filters -> all files, all groups (regression guard)."""
    result = _run_dry(test_dir)
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == {
        "00_setup.yaml",
        "01_api_surface.yaml",
        "02_mcp_surface.yaml",
    }
    assert _groups_in_plan(result.output) == {1, 2, 5, 6, 16}
