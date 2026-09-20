"""CLI integration tests for ``--file`` selection (0.10.0).

A developer must be able to run ONE file and trust the result: the run covers the
suite's setup layer, everything the file declares it depends on, and the file
itself — in the normal canonical order — and nothing else.

Guarantees under test:

  * a single stem pulls its ``requires`` closure plus the setup layer, in
    canonical order, and nothing else.
  * a stem in the SETUP layer pulls the setup files sorting before it, so a file
    that reads a variable declared in the first setup file can run alone.
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


def test_a_setup_stem_pulls_no_non_setup_file(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--file", "00_setup")
    assert _files_in_plan(result.output) == ["00_setup.yaml"]


# ------------------------------------------------------------------------ setup layer


def _setup_layer_suite(tmp_path: Path) -> Path:
    """Three ordered setup files (the first owns the suite's variables) plus one api file.

    Mirrors the real shape: ``variables:`` is single-homed in the first setup
    file and a later setup file reads it, so selecting the later file alone can
    only work if the earlier one comes with it.
    """
    test_dir = tmp_path / "setup_suite"
    test_dir.mkdir()
    first = _file_doc("setup", [_group(1, "Bootstrap", ["S.1"])])
    first["variables"] = {"SEED_TOKEN": "token-value"}
    _write_yaml(test_dir, "00_setup.yaml", first)

    seed = _file_doc("setup", [_group(2, "Seed", ["S.2"])])
    seed["groups"][0]["tests"][0]["commands"] = [{"cmd": "test -n '{{SEED_TOKEN}}'"}]
    _write_yaml(test_dir, "00g_seed.yaml", seed)

    _write_yaml(test_dir, "00z_last.yaml", _file_doc("setup", [_group(3, "Last", ["S.3"])]))
    _write_yaml(test_dir, "01_api.yaml", _file_doc("api", [_group(5, "Api", ["A.1"])]))
    return test_dir


def test_a_setup_stem_pulls_the_earlier_setup_files(tmp_path: Path) -> None:
    result = _plan(_setup_layer_suite(tmp_path), "--file", "00g_seed")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == ["00_setup.yaml", "00g_seed.yaml"]


def test_a_setup_stem_excludes_the_later_setup_files(tmp_path: Path) -> None:
    result = _plan(_setup_layer_suite(tmp_path), "--file", "00g_seed")
    plan = _files_in_plan(result.output)
    assert plan, result.output
    assert "00z_last.yaml" not in plan
    assert "01_api.yaml" not in plan


def test_the_first_setup_stem_still_selects_only_itself(tmp_path: Path) -> None:
    result = _plan(_setup_layer_suite(tmp_path), "--file", "00_setup")
    assert _files_in_plan(result.output) == ["00_setup.yaml"]


def test_a_glob_matching_several_setup_files_pulls_each_prefix(tmp_path: Path) -> None:
    result = _plan(_setup_layer_suite(tmp_path), "--file", "00[gz]_*")
    assert result.exit_code == 0, result.output
    assert _files_in_plan(result.output) == ["00_setup.yaml", "00g_seed.yaml", "00z_last.yaml"]


def test_a_setup_stem_with_skip_setup_is_a_clear_error(tmp_path: Path) -> None:
    """``--skip-setup`` removes the whole layer, so the pattern can match nothing."""
    result = _plan(_setup_layer_suite(tmp_path), "--file", "00g_seed", "--skip-setup")
    assert result.exit_code != 0
    assert "00g_seed" in result.output


def test_a_setup_stem_runs_with_a_variable_from_the_first_setup_file(tmp_path: Path) -> None:
    """The defect this guarantee exists for: the run must resolve the suite variable."""
    test_dir = _setup_layer_suite(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["run", str(test_dir), "--file", "00g_seed"],
        env={
            "REGRUN_RUNS_DIR": str(tmp_path / "runs"),
            "REGRUN_LOCK_TARGET": "demotarget",
        },
    )
    assert result.exit_code == 0, result.output
    assert "S.2" in result.output
    assert "SEED_TOKEN" not in result.output
    assert "S.3" not in result.output


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
