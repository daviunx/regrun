"""Unit tests for ``engine/shardplan.py`` — deterministic suite sharding (0.10.0).

Pure functions over in-memory nodes; no files, no network, no mocks.

Contract:

  * ``parse_shard_spec("k/n")`` parses and VALIDATES the ``--shard`` argument.
  * ``plan_shards(nodes, n, weights=None)`` returns ``n`` ``Shard`` objects.
    Units are connected components of the NON-setup dependency graph, so a file
    and its ``requires`` closure never split across shards. Setup files run in
    EVERY shard. ``serial: true`` units land in the LAST shard only, and when
    ``n > 1`` that shard takes nothing else.
  * Balance is greedy LPT by supplied weights; with no weights the fallback
    weight is the file's test count. ``Shard.weight`` covers the shard's non-setup
    units only (setup runs everywhere, so it is not part of the balance).
  * The plan is deterministic: the same node set yields the same plan regardless
    of input order. Units sort by weight desc then stem asc, and an equal-weight
    shard tie goes to the LOWEST shard index.

Sharding requires DISJOINT environments (own database + own index prefix) per
shard. regrun cannot verify that; it is a documented hard precondition.
"""

import pytest

from regrun.engine import shardplan
from regrun.engine.depgraph import FileNode


def _node(
    stem: str,
    layer: str = "api",
    requires: tuple[str, ...] = (),
    serial: bool = False,
    test_count: int = 1,
) -> FileNode:
    return FileNode(
        stem=stem,
        layer=layer,
        requires=list(requires),
        serial=serial,
        test_count=test_count,
    )


def _suite() -> list[FileNode]:
    """One setup file, a two-file coupled unit, and three independent files."""
    return [
        _node("00_setup", layer="setup"),
        _node("01_provider"),
        _node("02_consumer", requires=("01_provider",)),
        _node("03_alpha"),
        _node("04_beta"),
        _node("05_gamma"),
    ]


def _non_setup(shard: shardplan.Shard) -> set[str]:
    return {stem for stem in shard.stems if stem != "00_setup"}


# -------------------------------------------------------------------- parse_shard_spec


def test_parse_shard_spec_reads_k_and_n() -> None:
    assert shardplan.parse_shard_spec("2/3") == (2, 3)


def test_parse_shard_spec_accepts_the_single_shard_identity() -> None:
    assert shardplan.parse_shard_spec("1/1") == (1, 1)


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1/0", "-1/3", "x/3", "3", "1/2/3", ""])
def test_parse_shard_spec_rejects_invalid_specs(spec: str) -> None:
    with pytest.raises(shardplan.ShardSpecError):
        shardplan.parse_shard_spec(spec)


# ------------------------------------------------------------------ coverage/disjointness


def test_plan_returns_one_shard_per_requested_part() -> None:
    shards = shardplan.plan_shards(_suite(), 3)
    assert [s.index for s in shards] == [1, 2, 3]
    assert all(s.total == 3 for s in shards)


def test_shards_are_disjoint_outside_setup() -> None:
    shards = shardplan.plan_shards(_suite(), 3)
    seen: set[str] = set()
    for shard in shards:
        stems = _non_setup(shard)
        assert not (stems & seen), f"shard {shard.index} overlaps an earlier shard"
        seen |= stems


def test_shards_together_cover_every_file() -> None:
    shards = shardplan.plan_shards(_suite(), 3)
    covered: set[str] = set()
    for shard in shards:
        covered |= set(shard.stems)
    assert covered == {node.stem for node in _suite()}


def test_setup_files_run_in_every_shard() -> None:
    shards = shardplan.plan_shards(_suite(), 3)
    assert all("00_setup" in shard.stems for shard in shards)


def test_a_file_and_its_closure_land_in_the_same_shard() -> None:
    shards = shardplan.plan_shards(_suite(), 3)
    for shard in shards:
        stems = _non_setup(shard)
        if "02_consumer" in stems:
            assert "01_provider" in stems
            break
    else:
        raise AssertionError("02_consumer was not assigned to any shard")


def test_stems_are_ordered_setup_first_then_by_stem() -> None:
    shards = shardplan.plan_shards(_suite(), 1)
    assert shards[0].stems == [
        "00_setup",
        "01_provider",
        "02_consumer",
        "03_alpha",
        "04_beta",
        "05_gamma",
    ]


def test_single_shard_is_the_identity_plan() -> None:
    shards = shardplan.plan_shards(_suite(), 1)
    assert len(shards) == 1
    assert set(shards[0].stems) == {node.stem for node in _suite()}


