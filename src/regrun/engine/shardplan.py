"""Deterministic suite sharding: split a suite into n subsets that can run at once.

The unit of sharding is a CONNECTED COMPONENT of the non-setup dependency graph,
never a single file: a file and everything its ``requires`` closure names have to
land together, or a shard runs a consumer whose producer is in another shard.
Setup runs in every shard, because every file depends on it.

Balance is greedy longest-processing-time over per-unit weights. With measured
timings the shards come out within the heaviest unit of each other; with no
timings the fallback weight is the file's test count, which is a poor proxy for
duration but a much better one than nothing.

Everything is deterministic and independent of input order: units sort by weight
descending then by run order (``ordering``) ascending, and an equal-weight tie
goes to the lowest shard index. A pipeline author can therefore diff two plans
and trust the difference.

SHARDING REQUIRES DISJOINT ENVIRONMENTS — each shard needs its own database and
its own index prefix. Two shards against one stack will cross-contaminate each
other's fixtures. regrun cannot verify this; it is a hard precondition of using
the feature.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from regrun.engine.depgraph import FileNode
from regrun.engine.ordering import run_order_key

__all__ = ["Shard", "ShardSpecError", "parse_shard_spec", "plan_shards"]


def _order_key_of(nodes: list[FileNode]) -> Callable[[str], tuple[int, str]]:
    """A run-order key function for these nodes' stems, from the one primitive.

    Every stem list this module hands out is in the order the runner will execute
    it, so a printed plan can be read against a report line by line.
    """
    layers = {node.stem: node.layer for node in nodes}
    return lambda stem: run_order_key(stem, layers[stem])


class ShardSpecError(ValueError):
    """The ``--shard`` argument is not a valid ``k/n``, or ``n`` is not positive."""


@dataclass(frozen=True)
class Shard:
    """One shard of a planned split.

    ``stems`` is in the canonical run order (``ordering``): the setup layer first,
    then the shard's own files.
    ``weight`` covers the shard's NON-setup units only: setup runs everywhere, so
    it is not part of what the balance is trying to even out.
    """

    index: int
    total: int
    stems: list[str] = field(default_factory=list)
    weight: float = 0.0


@dataclass
class _Unit:
    """A group of files that must run together, with its combined weight.

    ``key`` is the run-order key of the unit's EARLIEST file, which is both its
    canonical name and the tie-break that keeps a plan from wobbling.
    """

    stems: list[str]
    weight: float
    serial: bool
    key: tuple[int, str]


def parse_shard_spec(spec: str) -> tuple[int, int]:
    """Parse and validate a ``k/n`` shard argument into ``(k, n)``."""
    parts = spec.split("/")
    if len(parts) != 2:
        raise ShardSpecError(f"invalid shard spec '{spec}': expected the form k/n")
    try:
        index, total = int(parts[0]), int(parts[1])
    except ValueError:
        raise ShardSpecError(f"invalid shard spec '{spec}': k and n must be integers")
    if total < 1 or index < 1 or index > total:
        raise ShardSpecError(f"invalid shard spec '{spec}': need 1 <= k <= n and n >= 1")
    return index, total


def _components(nodes: list[FileNode]) -> list[list[str]]:
    """Connected components of the non-setup dependency graph, as stem lists.

    Edges are treated as UNDIRECTED: coupling is symmetric for the purpose of
    "must these files run together", regardless of which one declared it.
    """
    order_key = _order_key_of(nodes)
    stems = sorted((node.stem for node in nodes), key=order_key)
    parent = {stem: stem for stem in stems}

    def find(stem: str) -> str:
        while parent[stem] != stem:
            parent[stem] = parent[parent[stem]]
            stem = parent[stem]
        return stem

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            # Always attach the larger root to the smaller one, so the
            # representative is order-independent.
            low, high = sorted((left_root, right_root))
            parent[high] = low

    for node in sorted(nodes, key=lambda n: n.stem):
        for required in sorted(node.requires):
            if required in parent:
                union(node.stem, required)

    groups: dict[str, list[str]] = {}
    for stem in stems:
        groups.setdefault(find(stem), []).append(stem)
    members_in_order = [sorted(members, key=order_key) for members in groups.values()]
    return sorted(members_in_order, key=lambda members: order_key(members[0]))


def _build_units(nodes: list[FileNode], weights: dict[str, float] | None) -> list[_Unit]:
    """Group non-setup files into coupled units, sorted heaviest first."""
    non_setup = [node for node in nodes if not node.is_setup]
    by_stem = {node.stem: node for node in non_setup}

    def weight_of(stem: str) -> float:
        if weights is not None and stem in weights:
            return weights[stem]
        return float(by_stem[stem].test_count)

    order_key = _order_key_of(non_setup)
    units = [
        _Unit(
            stems=members,
            weight=sum(weight_of(stem) for stem in members),
            serial=any(by_stem[stem].serial for stem in members),
            key=order_key(members[0]),
        )
        for members in _components(non_setup)
    ]
    units.sort(key=lambda unit: (-unit.weight, unit.key))
    return units


def _assign(units: list[_Unit], slots: int) -> list[list[_Unit]]:
    """Greedy longest-processing-time assignment into ``slots`` buckets.

    Units arrive heaviest first; each goes to the lightest bucket, and an
    equal-weight tie goes to the LOWEST index so the plan never wobbles.
    """
    buckets: list[list[_Unit]] = [[] for _ in range(slots)]
    totals = [0.0] * slots
    for unit in units:
        target = min(range(slots), key=lambda index: (totals[index], index))
        buckets[target].append(unit)
        totals[target] += unit.weight
    return buckets


def plan_shards(
    nodes: list[FileNode],
    total: int,
    weights: dict[str, float] | None = None,
) -> list[Shard]:
    """Split ``nodes`` into ``total`` shards. See the module docstring for the rules.

    ``weights`` maps a stem to its measured duration; a stem it omits falls back
    to that file's test count.
    """
    if total < 1:
        raise ShardSpecError(f"invalid shard count {total}: must be >= 1")

    order_key = _order_key_of(nodes)
    setup_stems = sorted((node.stem for node in nodes if node.is_setup), key=order_key)
    units = _build_units(nodes, weights)

    if total == 1:
        buckets = [units]
    else:
        # A serial unit asserts process-global behaviour, so it gets the last
        # shard to itself rather than sharing one with unrelated traffic.
        serial = [unit for unit in units if unit.serial]
        parallel = [unit for unit in units if not unit.serial]
        buckets = _assign(parallel, total - 1) if serial else _assign(parallel, total)
        if serial:
            buckets.append(serial)

    shards: list[Shard] = []
    for index, bucket in enumerate(buckets, start=1):
        stems = sorted((stem for unit in bucket for stem in unit.stems), key=order_key)
        shards.append(
            Shard(
                index=index,
                total=total,
                stems=setup_stems + stems,
                weight=sum(unit.weight for unit in bucket),
            )
        )
    return shards
