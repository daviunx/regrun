"""Static linter for YAML regression suites (no network, no execution).

Encodes the regression-testing discipline from
``documentation/standards/testing/regression.md`` as mechanical checks so
violations surface at commit time instead of months later as flakes.

Rules
-----
==== ==== ================================================================
Rule Sev  Meaning
==== ==== ================================================================
E001 err  Duplicate group id within a single file.
E002 err  An mcp-layer file (``runner: fastmcp`` or ``default_auth: mcp``)
          sorts AFTER a file whose name contains ``cleanup`` in the same
          directory — cleanup revokes the shared api_key, so mcp files must
          sort before it (the 16x-before-17_cleanup rule).
E003 err  A test has an ``auth:`` key with a null value (the ``auth: none``
          string-literal trap — bare ``auth:`` parses as YAML null).
W001 warn An MCP tool test asserts ``is_error`` with no ``json_path`` block
          (asserts the call didn't error, not that it did the right thing).
W002 warn ``equals``/``contains`` on a positional array json_path (``[0]`` /
          ``[*]``) — rank-0 fragile. Suppress per-test with an inline
          ``# lint: allow-positional`` comment, or per-file with
          ``--allow-positional GLOB``.
W003 warn An ``eventually:`` poll whose worst-case ceiling is below the
          budget floor (default 75s) — under-budgeted async poll.
W004 warn A POST/create-shaped test whose body/args carry no ``{{RUN_ID}}``
          / ``{{timestamp}}`` — missing per-run uniqueness. 4xx-asserting
          (negative) tests are skipped.
W005 warn A cleanup-flagged group references a variable captured in ANOTHER
          group of the same file — capture-dependent sweep (should be
          pattern-based / capture-independent).
==== ==== ================================================================
"""

import fnmatch
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

ERROR = "error"
WARN = "warn"

# Variables that never count as "captured elsewhere" for W005.
_BUILTIN_VARS = {"RUN_ID", "timestamp", "date", "uuid"}
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")
_TEST_ID_RE = re.compile(r'^\s*-?\s*id:\s*["\']?([A-Za-z][A-Za-z0-9_.\-]*)')
_ALLOW_POSITIONAL = "# lint: allow-positional"
_CREATE_KEY_HINTS = ("name", "slug", "title", "email")


class LintFinding(BaseModel):
    """A single lint result. ``test_id`` is ``"-"`` for file-level findings."""

    file: str
    test_id: str
    rule: str
    severity: str
    message: str


def eventually_ceiling(cfg: dict) -> float:
    """Worst-case wall time of an ``eventually:`` block, in seconds.

    Mirrors ``engine/retry.py``: ``initial_delay`` before the first attempt,
    then a between-attempt sleep of ``interval * backoff**k`` for
    ``k = 0 .. max_attempts-2`` (one sleep less than the attempt count).
    """
    max_attempts = int(cfg.get("max_attempts", 10))
    interval = float(cfg.get("interval", 2.0))
    backoff = float(cfg.get("backoff", 1.0))
    initial_delay = float(cfg.get("initial_delay", 0.0))
    total = initial_delay
    for k in range(max_attempts - 1):
        total += interval * (backoff**k)
    return total


def _is_mcp_file(raw: dict) -> bool:
    meta = raw.get("meta") or {}
    return meta.get("runner") == "fastmcp" or meta.get("default_auth") == "mcp"


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _is_create_shaped(test: dict) -> bool:
    if (test.get("method") or "").upper() == "POST":
        return True
    args = test.get("args")
    if isinstance(args, dict):
        return any(any(h in str(k).lower() for h in _CREATE_KEY_HINTS) for k in args)
    return False


def _asserts_4xx(test: dict) -> bool:
    assertion = test.get("assert") or {}
    status = assertion.get("status")
    values = status if isinstance(status, list) else [status]
    return any(isinstance(s, int) and 400 <= s < 500 for s in values)


