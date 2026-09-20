"""Shared vocabulary for the lint rule modules: the finding model and the
primitives every rule family reads (variable references, test line spans,
inline suppression markers).

Extracted from ``linter.py`` so each rule family is its own module and
``linter.py`` stays the coordinator.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

ERROR = "error"
WARN = "warn"

# Variables that never count as "captured elsewhere" (W005) nor as produced by
# a suite file (W012) — the engine mints them.
BUILTIN_VARS = {"RUN_ID", "timestamp", "date", "uuid"}
VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")
TEST_ID_RE = re.compile(r'^\s*-?\s*id:\s*["\']?([A-Za-z][A-Za-z0-9_.\-]*)')

ALLOW_POSITIONAL = "# lint: allow-positional"
ALLOW_NOCREATE = "# lint: allow-nocreate"
ALLOW_EXISTS = "# lint: allow-exists"

# Canonical run order: layer rank then filename. The linter must consume the
# same order the runner derives (``cli._discover_yaml_files``), never re-derive
# a different one.
LAYER_ORDER = {"setup": 0, "api": 1, "mcp": 2, "chat": 3}


class LintFinding(BaseModel):
    """A single lint result. ``test_id`` is ``"-"`` for file-level findings."""

    file: str
    test_id: str
    rule: str
    severity: str
    message: str


def is_mcp_file(raw: dict) -> bool:
    """An mcp-layer file: ``runner: fastmcp`` or ``default_auth: mcp``."""
    meta = raw.get("meta") or {}
    return meta.get("runner") == "fastmcp" or meta.get("default_auth") == "mcp"


def iter_strings(obj: object):
    """Yield every string nested anywhere inside ``obj``."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)


def map_test_line_ranges(text: str) -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Map each test id to its ``[start, end)`` line range (0-indexed).

    Loose but robust: a test block runs from its ``id:`` line to the line
    before the next test ``id:`` line. Group ids (integers) never match the
    letter-leading test-id pattern, so they don't split ranges.
    """
    lines = text.splitlines()
    ranges: dict[str, tuple[int, int]] = {}
    current: str | None = None
    start = 0
    for i, line in enumerate(lines):
        m = TEST_ID_RE.match(line)
        if m:
            if current is not None:
                ranges[current] = (start, i)
            current = m.group(1)
            start = i
    if current is not None:
        ranges[current] = (start, len(lines))
    return lines, ranges


def derived_variables(parsed: list[tuple[Path, dict, str]]) -> set[str]:
    """Suite-wide set of RUN-SCOPED declared variables (W004).

    A declared variable is run-scoped when its declaration derives — directly
    or through other declared variables (fixpoint) — from ``{{timestamp}}``,
    ``{{uuid}}`` or ``RUN_ID``. ``RUN_ID`` itself is always in the set (engine
    builtin since 0.9.0; suites may still declare it).
    """
    declared: dict[str, str] = {}
    for _path, raw, _text in parsed:
        for key, value in (raw.get("variables") or {}).items():
            declared.setdefault(key, str(value))

    derived = {"RUN_ID"}
    changed = True
    while changed:
        changed = False
        for key, value in declared.items():
            if key in derived:
                continue
            names = set(VAR_RE.findall(value))
            if {"timestamp", "uuid"} & names or names & derived:
                derived.add(key)
                changed = True
    return derived


@dataclass(frozen=True)
class FileContext:
    """Everything a rule needs about ONE suite file, pre-computed once."""

    path: Path
    raw: dict
    budget_floor: float
    file_allows: bool
    derived_vars: set[str]
    lines: list[str]
    id_ranges: dict[str, tuple[int, int]]
    is_mcp: bool
    meta_runner: str | None
    default_auth: str | None
    auth_profiles: set[str]
    file_vars: set[str]
    captures_by_group: list[set[str]]

    @property
    def fname(self) -> str:
        return self.path.name

    @property
    def groups(self) -> list[dict]:
        return self.raw.get("groups") or []

    def has_marker(self, test_id: str, marker: str) -> bool:
        """True if ``marker`` appears on any source line within the test's span.

        The shared span-scan mechanism behind the inline suppression comments
        (``# lint: allow-positional`` for W002, ``# lint: allow-nocreate`` for
        W004, ``# lint: allow-exists`` for W009).
        """
        span = self.id_ranges.get(test_id)
        if span is None:
            return False
        start, end = span
        return any(marker in line for line in self.lines[start:end])

    def finding(self, test_id: str, rule: str, severity: str, message: str) -> LintFinding:
        return LintFinding(
            file=self.fname,
            test_id=test_id,
            rule=rule,
            severity=severity,
            message=message,
        )


def build_file_context(
    path: Path,
    raw: dict,
    text: str,
    budget_floor: float,
    file_allows: bool,
    derived_vars: set[str],
) -> FileContext:
    """Pre-compute the per-file facts the rule modules share."""
    lines, id_ranges = map_test_line_ranges(text)
    meta = raw.get("meta") or {}

    captures_by_group: list[set[str]] = []
    for g in raw.get("groups") or []:
        captured: set[str] = set()
        for t in g.get("tests") or []:
            cap = t.get("capture")
            if isinstance(cap, dict):
                captured.update(cap.keys())
        captures_by_group.append(captured)

    return FileContext(
        path=path,
        raw=raw,
        budget_floor=budget_floor,
        file_allows=file_allows,
        derived_vars=derived_vars,
        lines=lines,
        id_ranges=id_ranges,
        is_mcp=is_mcp_file(raw),
        meta_runner=meta.get("runner"),
        default_auth=meta.get("default_auth"),
        auth_profiles=set((raw.get("auth") or {}).keys()),
        file_vars=set((raw.get("variables") or {}).keys()),
        captures_by_group=captures_by_group,
    )
