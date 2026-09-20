"""CLI integration tests for ``--shard k/n`` (0.10.0).

``--dry-run --shard k/n`` must print the plan so a pipeline author can diff the
subsets before wiring a matrix. Shards run on DISJOINT environments (own database
+ own index prefix); regrun cannot verify that, so it is a documented hard
precondition, stated in the option's help.

Guarantees under test:

  * each shard's plan carries the setup layer plus its own subset.
  * the subsets are disjoint outside setup and together cover every file.
  * a file and its ``requires`` closure stay in one shard.
  * ``1/1`` is the identity plan.
  * an invalid ``k/n`` is a clear error with a non-zero exit.
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


def _file_doc(layer: str, groups: list[dict], **meta: object) -> dict:
    base: dict = {"product": "demo", "layer": layer, "runner": "bash"}
    base.update(meta)
    return {"meta": base, "groups": groups}


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
    _write_yaml(test_dir, "03_alpha.yaml", _file_doc("api", [_group(7, "Alpha", ["A.1"])]))
    _write_yaml(test_dir, "04_beta.yaml", _file_doc("api", [_group(8, "Beta", ["B.1"])]))
    _write_yaml(
        test_dir,
        "05_dispatcher.yaml",
        _file_doc("api", [_group(9, "Dispatcher", ["D.1"])], serial=True),
    )
    return test_dir


def _plan(test_dir: Path, *args: str):
    return CliRunner().invoke(cli, ["run", str(test_dir), "--dry-run", *args])


def _files_in_plan(output: str) -> set[str]:
    return {line.split("File:", 1)[1].strip() for line in output.splitlines() if "File:" in line}


def _shard_files(test_dir: Path, k: int, n: int) -> set[str]:
    result = _plan(test_dir, "--shard", f"{k}/{n}")
    assert result.exit_code == 0, result.output
    return _files_in_plan(result.output)


# ------------------------------------------------------------------------ plan printing


def test_dry_run_prints_the_shard_it_planned(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--shard", "1/3")
    assert result.exit_code == 0, result.output
    assert "1/3" in result.output


def test_every_shard_carries_the_setup_layer(tmp_path: Path) -> None:
    test_dir = _suite(tmp_path)
    for k in (1, 2, 3):
        assert "00_setup.yaml" in _shard_files(test_dir, k, 3)


def test_shards_are_disjoint_outside_setup(tmp_path: Path) -> None:
    test_dir = _suite(tmp_path)
    seen: set[str] = set()
    for k in (1, 2, 3):
        files = _shard_files(test_dir, k, 3) - {"00_setup.yaml"}
        assert not (files & seen), f"shard {k}/3 overlaps an earlier shard"
        seen |= files


def test_shards_together_cover_every_file(tmp_path: Path) -> None:
    test_dir = _suite(tmp_path)
    covered: set[str] = set()
    for k in (1, 2, 3):
        covered |= _shard_files(test_dir, k, 3)
    assert covered == {p.name for p in test_dir.glob("*.yaml")}


def test_a_file_and_its_closure_stay_in_one_shard(tmp_path: Path) -> None:
    test_dir = _suite(tmp_path)
    for k in (1, 2, 3):
        files = _shard_files(test_dir, k, 3)
        if "02_consumer.yaml" in files:
            assert "01_provider.yaml" in files
            return
    raise AssertionError("02_consumer.yaml was not planned into any shard")


def test_serial_file_lands_alone_in_the_last_shard(tmp_path: Path) -> None:
    test_dir = _suite(tmp_path)
    assert _shard_files(test_dir, 3, 3) - {"00_setup.yaml"} == {"05_dispatcher.yaml"}


def test_single_shard_is_the_identity_plan(tmp_path: Path) -> None:
    test_dir = _suite(tmp_path)
    assert _shard_files(test_dir, 1, 1) == {p.name for p in test_dir.glob("*.yaml")}


# ----------------------------------------------------------------------------- errors


def test_shard_index_above_the_count_is_rejected(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--shard", "4/3")
    assert result.exit_code != 0
    assert "4/3" in result.output


def test_zero_shard_index_is_rejected(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--shard", "0/3")
    assert result.exit_code != 0


def test_malformed_shard_spec_is_rejected(tmp_path: Path) -> None:
    result = _plan(_suite(tmp_path), "--shard", "half")
    assert result.exit_code != 0


# ----------------------------------------------------------------------- documentation


def test_help_states_the_disjoint_environment_precondition() -> None:
    result = CliRunner().invoke(cli, ["run", "--help"])
    assert "--shard" in result.output
    assert "disjoint" in result.output.lower()
