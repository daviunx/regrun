"""CLI integration tests: an unsatisfiable ``requires:`` aborts the run.

A ``requires:`` entry naming a file that does not exist -- a typo, or a producer
someone renamed -- used to be dropped silently. The run then went green with
blocked-skip quietly disabled for that dependency, which is the exact invisible
coupling the feature exists to remove. Like an unknown auth profile, it is a
suite defect and fails loudly, before any test executes.

The one tolerated case is the operator's OWN narrowing: ``--file`` / ``--layer``
/ ``--shard`` can legitimately leave a declared producer out of the run, and a
filtered run cannot judge a producer it never loaded.

Guarantees under test:

  * an unknown stem aborts with a non-zero exit, naming the file, the stem and
    the known stems, and executes nothing.
  * a self-reference aborts the same way.
  * a requires cycle aborts instead of running an order no run can satisfy.
  * a producer left out by the operator's own narrowing is tolerated.
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


def _group(group_id: int, name: str, test_ids: list[str]) -> dict:
    return {
        "id": group_id,
        "name": name,
        "priority": "high",
        "tests": [_bash_test(t) for t in test_ids],
    }


def _file_doc(layer: str, groups: list[dict], requires: list[str] | None = None) -> dict:
    meta: dict = {"product": "demo", "layer": layer, "runner": "bash"}
    if requires is not None:
        meta["requires"] = requires
    return {"meta": meta, "groups": groups}


def _suite(tmp_path: Path, requires: list[str]) -> Path:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _file_doc("setup", [_group(1, "Bootstrap", ["S.1"])]))
    _write_yaml(test_dir, "01_provider.yaml", _file_doc("api", [_group(5, "Provider", ["P.1"])]))
    _write_yaml(
        test_dir,
        "02_consumer.yaml",
        _file_doc("api", [_group(6, "Consumer", ["C.1"])], requires=requires),
    )
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


# ----------------------------------------------------------------------- unknown stem


def test_an_unknown_required_stem_aborts_the_run(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, ["99_ghost"]), tmp_path)
    assert result.exit_code != 0, result.output


def test_the_abort_names_the_file_the_stem_and_the_known_stems(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, ["99_ghost"]), tmp_path)
    assert "02_consumer" in result.output
    assert "99_ghost" in result.output
    assert "01_provider" in result.output


def test_nothing_executes_when_a_requires_entry_is_unsatisfiable(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, ["99_ghost"]), tmp_path)
    assert "P.1" not in result.output
    assert "C.1" not in result.output


def test_a_self_reference_aborts_the_run(tmp_path: Path) -> None:
    result = _invoke(_suite(tmp_path, ["02_consumer"]), tmp_path)
    assert result.exit_code != 0
    assert "02_consumer" in result.output


# ------------------------------------------------------------------------------ cycle


def test_a_requires_cycle_aborts_the_run(tmp_path: Path) -> None:
    """A cycle is unrepresentable: running it anyway can only produce a false verdict."""
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_a.yaml",
        _file_doc("api", [_group(5, "A", ["A.1"])], requires=["02_b"]),
    )
    _write_yaml(
        test_dir,
        "02_b.yaml",
        _file_doc("api", [_group(6, "B", ["B.1"])], requires=["01_a"]),
    )
    result = _invoke(test_dir, tmp_path)
    assert result.exit_code != 0, result.output
    assert "cycle" in result.output.lower()
    assert "A.1" not in result.output


# --------------------------------------------------------------- narrowing is tolerated


def test_a_producer_left_out_by_file_selection_is_tolerated(tmp_path: Path) -> None:
    """``--file`` on the consumer alone still pulls its producer, so this is the
    inverse case: selecting the PRODUCER must not fail over the consumer it left out."""
    result = _invoke(_suite(tmp_path, ["01_provider"]), tmp_path, "--file", "01_provider")
    assert result.exit_code == 0, result.output


def test_a_layer_filter_that_leaves_out_a_producer_is_tolerated(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "00_setup.yaml", _file_doc("setup", [_group(1, "Bootstrap", ["S.1"])]))
    _write_yaml(test_dir, "01_provider.yaml", _file_doc("api", [_group(5, "Provider", ["P.1"])]))
    _write_yaml(
        test_dir,
        "02_mcp.yaml",
        _file_doc("mcp", [_group(16, "Mcp", ["M.1"])], requires=["01_provider"]),
    )
    result = _invoke(test_dir, tmp_path, "--layer", "mcp")
    assert result.exit_code == 0, result.output
    assert "M.1" in result.output
