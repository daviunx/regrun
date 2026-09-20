"""Fixture-hygiene rules: W004 (per-run uniqueness), W005 (capture-dependent
cleanup), W007 (sweep-first unenforced) and W011 (unswept fixture families).
"""

from pathlib import Path

from regrun.engine.lint_rules.context import (
    ALLOW_NOCREATE,
    BUILTIN_VARS,
    VAR_RE,
    WARN,
    FileContext,
    LintFinding,
    iter_strings,
)
from regrun.engine.lint_rules.shapes import (
    FAMILY_PREFIX_RE,
    is_create_shaped,
    is_negative_test,
)

__all__ = ["check_sweep_coverage", "check_test"]


def check_test(ctx: FileContext, group_index: int, group: dict, test: dict) -> list[LintFinding]:
    """W004 + W005 for one test."""
    findings = _check_uniqueness(ctx, test)
    if group.get("cleanup"):
        findings.extend(_check_cleanup_captures(ctx, group_index, test))
    return findings


def _check_uniqueness(ctx: FileContext, test: dict) -> list[LintFinding]:
    """W004 — create-shaped test missing per-run uniqueness.

    Satisfied ONLY by ``{{RUN_ID}}`` or a run-scoped DECLARED variable — an
    inline ``{{timestamp}}`` is recomputed per render, so its value exists
    nowhere in the store: uncapturable and unsweepable. An inline
    ``# lint: allow-nocreate`` escape-hatches the irreducible cases
    (server-derived identifier, key-hint on a non-create tool).
    """
    if not is_create_shaped(test) or is_negative_test(test):
        return []

    payload = list(iter_strings(test.get("body"))) + list(iter_strings(test.get("args")))
    payload_refs: set[str] = set()
    for s in payload:
        payload_refs.update(VAR_RE.findall(s))

    tid = test.get("id", "-")
    if payload_refs & ctx.derived_vars or ctx.has_marker(tid, ALLOW_NOCREATE):
        return []
    return [
        ctx.finding(
            tid,
            "W004",
            WARN,
            (
                "create-shaped body/args carry no {{RUN_ID}} or run-scoped "
                "variable (inline {{timestamp}} is unreproducible)"
            ),
        )
    ]


def _check_cleanup_captures(ctx: FileContext, group_index: int, test: dict) -> list[LintFinding]:
    """W005 — a cleanup group referencing a variable captured in another group."""
    refs: set[str] = set()
    for key in ("body", "args", "commands", "path"):
        for s in iter_strings(test.get(key)):
            refs.update(VAR_RE.findall(s))

    findings: list[LintFinding] = []
    for var in sorted(refs):
        if var in BUILTIN_VARS or var.startswith("env.") or var in ctx.file_vars:
            continue
        if var in ctx.captures_by_group[group_index]:
            continue
        captured_elsewhere = any(
            var in caps for j, caps in enumerate(ctx.captures_by_group) if j != group_index
        )
        if captured_elsewhere:
            findings.append(
                ctx.finding(
                    test.get("id", "-"),
                    "W005",
                    WARN,
                    f"cleanup group references '{{{{{var}}}}}' captured in another group",
                )
            )
    return findings


class _SweepCoverage:
    """Suite-wide sweep facts, gathered in one pass over the parsed files.

    Positions are ``(file_index, group_index)`` in suite sort order, so
    "sorts before" is literal execution order.
    """

    def __init__(self) -> None:
        self.first_create: tuple[int, int] | None = None
        self.first_cleanup: tuple[int, int] | None = None
        self.created_prefixes: dict[str, str] = {}  # prefix -> "file:test" of first creator
        self.coverage: list[str] = []

    def absorb(self, file_index: int, path: Path, raw: dict) -> None:
        for step in raw.get("sweep") or []:
            self.coverage.extend(s for s in iter_strings(step) if isinstance(s, str))
        for gi, g in enumerate(raw.get("groups") or []):
            if g.get("cleanup") and self.first_cleanup is None:
                self.first_cleanup = (file_index, gi)
            for t in g.get("tests") or []:
                if g.get("cleanup"):
                    self.coverage.extend(s for s in iter_strings(t) if isinstance(s, str))
                    continue
                if is_create_shaped(t) and not is_negative_test(t):
                    if self.first_create is None:
                        self.first_create = (file_index, gi)
                    self._absorb_prefixes(path, t)

    def _absorb_prefixes(self, path: Path, test: dict) -> None:
        payload = list(iter_strings(test.get("body"))) + list(iter_strings(test.get("args")))
        for s in payload:
            for m in FAMILY_PREFIX_RE.finditer(s):
                self.created_prefixes.setdefault(m.group(1), f"{path.name}:{test.get('id', '-')}")

    @property
    def orphans(self) -> list[str]:
        return sorted(p for p in self.created_prefixes if not any(p in c for c in self.coverage))


def check_sweep_coverage(parsed: list[tuple[Path, dict, str]]) -> list[LintFinding]:
    """Directory-level sweep rules: W007 (sweep-first missing) + W011 (orphans)."""
    findings: list[LintFinding] = []
    has_sweep = any(raw.get("sweep") for _path, raw, _text in parsed)

    state = _SweepCoverage()
    for fi, (path, raw, _text) in enumerate(parsed):
        state.absorb(fi, path, raw)

    # W007 — no sweep: block AND no cleanup group sorting before the first
    # create-shaped test. A suite that creates nothing needs no sweep.
    if (
        not has_sweep
        and state.first_create is not None
        and (state.first_cleanup is None or state.first_cleanup > state.first_create)
    ):
        findings.append(
            LintFinding(
                file="-",
                test_id="-",
                rule="W007",
                severity=WARN,
                message=(
                    "suite has no sweep: block and no cleanup: true group before its "
                    "first create-shaped test (sweep-first unenforced)"
                ),
            )
        )

    # W011 (best-effort) — created fixture families whose prefix appears in no
    # sweep step / cleanup group string.
    orphans = state.orphans
    if orphans:
        listed = ", ".join(f"'{p}' ({state.created_prefixes[p]})" for p in orphans)
        findings.append(
            LintFinding(
                file="-",
                test_id="-",
                rule="W011",
                severity=WARN,
                message=f"fixture families with no sweep/cleanup delete pattern: {listed}",
            )
        )

    return findings
