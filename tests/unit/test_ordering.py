"""The run order is ONE order, and every consumer of it agrees.

``engine/ordering.py`` defines the order suite files run in. This file pins the
contract itself and then asserts that every part of the engine deciding
something on the strength of that order returns the SAME sequence for the same
files: the file discovery the runner executes, the dependency graph, the shard
planner and the linter's ordering-dependent rules.

The fixture set is deliberately punctuated and mixed-case, because that is where
two independent sort keys diverge: a key built from a filename compares the
constant ``.yaml`` suffix against real characters, so it disagrees with a key
built from the stem the moment one name is a prefix of another. An ordinary
suite (digits, letters, underscores) cannot show the difference, which is why
nothing caught it.
"""

from pathlib import Path

import pytest
import yaml

from regrun.engine import depgraph, ordering, selection, shardplan
from regrun.engine.lint_rules import variables as lint_variables

# One layer, so the fixture exercises the NAME half of the key. Byte order:
# '.' (0x2E) < 'B' (0x42) < '_' (0x5F) < 'a' (0x61), and a name that is a prefix
# of another comes first because the extension is not part of the comparison.
#
# The two cased names differ by more than their case ON PURPOSE: a suite holding
# both "00a_x.yaml" and "00A_x.yaml" cannot exist on a case-insensitive
# filesystem, so a fixture pairing them would be five files on one platform and
# four on another. Case ordering is pinned on the key alone, below.
PUNCTUATED = ["00a_x", "00_setup-extra", "00.b", "00_setup", "00B_x"]
EXPECTED = ["00.b", "00B_x", "00_setup", "00_setup-extra", "00a_x"]

LAYER = "api"


def _doc(stem: str) -> dict:
    return {
        "meta": {"product": "demo", "layer": LAYER, "runner": "bash"},
        "groups": [
            {
                "id": 1 + PUNCTUATED.index(stem),
                "name": f"group {stem}",
                "priority": "high",
                "tests": [
                    {
                        "id": f"T.{stem}",
                        "name": f"test {stem}",
                        "commands": [{"cmd": "true"}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            }
        ],
    }


@pytest.fixture
def suite_dir(tmp_path: Path) -> Path:
    """The punctuated fixture set on disk, written in a deliberately wrong order."""
    for stem in PUNCTUATED:
        (tmp_path / f"{stem}.yaml").write_text(yaml.safe_dump(_doc(stem), sort_keys=False))
    return tmp_path


def _nodes() -> list[depgraph.FileNode]:
    return [depgraph.FileNode(stem=stem, layer=LAYER, test_count=1) for stem in PUNCTUATED]


def _parsed(suite_dir: Path) -> list[tuple[Path, dict, str]]:
    return [
        (path, yaml.safe_load(path.read_text()), path.read_text())
        for path in suite_dir.glob("*.yaml")
    ]


# ------------------------------------------------------------------- the key itself


def test_the_key_orders_a_punctuated_set_as_documented() -> None:
    ordered = sorted(PUNCTUATED, key=lambda stem: ordering.run_order_key(stem, LAYER))
    assert ordered == EXPECTED


def test_a_prefix_name_runs_before_the_name_that_extends_it() -> None:
    """The documented consequence of excluding the extension from the key."""
    assert ordering.run_order_key("00_setup", LAYER) < ordering.run_order_key(
        "00_setup-extra", LAYER
    )


def test_case_is_never_folded() -> None:
    """Byte order, so uppercase precedes lowercase and no locale can move a suite."""
    assert ordering.run_order_key("00A_x", LAYER) < ordering.run_order_key("00a_x", LAYER)


def test_layer_rank_outranks_the_name() -> None:
    assert ordering.run_order_key("99_zzz", "setup") < ordering.run_order_key("00_aaa", "api")


def test_an_unknown_layer_sorts_after_every_known_one() -> None:
    assert ordering.layer_rank("nonsense") > max(
        ordering.layer_rank(x) for x in ordering.LAYER_ORDER
    )


def test_alphanumeric_stems_are_unaffected_by_the_key_choice() -> None:
    """The constraint on the whole change: an ordinary suite's order cannot move."""
    ordinary = ["00_setup", "01_api_surface", "02_mcp_surface", "16x_mcp_tail", "17_cleanup"]
    by_stem = sorted(ordinary, key=lambda stem: ordering.run_order_key(stem, LAYER))
    by_filename = sorted(ordinary, key=lambda stem: f"{stem}.yaml")
    assert by_stem == by_filename == ordinary


# ----------------------------------------------------------------- every consumer


def _discovery(suite_dir: Path) -> list[str]:
    """What the runner will execute, in order."""
    return [path.stem for path in selection.discover_yaml_files(suite_dir, None)]


def _graph_order(suite_dir: Path) -> list[str]:
    """What the dependency graph iterates and reports."""
    return depgraph.build(_nodes()).order


def _shard_plan(suite_dir: Path) -> list[str]:
    """What a single-shard plan says will run."""
    return shardplan.plan_shards(_nodes(), 1)[0].stems


def _lint_order(suite_dir: Path) -> list[str]:
    """The positions the linter's ordering-dependent rules judge against."""
    index = lint_variables._order_index(_parsed(suite_dir))
    return sorted(index, key=lambda stem: index[stem])


CONSUMERS = {
    "discovery": _discovery,
    "graph_order": _graph_order,
    "shard_plan": _shard_plan,
    "lint_order": _lint_order,
}


@pytest.mark.parametrize("consumer", sorted(CONSUMERS))
def test_every_consumer_yields_the_canonical_order(consumer: str, suite_dir: Path) -> None:
    assert CONSUMERS[consumer](suite_dir) == EXPECTED


def test_the_consumers_agree_with_each_other(suite_dir: Path) -> None:
    """Stated separately: the failure being guarded is DIVERGENCE, not a bad literal."""
    answers = {name: fn(suite_dir) for name, fn in CONSUMERS.items()}
    assert len(set(map(tuple, answers.values()))) == 1, answers


# -------------------------------------------------------- setup prefix, same order


def test_the_setup_prefix_follows_the_same_order() -> None:
    """``selection_closure`` must cut the setup layer where the runner splits it."""
    nodes = [
        depgraph.FileNode(stem=stem, layer="setup", test_count=1)
        for stem in ["00_setup", "00_setup-extra", "00a_x"]
    ]
    graph = depgraph.build(nodes)
    assert graph.setup_stems == ("00_setup", "00_setup-extra", "00a_x")
    assert depgraph.selection_closure(graph, "00_setup") == set()
    assert depgraph.selection_closure(graph, "00_setup-extra") == {"00_setup"}
    assert depgraph.selection_closure(graph, "00a_x") == {"00_setup", "00_setup-extra"}