def _map_test_line_ranges(text: str) -> tuple[list[str], dict[str, tuple[int, int]]]:
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
        m = _TEST_ID_RE.match(line)
        if m:
            if current is not None:
                ranges[current] = (start, i)
            current = m.group(1)
            start = i
    if current is not None:
        ranges[current] = (start, len(lines))
    return lines, ranges


def _positional_suppressed(
    test_id: str,
    lines: list[str],
    ranges: dict[str, tuple[int, int]],
) -> bool:
    span = ranges.get(test_id)
    if span is None:
        return False
    start, end = span
    return any(_ALLOW_POSITIONAL in line for line in lines[start:end])


def _lint_file(
    path: Path, raw: dict, text: str, budget_floor: float, file_allows: bool
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    fname = path.name
    lines, id_ranges = _map_test_line_ranges(text)

    groups = raw.get("groups") or []

    # E001 — duplicate group ids
    seen: set[int] = set()
    for g in groups:
        gid = g.get("id")
        if gid in seen:
            findings.append(
                LintFinding(
                    file=fname,
                    test_id="-",
                    rule="E001",
                    severity=ERROR,
                    message=f"duplicate group id {gid}",
                )
            )
        seen.add(gid)

    # Collect capture maps for W005 (per-group captured var names).
    captures_by_group: list[set[str]] = []
    for g in groups:
        captured: set[str] = set()
        for t in g.get("tests") or []:
            cap = t.get("capture")
            if isinstance(cap, dict):
                captured.update(cap.keys())
        captures_by_group.append(captured)
    file_vars = set((raw.get("variables") or {}).keys())

    for gi, g in enumerate(groups):
        is_cleanup = bool(g.get("cleanup"))
        for t in g.get("tests") or []:
            tid = t.get("id", "-")
            assertion = t.get("assert") or {}

            # E003 — auth: null
            if "auth" in t and t["auth"] is None:
                findings.append(
                    LintFinding(
                        file=fname,
                        test_id=tid,
                        rule="E003",
                        severity=ERROR,
                        message="auth: key is null (use the string literal 'none')",
                    )
                )

            # W001 — MCP tool test asserting is_error with no json_path
            if t.get("tool") and "is_error" in assertion and "json_path" not in assertion:
                findings.append(
                    LintFinding(
                        file=fname,
                        test_id=tid,
                        rule="W001",
                        severity=WARN,
                        message="MCP test asserts is_error only (no json_path on the response)",
                    )
                )

            # W002 — positional array assert with equals/contains
            json_path = assertion.get("json_path")
            if isinstance(json_path, dict):
                for jp, cond in json_path.items():
                    if not isinstance(cond, dict):
                        continue
                    positional = "[0]" in jp or "[*]" in jp
                    fragile_op = "equals" in cond or "contains" in cond
                    if positional and fragile_op:
                        if file_allows or _positional_suppressed(tid, lines, id_ranges):
                            continue
                        op = "equals" if "equals" in cond else "contains"
                        findings.append(
                            LintFinding(
                                file=fname,
                                test_id=tid,
                                rule="W002",
                                severity=WARN,
                                message=f"positional {op} on array path '{jp}' (rank-0 fragile)",
                            )
                        )

            # W003 — under-budgeted eventually poll
            ev = t.get("eventually")
            if isinstance(ev, dict):
                ceiling = eventually_ceiling(ev)
                if ceiling < budget_floor:
                    findings.append(
                        LintFinding(
                            file=fname,
                            test_id=tid,
                            rule="W003",
                            severity=WARN,
                            message=f"eventually ceiling {ceiling:.0f}s < floor {budget_floor:.0f}s",
                        )
                    )

            # W004 — create-shaped test missing per-run uniqueness
            if _is_create_shaped(t) and not _asserts_4xx(t):
                payload = list(_iter_strings(t.get("body"))) + list(_iter_strings(t.get("args")))
                if not any(("{{RUN_ID}}" in s or "{{timestamp}}" in s) for s in payload):
                    findings.append(
                        LintFinding(
                            file=fname,
                            test_id=tid,
                            rule="W004",
                            severity=WARN,
                            message="create-shaped body/args carry no {{RUN_ID}}/{{timestamp}}",
                        )
                    )

            # W005 — cleanup group referencing vars captured in another group
            if is_cleanup:
                refs: set[str] = set()
                for s in _iter_strings(t.get("body")):
                    refs.update(_VAR_RE.findall(s))
                for s in _iter_strings(t.get("args")):
                    refs.update(_VAR_RE.findall(s))
                for s in _iter_strings(t.get("commands")):
                    refs.update(_VAR_RE.findall(s))
                for s in _iter_strings(t.get("path")):
                    refs.update(_VAR_RE.findall(s))
                for var in sorted(refs):
                    if var in _BUILTIN_VARS or var.startswith("env.") or var in file_vars:
                        continue
                    if var in captures_by_group[gi]:
                        continue
                    captured_elsewhere = any(
                        var in caps for j, caps in enumerate(captures_by_group) if j != gi
                    )
                    if captured_elsewhere:
                        findings.append(
                            LintFinding(
                                file=fname,
                                test_id=tid,
                                rule="W005",
                                severity=WARN,
                                message=f"cleanup group references '{{{{{var}}}}}' captured in another group",
                            )
                        )

    return findings


def lint_directory(
    test_dir: Path,
    budget_floor: float = 75.0,
    allow_positional: tuple[str, ...] = (),
) -> list[LintFinding]:
    """Statically lint every ``*.yaml`` file in ``test_dir``. No network."""
    yaml_files = sorted(test_dir.glob("*.yaml"))
    if not yaml_files:
        return []

    parsed: list[tuple[Path, dict, str]] = []
    findings: list[LintFinding] = []
    for path in yaml_files:
        text = path.read_text()
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            findings.append(
                LintFinding(
                    file=path.name,
                    test_id="-",
                    rule="E000",
                    severity=ERROR,
                    message=f"YAML parse error: {exc}",
                )
            )
            continue
        if not isinstance(raw, dict):
            continue
        parsed.append((path, raw, text))

    # E002 — cross-file: mcp file sorting after a cleanup-named file.
    cleanup_files = [p.name for p, _, _ in parsed if "cleanup" in p.name.lower()]
    for path, raw, _ in parsed:
        if _is_mcp_file(raw):
            for cname in cleanup_files:
                if cname < path.name:
                    findings.append(
                        LintFinding(
                            file=path.name,
                            test_id="-",
                            rule="E002",
                            severity=ERROR,
                            message=f"mcp-layer file sorts after cleanup file '{cname}'",
                        )
                    )

    for path, raw, text in parsed:
        file_allows = any(fnmatch.fnmatch(path.name, g) for g in allow_positional)
        findings.extend(_lint_file(path, raw, text, budget_floor, file_allows))

    return findings


def lint_exit_code(findings: list[LintFinding], strict: bool = False) -> int:
    """Exit 1 if any error (or, under ``--strict``, any warning); else 0."""
    if any(f.severity == ERROR for f in findings):
        return 1
    if strict and any(f.severity == WARN for f in findings):
        return 1
    return 0


def format_lint_report(findings: list[LintFinding], strict: bool = False) -> str:
    """Render ``file:test_id RULE (sev) message`` lines plus a summary."""
    lines = [f"{f.file}:{f.test_id} {f.rule} ({f.severity}) {f.message}" for f in findings]
    errors = sum(1 for f in findings if f.severity == ERROR)
    warns = sum(1 for f in findings if f.severity == WARN)
    if lines:
        lines.append("")
    verdict = "FAIL" if lint_exit_code(findings, strict) else "PASS"
    lines.append(f"Summary: {errors} error(s), {warns} warning(s) — {verdict}")
    return "\n".join(lines)
