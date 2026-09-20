"""Variable-isolation rules: W012 (foreign capture) and E005 (bad ``requires:``).

Both exist because a suite file that silently borrows another file's captured
value is invisible coupling: the run order happens to satisfy it today, and a
filtered run, a re-run of one file, or a shard boundary breaks it tomorrow with a
404 that looks like a product bug. ``requires:`` makes the coupling a declaration
the engine and the linter can both reason about.

W012 names the borrowed value and the file that produces it, and ships as a
WARNING: every existing suite has some, and a rollout that reds them all on the
first release just gets the rule switched off.

E005 is an ERROR: a ``requires:`` that names nothing, names itself, closes a
cycle, or points at a file that runs LATER cannot be satisfied by any run, so it
is a defect in the declaration rather than a judgement call.
"""

from pathlib import Path

from regrun.engine.depgraph import FileNode, Graph, build, closure, detect_cycles

# The run order comes from ``ordering``, the one place it is defined: E005's
# order check must use the exact order the runner runs files in.
from regrun.engine.ordering import run_order_key
from regrun.engine.lint_rules.context import (
    BUILTIN_VARS,
    ERROR,
    TEMPLATE_GLOBALS,
    VAR_REF_RE,
    WARN,
    LintFinding,
    iter_strings,
)

__all__ = ["check_directory"]

Parsed = list[tuple[Path, dict, str]]


def _layer(raw: dict) -> str:
    return str((raw.get("meta") or {}).get("layer") or "unknown")


def _requires(raw: dict) -> list[str]:
    declared = (raw.get("meta") or {}).get("requires") or []
    return [str(stem) for stem in declared] if isinstance(declared, list) else []


def _captured(raw: dict) -> set[str]:
    """Every variable the file produces: test captures, command captures, ``variables:``."""
    produced = set((raw.get("variables") or {}).keys())
    for group in raw.get("groups") or []:
        for test in group.get("tests") or []:
            capture = test.get("capture")
            if isinstance(capture, dict):
                produced.update(capture.keys())
            for command in test.get("commands") or []:
                if isinstance(command, dict) and isinstance(command.get("capture"), dict):
                    produced.update(command["capture"].keys())
    return produced


def _referenced(raw: dict) -> set[str]:
    """Every variable name the file's groups reference, in any template form.

    ``{{ VAR | default('x') }}`` and ``{{ VAR.field }}`` reference VAR as much as
    ``{{ VAR }}`` does, so the leading identifier is what counts. Template
    globals such as ``env`` are not variables and no file produces them.
    """
    names: set[str] = set()
    for text in iter_strings(raw.get("groups") or []):
        names.update(VAR_REF_RE.findall(text))
    return names - TEMPLATE_GLOBALS


def _nodes(parsed: Parsed) -> list[FileNode]:
    """Graph nodes with unsatisfiable ``requires`` entries dropped.

    E005 reports those separately; the graph is built from what remains so cycle
    detection never has to cope with a dangling or self edge.
    """
    stems = {path.stem for path, _raw, _text in parsed}
    return [
        FileNode(
            stem=path.stem,
            layer=_layer(raw),
            requires=[s for s in _requires(raw) if s in stems and s != path.stem],
        )
        for path, raw, _text in parsed
    ]


def _closures(graph: Graph) -> dict[str, set[str]]:
    """Each stem's dependency closure, from the one closure implementation there is."""
    return {stem: closure(graph, stem) for stem in graph.order}


def _producers(parsed: Parsed) -> dict[str, list[tuple[str, str]]]:
    """Variable name -> the (stem, filename) of each file producing it."""
    producers: dict[str, list[tuple[str, str]]] = {}
    for path, raw, _text in parsed:
        for name in _captured(raw):
            producers.setdefault(name, []).append((path.stem, path.name))
    return producers


def _foreign_captures(parsed: Parsed, graph: Graph) -> list[LintFinding]:
    """W012 — a value another file produces, used without declaring the dependency.

    Cleared by declaring ``requires:`` (directly or transitively), by producing
    the value locally, or by the producer being a ``layer: setup`` file: the
    bootstrap contract every file already depends on, which is why it is never
    declared.
    """
    producers = _producers(parsed)
    closures = _closures(graph)
    setup_stems = {path.stem for path, raw, _text in parsed if _layer(raw) == "setup"}

    findings: list[LintFinding] = []
    for path, raw, _text in parsed:
        local = _captured(raw)
        declared = closures.get(path.stem, set())
        for name in sorted(_referenced(raw)):
            if name in BUILTIN_VARS or name in local:
                continue
            foreign = [
                fname
                for stem, fname in producers.get(name, [])
                if stem != path.stem and stem not in setup_stems and stem not in declared
            ]
            if not foreign:
                continue
            findings.append(
                LintFinding(
                    file=path.name,
                    test_id="-",
                    rule="W012",
                    severity=WARN,
                    message=(
                        f"uses '{{{{{name}}}}}' captured in {foreign[0]} without declaring it "
                        f"(add meta.requires: [{Path(foreign[0]).stem}])"
                    ),
                )
            )
    return findings


def _order_index(parsed: Parsed) -> dict[str, int]:
    """Canonical run position of each stem, from the engine's ordering primitive."""
    ordered = sorted(
        ((path, raw) for path, raw, _text in parsed),
        key=lambda item: run_order_key(item[0].stem, _layer(item[1])),
    )
    return {path.stem: index for index, (path, _raw) in enumerate(ordered)}


def _bad_requires(parsed: Parsed, graph: Graph) -> list[LintFinding]:
    """E005 — a ``requires:`` no run can satisfy: unknown, self, later, or cyclic."""
    stems = {path.stem for path, _raw, _text in parsed}
    position = _order_index(parsed)

    findings: list[LintFinding] = []
    for path, raw, _text in parsed:
        for required in _requires(raw):
            if required == path.stem:
                message = "requires itself"
            elif required not in stems:
                message = f"requires '{required}', which names no file in this suite"
            elif position[required] > position[path.stem]:
                message = f"requires '{required}', which runs AFTER it in the canonical order"
            else:
                continue
            findings.append(
                LintFinding(
                    file=path.name,
                    test_id="-",
                    rule="E005",
                    severity=ERROR,
                    message=message,
                )
            )

    names = {path.stem: path.name for path, _raw, _text in parsed}
    for cycle in detect_cycles(graph):
        findings.append(
            LintFinding(
                file=names[cycle[0]],
                test_id="-",
                rule="E005",
                severity=ERROR,
                message=f"requires: cycle {' -> '.join([*cycle, cycle[0]])}",
            )
        )
    return findings


def check_directory(parsed: Parsed) -> list[LintFinding]:
    """W012 + E005 — both are cross-file by nature.

    One graph serves both rules, so the two never disagree about what depends on
    what.
    """
    graph = build(_nodes(parsed))
    return _foreign_captures(parsed, graph) + _bad_requires(parsed, graph)
