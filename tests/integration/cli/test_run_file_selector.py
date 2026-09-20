"""CLI integration tests for ``--file`` selection (0.10.0).

A developer must be able to run ONE file and trust the result: the run covers the
suite's setup layer, everything the file declares it depends on, and the file
itself — in the normal canonical order — and nothing else.

Guarantees under test:

  * a single stem pulls its ``requires`` closure plus the setup layer, in
    canonical order, and nothing else.
  * glob form and a repeated flag both work.
  * an unknown stem is a clear error with a non-zero exit, never a silent
    zero-file run.
  * ``--file`` composes with ``--group`` and with ``--skip-setup``.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


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
    """setup + provider + consumer(requires provider) + independent + one mcp file."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _file_doc("setup", [_group(1, "Bootstrap", ["S.1"])]))
    _write_yaml(test_dir, "01_provider.yaml", _file_doc("api", [_group(5, "Provider", ["P.1"])]))
    _write_yaml(
        test_dir,
        "02_consumer.yaml",
        _file_doc(
            "api",
            [_group(6, "Consumer", ["C.1"]), _group(7, "Consumer Extra", ["C.2"])],
            requires=["01_provider"],
        ),
    )
    _write_yaml(
        test_dir, "03_independent.yaml", _file_doc("api", [_group(8, "Independent", ["I.1"])])
    )
    _write_yaml(test_dir, "04_mcp_tail.yaml", _file_doc("mcp", [_group(16, "Mcp", ["M.1"])]))
    return test_dir


def _plan(test_dir: Path, *args: str):
    return CliRunner().invoke(cli, ["run", str(test_dir), "--dry-run", *args])


def _files_in_plan(output: str) -> list[str]:
    return [line.split("File:", 1)[1].strip() for line in output.splitlines() if "File:" in line]


# ------------------------------------------------------------------------ single stem


def test_single_stem_pulls_setup_and_closure_in_canonical_order(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "02_consumer")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == [
        "00_setup.yaml",
        "01_provider.yaml",
        "02_consumer.yaml",
    ]


def test_single_stem_excludes_unrelated_files(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "02_consumer")
    assert result.exit_code == 0, result.output
    plan = _files_in_plan(result.output)
    assert plan, result.output
    assert "03_independent.yaml" not in plan
    assert "04_mcp_tail.yaml" not in plan


def test_an_independent_stem_pulls_only_setup(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "03_independent")
    assert _files_in_plan(result.output) == ["00_setup.yaml", "03_independent.yaml"]


def test_a_setup_stem_selects_only_the_setup_layer(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "00_setup")
    assert _files_in_plan(result.output) == ["00_setup.yaml"]


# ------------------------------------------------------------------------ glob/repeat


def test_glob_form_selects_every_matching_stem(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "0[13]_*")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == [
        "00_setup.yaml",
        "01_provider.yaml",
        "03_independent.yaml",
    ]


def test_glob_still_pulls_the_closure_of_each_match(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "02_*")
    assert _files_in_plan(result.output) == [
        "00_setup.yaml",
        "01_provider.yaml",
        "02_consumer.yaml",
    ]


def test_the_flag_is_repeatable(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "03_independent", "--file", "04_mcp_tail")
    assert _files_in_plan(result.output) == [
        "00_setup.yaml",
        "03_independent.yaml",
        "04_mcp_tail.yaml",
    ]


# ----------------------------------------------------------------------------- errors


def test_unknown_stem_is_a_clear_error(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "99_nope")
    assert result.exit_code != 0
    assert "99_nope" in result.output


def test_a_glob_matching_nothing_is_a_clear_error(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "9*_nope")
    assert result.exit_code != 0
    assert "9*_nope" in result.output


# -------------------------------------------------------------------------- composition


def test_file_composes_with_group(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "02_consumer", "--group", "6")
    assert result.exit_code == 0, result.output
    assert "Group 6:" in result.output
    assert "Group 7:" not in result.output
    assert "01_provider.yaml" in _files_in_plan(result.output)


def test_file_composes_with_skip_setup(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "02_consumer", "--skip-setup")
    assert _files_in_plan(result.output) == ["01_provider.yaml", "02_consumer.yaml"]


def test_file_selection_is_reported_on_a_real_run(tmp_path: Path) -> None:
    """FR-2: the report must say which files ran."""
    test_dir = _suite(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["run", str(test_dir), "--file", "03_independent"],
        env={
            "REGRUN_RUNS_DIR": str(tmp_path / "runs"),
            "REGRUN_LOCK_TARGET": "demotarget",
        },
    )
    assert result.exit_code == 0, result.output
    assert "00_setup" in result.output
    assert "03_independent" in result.output
    assert "P.1" not in result.output
