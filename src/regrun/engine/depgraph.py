"""The file dependency graph: who needs whom, and is the declaration sane.

Pure functions over in-memory nodes. Nothing here reads a file, opens a socket
or knows what a test result is; the executor, the linter and the shard planner
all ask this module the same questions and get the same answers.

Two kinds of dependency exist:

* DECLARED — ``meta.requires: [stem, ...]``, a file naming the files that
  produce values it consumes.
* IMPLICIT — every ``layer: setup`` file is a dependency of every non-setup
  file. Setup is the bootstrap contract nobody declares. Setup files carry no
  implicit dependency of their own, so setup ordering can never surface as a
  graph cycle. "A failed setup file blocks every later file" therefore cannot be
  an edge here: it is the setup gate in ``blocked.BlockedTracker``, applied by
  the executor to every file that follows a failed setup file, and the only
  mechanism covering one setup file blocking the NEXT one.

Selecting a file to RUN is a wider question than blocking, and ``requires`` plus
the implicit rule above does not answer it for a setup file: the setup layer is
ordered and single-homed, so a later setup file reads what an earlier one
declared. ``selection_closure`` adds that ordered prefix, without making it an
edge.

Every result is deterministic and independent of input order: a graph built
from a shuffled node list answers identically, so a report or a shard plan
never moves for a reason nobody can see. Two kinds of sorting live here and
they are not the same thing: anything answering "which file comes first" goes
through ``ordering``, while the plain stem sorts inside the traversals exist
only to make walking a SET reproducible and carry no run-order meaning.
"""

from dataclasses import dataclass, field

# The run order itself lives in ``ordering``, the one place it is defined. It is
# re-exported here because the graph is where most callers meet it.
from regrun.engine.ordering import LAYER_ORDER, run_order_key

__all__ = [
    "LAYER_ORDER",
    "FileNode",
    "Graph",
    "SelfDependencyError",
    "UnknownStemError",
    "build",
    "closure",
    "detect_cycles",
    "selection_closure",
    "validate_order",
]


class DepGraphError(Exception):
    """Base class for a malformed dependency declaration."""


class UnknownStemError(DepGraphError):
    """A ``requires:`` entry, or a queried stem, names no file in the suite."""


class SelfDependencyError(DepGraphError):
    """A file lists itself in its own ``requires:``."""


@dataclass(frozen=True)
class FileNode:
    """One suite file, reduced to the facts the graph and the planner need."""

    stem: str
    layer: str
    requires: list[str] = field(default_factory=list)
    serial: bool = False
    test_count: int = 0

    @property
    def is_setup(self) -> bool:
        return self.layer == "setup"


@dataclass(frozen=True)
class Graph:
    """Resolved dependencies for a whole suite.

    ``deps[stem]`` is the DIRECT dependency set of ``stem``, declared plus
    implicit. ``order`` is the stems in CANONICAL RUN ORDER (``ordering``), which
    is what every deterministic traversal here iterates, and ``setup_stems`` is
    the setup layer in that same order.
    """

    nodes: dict[str, FileNode]
    deps: dict[str, set[str]]
    setup_stems: tuple[str, ...]

    @property
    def order(self) -> list[str]:
        return sorted(self.nodes, key=self.key_of)

    def key_of(self, stem: str) -> tuple[int, str]:
        """Run-order key of one stem, from the engine's single ordering primitive."""
        return run_order_key(stem, self.nodes[stem].layer)


def build(nodes: list[FileNode]) -> Graph:
    """Resolve declared plus implicit dependencies for every node.

    Raises ``UnknownStemError`` for a ``requires:`` entry naming no file in the
    suite, and ``SelfDependencyError`` for a file requiring itself. Cycles are
    NOT rejected here: ``detect_cycles`` reports them so the linter can render
    them as findings against the files that declared them.
    """
    by_stem = {node.stem: node for node in nodes}
    setup_stems = tuple(
        sorted(
            (node.stem for node in nodes if node.is_setup),
            key=lambda stem: run_order_key(stem, "setup"),
        )
    )

    deps: dict[str, set[str]] = {}
    for node in nodes:
        resolved: set[str] = set()
        for required in node.requires:
            if required == node.stem:
                raise SelfDependencyError(f"{node.stem} requires itself")
            if required not in by_stem:
                raise UnknownStemError(f"{node.stem} requires unknown file '{required}'")
            resolved.add(required)
        if not node.is_setup:
            resolved.update(setup_stems)
        deps[node.stem] = resolved

    return Graph(nodes=by_stem, deps=deps, setup_stems=setup_stems)


