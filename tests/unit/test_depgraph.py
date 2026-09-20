"""Unit tests for ``engine/depgraph.py`` — the file dependency graph (0.10.0).

Pure functions over in-memory nodes; no files, no network, no mocks.

Contract:

  * ``build(nodes)`` builds a ``Graph`` from ``meta.requires`` plus the implicit
    rule that every ``layer: setup`` file is a dependency of every NON-setup
    file (setup files carry no implicit dependency — "a failed setup file blocks
    every later file" is an executor clause, not a graph edge, so setup ordering
    never shows up as a graph cycle).
  * ``closure(graph, stem)`` is the TRANSITIVE dependency set of one stem.
  * ``build`` rejects a ``requires:`` naming an unknown stem or the file itself.
  * ``detect_cycles(graph)`` REPORTS cycles (it does not raise) so the linter can
    render them as findings.
  * ``validate_order(graph, order)`` reports every ``(dependent, required)`` pair
    where the required file sorts AFTER its dependent in the canonical run order.
"""

import pytest

from regrun.engine import depgraph


def _node(
    stem: str,
    layer: str = "api",
    requires: tuple[str, ...] = (),
    serial: bool = False,
    test_count: int = 1,
) -> depgraph.FileNode:
    return depgraph.FileNode(
        stem=stem,
        layer=layer,
        requires=list(requires),
        serial=serial,
        test_count=test_count,
    )


def _suite() -> list[depgraph.FileNode]:
    """setup + a three-file chain + one independent file."""
    return [
        _node("00_setup", layer="setup"),
        _node("01_provider"),
        _node("02_consumer", requires=("01_provider",)),
        _node("03_tail", requires=("02_consumer",)),
        _node("04_independent"),
    ]


# --------------------------------------------------------------------------- closure


def test_closure_contains_the_direct_dependency() -> None:
    graph = depgraph.build(_suite())
    assert "01_provider" in depgraph.closure(graph, "02_consumer")


def test_closure_is_transitive() -> None:
    graph = depgraph.build(_suite())
    assert depgraph.closure(graph, "03_tail") == {"00_setup", "02_consumer", "01_provider"}


def test_closure_includes_the_setup_layer_implicitly() -> None:
    """No file declares 00_setup; every non-setup file depends on it anyway."""
    graph = depgraph.build(_suite())
    assert depgraph.closure(graph, "04_independent") == {"00_setup"}


def test_closure_of_a_setup_file_is_empty() -> None:
    graph = depgraph.build(_suite())
    assert depgraph.closure(graph, "00_setup") == set()


def test_closure_excludes_the_stem_itself() -> None:
    graph = depgraph.build(_suite())
    assert "02_consumer" not in depgraph.closure(graph, "02_consumer")


def test_closure_of_an_unknown_stem_raises() -> None:
    graph = depgraph.build(_suite())
    with pytest.raises(depgraph.UnknownStemError):
        depgraph.closure(graph, "99_nope")


def test_closure_is_deterministic() -> None:
    graph = depgraph.build(_suite())
    assert depgraph.closure(graph, "03_tail") == depgraph.closure(graph, "03_tail")


def test_multiple_setup_files_are_all_implicit_dependencies() -> None:
    graph = depgraph.build(
        [
            _node("00_setup", layer="setup"),
            _node("00a_seed", layer="setup"),
            _node("01_api"),
        ]
    )
    assert depgraph.closure(graph, "01_api") == {"00_setup", "00a_seed"}


# ----------------------------------------------------------------------------- build


def test_build_rejects_an_unknown_required_stem() -> None:
    with pytest.raises(depgraph.UnknownStemError, match="99_missing"):
        depgraph.build(
            [_node("00_setup", layer="setup"), _node("01_api", requires=("99_missing",))]
        )


def test_build_rejects_a_self_reference() -> None:
    with pytest.raises(depgraph.SelfDependencyError, match="01_api"):
        depgraph.build([_node("00_setup", layer="setup"), _node("01_api", requires=("01_api",))])


def test_build_accepts_an_empty_node_list() -> None:
    graph = depgraph.build([])
    assert depgraph.detect_cycles(graph) == []


def test_closure_on_an_empty_graph_raises() -> None:
    graph = depgraph.build([])
    with pytest.raises(depgraph.UnknownStemError):
        depgraph.closure(graph, "00_setup")


# ---------------------------------------------------------------------------- cycles


def test_detect_cycles_finds_a_two_cycle() -> None:
    graph = depgraph.build(
        [
            _node("01_a", requires=("02_b",)),
            _node("02_b", requires=("01_a",)),
        ]
    )
    cycles = depgraph.detect_cycles(graph)
    assert any({"01_a", "02_b"} == set(cycle) for cycle in cycles)


def test_detect_cycles_finds_a_three_cycle() -> None:
    graph = depgraph.build(
        [
            _node("01_a", requires=("03_c",)),
            _node("02_b", requires=("01_a",)),
            _node("03_c", requires=("02_b",)),
        ]
    )
    cycles = depgraph.detect_cycles(graph)
    assert any({"01_a", "02_b", "03_c"} == set(cycle) for cycle in cycles)


def test_detect_cycles_is_empty_on_an_acyclic_graph() -> None:
    assert depgraph.detect_cycles(depgraph.build(_suite())) == []


def test_detect_cycles_is_deterministic() -> None:
    nodes = [
        _node("01_a", requires=("02_b",)),
        _node("02_b", requires=("01_a",)),
    ]
    first = depgraph.detect_cycles(depgraph.build(nodes))
    second = depgraph.detect_cycles(depgraph.build(list(reversed(nodes))))
    assert first == second


# ------------------------------------------------------------------------- ordering


def test_validate_order_accepts_a_correctly_sorted_suite() -> None:
    graph = depgraph.build(_suite())
    order = ["00_setup", "01_provider", "02_consumer", "03_tail", "04_independent"]
    assert depgraph.validate_order(graph, order) == []


def test_validate_order_flags_a_required_file_that_sorts_later() -> None:
    """``01_early`` requires ``02_late``: the producer runs AFTER its consumer."""
    graph = depgraph.build(
        [
            _node("00_setup", layer="setup"),
            _node("01_early", requires=("02_late",)),
            _node("02_late"),
        ]
    )
    violations = depgraph.validate_order(graph, ["00_setup", "01_early", "02_late"])
    assert ("01_early", "02_late") in violations


def test_validate_order_flags_a_cross_layer_backwards_edge() -> None:
    """An mcp file requiring an api file is fine; the reverse is an order violation."""
    graph = depgraph.build(
        [
            _node("00_setup", layer="setup"),
            _node("01_api", requires=("02_mcp",), layer="api"),
            _node("02_mcp", layer="mcp"),
        ]
    )
    violations = depgraph.validate_order(graph, ["00_setup", "01_api", "02_mcp"])
    assert violations == [("01_api", "02_mcp")]
