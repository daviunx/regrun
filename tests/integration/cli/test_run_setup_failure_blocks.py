"""CLI integration tests: a failed setup file blocks every later file.

A suite normally has SEVERAL setup files (auth bootstrap, then seed files), and
setup files never declare ``requires:`` on each other -- they are the bootstrap
every other file implicitly depends on. So when the auth bootstrap fails, the
seed file that sorts after it must not run and "pass": its verdict would be
meaningless, and a suite reporting a green seed after a dead bootstrap sends the
reader to the wrong place.

Guarantees under test:

  * the first setup file failing blocks the later setup files AND every api/mcp
    file, each naming the failed setup stem.
  * a later setup file failing leaves the earlier ones' verdicts intact.
  * the run exits non-zero, and the blocked tests are not counted as failures.
  * a CLEANUP-group failure inside a setup file blocks nothing (same rule
    ``file_failed`` already applies to every other file).
"""

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


def _group(group_id: int, name: str, tests: list[dict], cleanup: bool = False) -> dict:
    group: dict = {"id": group_id, "name": name, "priority": "high", "tests": tests}
    if cleanup:
        group["cleanup"] = True
    return group


def _file_doc(layer: str, groups: list[dict]) -> dict:
    return {
        "meta": {"product": "demo", "layer": layer, "runner": "bash"},
        "groups": groups,
    }


def _suite(tmp_path: Path, first_cmd: str = "true", second_cmd: str = "true") -> Path:
    """Two setup files, then an api file and an mcp file. No requires: anywhere."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "00_auth.yaml",
        _file_doc("setup", [_group(1, "Bootstrap", [_bash_test("S.1", cmd=first_cmd)])]),
    )
    _write_yaml(
        test_dir,
        "00a_seed.yaml",
        _file_doc("setup", [_group(2, "Seed", [_bash_test("SD.1", cmd=second_cmd)])]),
    )
    _write_yaml(test_dir, "01_api.yaml", _file_doc("api", [_group(5, "Api", [_bash_test("A.1")])]))
    _write_yaml(test_dir, "02_mcp.yaml", _file_doc("mcp", [_group(16, "Mcp", [_bash_test("M.1")])]))
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


def _row(output: str, test_id: str) -> str:
    rows = [line for line in output.splitlines() if line.strip().startswith(test_id)]
    assert rows, f"no report row for {test_id} in:\n{output}"
    return rows[0]


# ------------------------------------------------------------- first setup file fails


def test_a_failed_first_setup_file_blocks_the_later_setup_file(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, first_cmd="false"), tmp_path)
    row = _row(result.output, "SD.1")
    assert "BLOCKED" in row
    assert "00_auth" in row


def test_a_failed_setup_file_blocks_the_api_and_mcp_layers(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, first_cmd="false"), tmp_path)
    for test_id in ("A.1", "M.1"):
        row = _row(result.output, test_id)
        assert "BLOCKED" in row, row
        assert "00_auth" in row, row


def test_a_failed_setup_file_reds_the_run(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, first_cmd="false"), tmp_path)
    assert result.exit_code == 1, result.output


def test_blocked_files_are_not_counted_as_failures(tmp_path: Path) -> None:
    """One broken bootstrap is ONE thing to read, not four.

    A bash command exiting non-zero is classified ERROR rather than FAILED, so
    the failure half of the count is asserted through the report's own
    ``Failures (n)`` section.
    """
    result = _invoke(_suite(tmp_path, first_cmd="false"), tmp_path)
    assert "Blocked: 3" in result.output
    assert "Failures (1)" in result.output


# ------------------------------------------------------------ later setup file fails


def test_a_failed_later_setup_file_leaves_the_earlier_one_green(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, second_cmd="false"), tmp_path)
    assert "PASS" in _row(result.output, "S.1")


def test_a_failed_later_setup_file_blocks_everything_after_it(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, second_cmd="false"), tmp_path)
    for test_id in ("A.1", "M.1"):
        row = _row(result.output, test_id)
        assert "BLOCKED" in row, row
        assert "00a_seed" in row, row


# ------------------------------------------------------------------- green setup path


def test_a_green_setup_layer_blocks_nothing(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path), tmp_path)
    assert result.exit_code == 0, result.output
    assert "BLOCKED" not in result.output


# ------------------------------------------------------------------ cleanup exception


def test_a_cleanup_failure_in_a_setup_file_blocks_nothing(tmp_path: Path) -> None:
    """A sweep is a backstop, not a provider: the same rule every file already has."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "00_auth.yaml",
        _file_doc(
            "setup",
            [
                _group(1, "Bootstrap", [_bash_test("S.1")]),
                _group(2, "Sweep", [_bash_test("S.9", cmd="false")], cleanup=True),
            ],
        ),
    )
    _write_yaml(test_dir, "01_api.yaml", _file_doc("api", [_group(5, "Api", [_bash_test("A.1")])]))

    result = _invoke(test_dir, tmp_path)
    assert "BLOCKED" not in result.output, result.output
    assert "PASS" in _row(result.output, "A.1")