def closure(graph: Graph, stem: str) -> set[str]:
    """The TRANSITIVE dependency set of ``stem``, excluding ``stem`` itself.

    Safe on a cyclic graph: already-visited stems are not revisited.
    """
    if stem not in graph.nodes:
        raise UnknownStemError(f"unknown file '{stem}'")

    seen: set[str] = set()
    pending = sorted(graph.deps.get(stem, set()))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(sorted(graph.deps.get(current, set())))
    seen.discard(stem)
    return seen


def selection_closure(graph: Graph, stem: str) -> set[str]:
    """Everything that has to RUN for ``stem`` to be runnable on its own.

    ``closure`` answers the BLOCKING question (whose failure invalidates this
    file), and a setup file has no producer to be invalidated by. Selecting a
    file to run asks a wider question, because the setup layer is ORDERED and
    single-homed: the first setup file owns the suite's ``variables:`` and
    ``meta.env_file``, and every setup file after it may read them. So a setup
    stem also pulls every setup file SORTING BEFORE IT, plus those files' own
    declared closures.

    "Before" is the engine's one run order (``ordering.run_order_key``), never a
    comparison of its own: a selection that ordered files differently from the
    runner would pull a producer the full suite runs afterwards, or drop one it
    runs first, and only punctuated names would show it.

    A LATER setup file is never pulled: nothing it produces can have existed when
    the selected file ran in a full suite, so needing it would be a defect rather
    than a dependency. For a non-setup stem the answer is exactly ``closure``,
    which already carries the whole setup layer.

    The prefix is deliberately NOT a graph edge. Blocking is unchanged (a failed
    setup file already blocks every later file through the setup gate), shard
    planning is unchanged (setup runs in every shard), and the graph keeps its
    guarantee that setup ordering can never surface as a cycle.
    """
    if stem not in graph.nodes:
        raise UnknownStemError(f"unknown file '{stem}'")

    selected = closure(graph, stem)
    if not graph.nodes[stem].is_setup:
        return selected

    boundary = graph.key_of(stem)
    for earlier in graph.setup_stems:
        if graph.key_of(earlier) >= boundary:
            continue
        selected.add(earlier)
        selected |= closure(graph, earlier)
    selected.discard(stem)
    return selected


def _normalize_cycle(path: list[str]) -> tuple[str, ...]:
    """Rotate a cycle to start at its lowest stem, so one cycle has one spelling."""
    pivot = path.index(min(path))
    return tuple(path[pivot:] + path[:pivot])


def detect_cycles(graph: Graph) -> list[list[str]]:
    """Report every dependency cycle. Does NOT raise.

    A cycle is a suite defect the linter surfaces as a finding on the files that
    declared it, which is more useful than an exception at load time — the
    operator wants to be told which files, not merely that something is wrong.
    """
    found: set[tuple[str, ...]] = set()
    visited: set[str] = set()

    def walk(stem: str, path: list[str], on_path: set[str]) -> None:
        for required in sorted(graph.deps.get(stem, set())):
            if required in on_path:
                found.add(_normalize_cycle(path[path.index(required) :]))
                continue
            if required in visited:
                continue
            walk(required, [*path, required], on_path | {required})
        visited.add(stem)

    for stem in graph.order:
        if stem not in visited:
            walk(stem, [stem], {stem})

    return [list(cycle) for cycle in sorted(found)]


def validate_order(graph: Graph, order: list[str]) -> list[tuple[str, str]]:
    """Report ``(dependent, required)`` pairs where the producer runs too late.

    ``order`` is the canonical run order. A dependency that sorts AFTER its
    dependent can never have produced anything by the time it is needed, so the
    declaration is a suite defect no matter how the run is filtered. Stems absent
    from ``order`` are skipped: a filtered run cannot judge them.
    """
    rank = {stem: index for index, stem in enumerate(order)}
    violations: list[tuple[str, str]] = []
    for stem in graph.order:
        if stem not in rank:
            continue
        for required in sorted(graph.deps.get(stem, set())):
            if required in rank and rank[required] > rank[stem]:
                violations.append((stem, required))
    return violations