def test_plan_rejects_a_non_positive_shard_count() -> None:
    with pytest.raises(shardplan.ShardSpecError):
        shardplan.plan_shards(_suite(), 0)


# ---------------------------------------------------------------------- serial isolation


def _serial_suite() -> list[FileNode]:
    nodes = _suite()
    nodes.append(_node("06_dispatcher", serial=True))
    return nodes


def test_serial_units_land_in_the_last_shard() -> None:
    shards = shardplan.plan_shards(_serial_suite(), 3)
    assert "06_dispatcher" in shards[-1].stems


def test_the_serial_shard_takes_nothing_else() -> None:
    shards = shardplan.plan_shards(_serial_suite(), 3)
    assert _non_setup(shards[-1]) == {"06_dispatcher"}


def test_serial_file_shares_the_only_shard_when_n_is_one() -> None:
    shards = shardplan.plan_shards(_serial_suite(), 1)
    assert "06_dispatcher" in shards[0].stems
    assert "03_alpha" in shards[0].stems


def test_parallel_shards_still_cover_everything_with_a_serial_unit() -> None:
    shards = shardplan.plan_shards(_serial_suite(), 3)
    covered: set[str] = set()
    for shard in shards:
        covered |= _non_setup(shard)
    assert covered == {node.stem for node in _serial_suite()} - {"00_setup"}


# ------------------------------------------------------------------------------ balance


def test_lpt_balances_by_supplied_durations() -> None:
    """Weights 8/4/4/2/2 over 2 shards: a perfect 10/10 split exists and LPT finds it."""
    nodes = [
        _node("00_setup", layer="setup"),
        _node("01_heavy"),
        _node("02_mid"),
        _node("03_mid"),
        _node("04_light"),
        _node("05_light"),
    ]
    weights = {
        "01_heavy": 8.0,
        "02_mid": 4.0,
        "03_mid": 4.0,
        "04_light": 2.0,
        "05_light": 2.0,
    }
    shards = shardplan.plan_shards(nodes, 2, weights=weights)
    totals = sorted(shard.weight for shard in shards)
    assert totals == [10.0, 10.0]


def test_lpt_keeps_shards_within_the_heaviest_unit_of_each_other() -> None:
    nodes = [_node("00_setup", layer="setup")] + [_node(f"{i:02d}_file") for i in range(1, 10)]
    weights = {node.stem: float(i) for i, node in enumerate(nodes[1:], start=1)}
    shards = shardplan.plan_shards(nodes, 3, weights=weights)
    totals = [shard.weight for shard in shards]
    assert max(totals) - min(totals) <= max(weights.values())


def test_fallback_weight_is_the_test_count() -> None:
    """No timings: a 10-test file must not share a shard with the other 10 tests."""
    nodes = [
        _node("00_setup", layer="setup"),
        _node("01_big", test_count=10),
        _node("02_small", test_count=5),
        _node("03_small", test_count=5),
    ]
    shards = shardplan.plan_shards(nodes, 2)
    assert sorted(shard.weight for shard in shards) == [10.0, 10.0]


def test_a_unit_weight_is_the_sum_of_its_files() -> None:
    nodes = [
        _node("00_setup", layer="setup"),
        _node("01_provider", test_count=3),
        _node("02_consumer", requires=("01_provider",), test_count=4),
    ]
    shards = shardplan.plan_shards(nodes, 2)
    assert sorted(shard.weight for shard in shards) == [0.0, 7.0]


# ------------------------------------------------------------------------- determinism


def _membership(shards: list[shardplan.Shard]) -> list[set[str]]:
    return [_non_setup(shard) for shard in shards]


def test_plan_is_stable_across_calls() -> None:
    assert _membership(shardplan.plan_shards(_suite(), 3)) == _membership(
        shardplan.plan_shards(_suite(), 3)
    )


def test_plan_is_independent_of_input_order() -> None:
    forward = shardplan.plan_shards(_suite(), 3)
    reversed_nodes = list(reversed(_suite()))
    assert _membership(shardplan.plan_shards(reversed_nodes, 3)) == _membership(forward)


def test_equal_weight_ties_are_broken_by_stem() -> None:
    nodes = [_node("00_setup", layer="setup")] + [_node(f"{i:02d}_file") for i in range(1, 5)]
    shards = shardplan.plan_shards(nodes, 2)
    assert _membership(shards) == [{"01_file", "03_file"}, {"02_file", "04_file"}]
