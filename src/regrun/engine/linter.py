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
          and no reference to a RUN-SCOPED declared variable (one whose
          declaration derives from ``{{timestamp}}``/``{{uuid}}``/RUN_ID).
          An INLINE ``{{timestamp}}`` no longer satisfies it: the builtin is
          recomputed per render, so the value exists nowhere in the store —
          uncapturable AND unsweepable. Create-shaped =
          POST, or an ``args.action`` in the create allowlist (create/add/…),
          or (no action) a name/slug/title/email arg. Skipped: negative tests
          (HTTP 4xx OR MCP ``is_error: true``); non-create ``action:`` verbs
          (reads get/list/…, mutations update/delete, diagnostics); and HTTP
          POSTs that are not creates — search/query endpoints, auth endpoints
          (login/auth/token), delete endpoints (``…delete``), and bodyless
          POSTs. Irreducible creates (server-derived name / key-hint on a
          non-create tool) suppress per-test with ``# lint: allow-nocreate``.
W005 warn A cleanup-flagged group references a variable captured in ANOTHER
          group of the same file — capture-dependent sweep (should be
          pattern-based / capture-independent).
W006 warn The suite directory declares NO ``preflight:`` block in any file —
          missing dependency-health probes (adoption nudge). Directory-level.
W007 warn The suite has neither a ``sweep:`` block nor a ``cleanup: true``
          group sorting BEFORE its first create-shaped test — sweep-first is
          unenforced, so a crashed run's leftovers greet the next run.
          Directory-level.
W008 warn A bash ``cmd`` carries a hardcoded ``http(s)://`` host or a
          ``psql … -d <name>`` database literal not wrapped in
          ``{{ env.get(...) }}`` / ``${VAR:-default}`` — the step silently
          reads the wrong stack instead of failing loud.
W009 warn A ``json_path`` condition whose ONLY operator is ``exists: true`` —
          a JSONPath match on ``null`` SATISFIES ``exists``, so the assert
          passes on the very null the code under test produces. Use
          ``not_empty`` or a value; suppress per-test for keys that may
          legitimately be absent with ``# lint: allow-exists``.
W010 warn A ``$.data.*`` path in ``json_path`` or ``capture`` within an
          mcp-layer file — MCP asserts/captures run on the POST-normalize
          body (``data`` is hoisted to top level), so the path never matches.
W011 warn Best-effort: fixture-name prefixes created by create-shaped tests
          (``<prefix>{{RUN_ID}}``) that appear in NO sweep step or
          ``cleanup: true`` group — unswept fixture families. Directory-level.
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
_ALLOW_NOCREATE = "# lint: allow-nocreate"
_ALLOW_EXISTS = "# lint: allow-exists"
_CREATE_KEY_HINTS = ("name", "slug", "title", "email")
# W008: literal-host detection in bash commands. A URL/db literal is
# parameterized (and exempt) when it sits inside a Jinja ``{{ ... }}`` span
# (e.g. the DEFAULT of ``{{ env.get('REGRUN_API_ENDPOINT', 'http://…') }}``)
# or a shell ``${VAR:-default}`` span, or when the host itself is a variable
# (``http://${API_HOST}/…`` / ``http://{{HOST}}/…``).
_URL_LITERAL_RE = re.compile(r"https?://")
_JINJA_SPAN_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_SHELL_SPAN_RE = re.compile(r"\$\{[^}]*\}")
_PSQL_DB_RE = re.compile(r"\bpsql\b[^;|&\n]*?\s-d\s+(\S+)")
# W011: a created-name string ``<prefix>{{RUN_ID}}`` names a fixture family by
# its prefix (``regr-vis-``); the prefix must appear in some sweep/cleanup
# delete pattern or the family is unsweepable.
_FAMILY_PREFIX_RE = re.compile(r"([A-Za-z][A-Za-z0-9._-]*[-_])\{\{\s*RUN_ID\s*\}\}")
# HTTP POST path-SEGMENT exemptions for W004 — a POST is create-shaped by
# default, but these segment classes are NOT creates and carry no per-run row:
#   * search / query — a read (semantic/keyword/hybrid search, ad-hoc query)
#   * login / auth / token — an auth exchange with FIXED credentials
# Matched as whole '/'-split segments (case-folded), never as substrings, so
# ``/searchable-widgets`` or ``/oauthorize-foo`` do NOT trip. Delete endpoints
# use a segment-CONTAINS check (``bulk-delete`` / ``.../delete``) because the
# verb rides a compound segment; bodyless POSTs are handled separately.
_SEARCH_SEGMENTS = frozenset({"search", "_search", "query"})
_AUTH_SEGMENTS = frozenset({"login", "auth", "token"})
# Multiplex WRITE actions that CREATE a new persistent row — the only case where
# per-run uniqueness (W004) applies. When a test's ``args.action`` names one of
# these it is create-shaped; EVERY other explicit action verb targets an existing
# entity or returns data — reads (get/list/list_missing/search/stats/…), in-place
# mutations (update/delete/restore), diagnostics — so W004 does NOT apply, even if
# the args carry a ``name``/``slug`` key (real-world NTX.2 get / PV.5 update-restore
# / MVL.2 list / MP.4 stats false positives). Discrete create tools (``tool_create``
# etc.) carry no ``action`` arg and fall through to the ``name``/``slug`` hint check.
_CREATE_ACTIONS = frozenset({"create", "add", "insert", "register", "new", "upsert"})


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


def _is_data_path(path: object) -> bool:
    """W010: a JSONPath rooted at ``$.data`` (segment-exact — ``$.database`` is not)."""
    if not isinstance(path, str):
        return False
    return path == "$.data" or path.startswith("$.data.") or path.startswith("$.data[")


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _http_post_is_noncreate(test: dict) -> bool:
    """An HTTP POST that is NOT a create → exempt from W004.

    Conservative, path-segment based (not substring soup):
      1. search / query endpoint (segment ``search``/``_search``/``query``) —
         a read, not a create.
      2. auth endpoint (segment ``login``/``auth``/``token``) — a fixed-
         credential exchange, no per-run row.
      3. delete endpoint (any segment CONTAINS ``delete``: ``bulk-delete`` /
         ``.../delete``) — delete semantics, not create.
      4. bodyless POST (no ``body`` and no ``json``) — an action toggle with
         nothing to carry a ``{{RUN_ID}}``.
    """
    segments = [s.lower() for s in str(test.get("path") or "").split("/") if s]
    if any(s in _SEARCH_SEGMENTS for s in segments):
        return True
    if any(s in _AUTH_SEGMENTS for s in segments):
        return True
    if any("delete" in s for s in segments):
        return True
    return test.get("body") is None and test.get("json") is None


def _is_create_shaped(test: dict) -> bool:
    if (test.get("method") or "").upper() == "POST":
        return not _http_post_is_noncreate(test)
    args = test.get("args")
    if isinstance(args, dict):
        # An explicit ``action`` verb governs: only a genuine create verb is
        # create-shaped. Reads (get/list/…), in-place mutations (update/delete),
        # and diagnostics never create a per-run row, so W004 does not apply even
        # when they carry a name/slug filter/target arg.
        action = args.get("action")
        if isinstance(action, str) and action.strip():
            return action.strip().lower() in _CREATE_ACTIONS
        return any(any(h in str(k).lower() for h in _CREATE_KEY_HINTS) for k in args)
    return False


def _is_negative_test(test: dict) -> bool:
    """A negative/expected-failure test — skipped by W004.

    Covers BOTH an HTTP 4xx status assertion (api layer) AND an MCP
    ``is_error: true`` assertion (mcp layer). A negative test intentionally
    rejects its input, so it exercises no create path and needs no per-run
    uniqueness.
    """
    assertion = test.get("assert") or {}
    status = assertion.get("status")
    values = status if isinstance(status, list) else [status]
    if any(isinstance(s, int) and 400 <= s < 500 for s in values):
        return True
    return assertion.get("is_error") is True


def _derived_variables(parsed: list[tuple[Path, dict, str]]) -> set[str]:
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
            names = set(_VAR_RE.findall(value))
            if {"timestamp", "uuid"} & names or names & derived:
                derived.add(key)
                changed = True
    return derived


def _param_spans(s: str) -> list[tuple[int, int]]:
    """Character spans of ``{{ … }}`` / ``${ … }`` parameterizations in a string."""
    return [m.span() for m in _JINJA_SPAN_RE.finditer(s)] + [
        m.span() for m in _SHELL_SPAN_RE.finditer(s)
    ]


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _bash_hardcoded_literals(cmd: str) -> list[str]:
    """W008 messages for one bash command: hardcoded URL hosts / psql db targets.

    A literal is exempt when it sits inside a ``{{ … }}`` or ``${ … }`` span
    (parameterized with a default), or when the host/name itself is a variable.
    """
    messages: list[str] = []
    spans = _param_spans(cmd)

    for m in _URL_LITERAL_RE.finditer(cmd):
        if _in_spans(m.start(), spans):
            continue
        after = cmd[m.end() : m.end() + 2]
        if after.startswith("$") or after.startswith("{{"):
            continue  # literal scheme, parameterized host
        token = re.match(r"\S+", cmd[m.start() :])
        literal = token.group(0).rstrip("'\"),;") if token else cmd[m.start() :]
        messages.append(f"hardcoded URL '{literal}'")

    for m in _PSQL_DB_RE.finditer(cmd):
        name = m.group(1).strip("'\"")
        if name.startswith("$") or name.startswith("{{") or _in_spans(m.start(1), spans):
            continue
        messages.append(f"hardcoded psql database '-d {name}'")

    return messages


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


def _span_has_marker(
    test_id: str,
    lines: list[str],
    ranges: dict[str, tuple[int, int]],
    marker: str,
) -> bool:
    """True if ``marker`` appears on any source line within the test's span.

    The shared span-scan mechanism behind the inline suppression comments
    (``# lint: allow-positional`` for W002, ``# lint: allow-nocreate`` for W004).
    """
    span = ranges.get(test_id)
    if span is None:
        return False
    start, end = span
    return any(marker in line for line in lines[start:end])


def _lint_file(
    path: Path,
    raw: dict,
    text: str,
    budget_floor: float,
    file_allows: bool,
    derived_vars: set[str],
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    fname = path.name
    is_mcp = _is_mcp_file(raw)
    lines, id_ranges = _map_test_line_ranges(text)

    groups = raw.get("groups") or []

    # W008 on sweep steps too — a sweep with a hardcoded host sweeps the wrong
    # stack, which is exactly the failure class the block exists to prevent.
    for step in raw.get("sweep") or []:
        if not isinstance(step, dict):
            continue
        for bash_cmd in step.get("commands") or []:
            cmd = bash_cmd.get("cmd") if isinstance(bash_cmd, dict) else None
            if not isinstance(cmd, str):
                continue
            for message in _bash_hardcoded_literals(cmd):
                findings.append(
                    LintFinding(
                        file=fname,
                        test_id=f"sweep:{step.get('name', '-')}",
                        rule="W008",
                        severity=WARN,
                        message=f"{message} (use {{{{ env.get(...) }}}} or ${{VAR:-default}})",
                    )
                )

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
                        if file_allows or _span_has_marker(
                            tid, lines, id_ranges, _ALLOW_POSITIONAL
                        ):
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

                    # W009 — exists: true as the ONLY operator: satisfied by a
                    # null value, so the assert cannot fail on the very null
                    # the code under test produces.
                    if cond == {"exists": True} and not _span_has_marker(
                        tid, lines, id_ranges, _ALLOW_EXISTS
                    ):
                        findings.append(
                            LintFinding(
                                file=fname,
                                test_id=tid,
                                rule="W009",
                                severity=WARN,
                                message=(
                                    f"'{jp}' asserts only exists: true (passes on null — "
                                    f"use not_empty/a value, or '# lint: allow-exists')"
                                ),
                            )
                        )

            # W010 — $.data.* on an mcp-layer file: asserts/captures run on the
            # POST-normalize body (data hoisted to top level) — never matches.
            if is_mcp:
                data_paths: list[str] = []
                if isinstance(json_path, dict):
                    data_paths.extend(jp for jp in json_path if _is_data_path(jp))
                cap = t.get("capture")
                if isinstance(cap, dict):
                    data_paths.extend(v for v in cap.values() if _is_data_path(v))
                for jp in data_paths:
                    findings.append(
                        LintFinding(
                            file=fname,
                            test_id=tid,
                            rule="W010",
                            severity=WARN,
                            message=(
                                f"'{jp}' targets $.data.* on an mcp-layer file — asserts/"
                                f"captures run on the POST-normalize body (data is hoisted)"
                            ),
                        )
                    )

            # W008 — hardcoded host / db target in a bash command.
            for bash_cmd in t.get("commands") or []:
                cmd = bash_cmd.get("cmd") if isinstance(bash_cmd, dict) else None
                if not isinstance(cmd, str):
                    continue
                for message in _bash_hardcoded_literals(cmd):
                    findings.append(
                        LintFinding(
                            file=fname,
                            test_id=tid,
                            rule="W008",
                            severity=WARN,
                            message=f"{message} (use {{{{ env.get(...) }}}} or ${{VAR:-default}})",
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

            # W004 — create-shaped test missing per-run uniqueness. Satisfied
            # ONLY by {{RUN_ID}} or a run-scoped DECLARED variable — an inline
            # {{timestamp}} is recomputed per render, so its value exists
            # nowhere in the store: uncapturable and unsweepable (RGRN-13).
            # An inline ``# lint: allow-nocreate`` escape-hatches the
            # irreducible cases (server-derived identifier, key-hint on a
            # non-create tool).
            if _is_create_shaped(t) and not _is_negative_test(t):
                payload = list(_iter_strings(t.get("body"))) + list(_iter_strings(t.get("args")))
                payload_refs: set[str] = set()
                for s in payload:
                    payload_refs.update(_VAR_RE.findall(s))
                has_unique = bool(payload_refs & derived_vars)
                suppressed = _span_has_marker(tid, lines, id_ranges, _ALLOW_NOCREATE)
                if not has_unique and not suppressed:
                    findings.append(
                        LintFinding(
                            file=fname,
                            test_id=tid,
                            rule="W004",
                            severity=WARN,
                            message=(
                                "create-shaped body/args carry no {{RUN_ID}} or run-scoped "
                                "variable (inline {{timestamp}} is unreproducible)"
                            ),
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

    # W006 — directory-level: no preflight block anywhere in the suite.
    if parsed and not any(raw.get("preflight") for _, raw, _ in parsed):
        findings.append(
            LintFinding(
                file="-",
                test_id="-",
                rule="W006",
                severity=WARN,
                message="suite declares no preflight: block (missing dependency-health probes)",
            )
        )

    findings.extend(_lint_sweep_coverage(parsed))

    derived_vars = _derived_variables(parsed)
    for path, raw, text in parsed:
        file_allows = any(fnmatch.fnmatch(path.name, g) for g in allow_positional)
        findings.extend(_lint_file(path, raw, text, budget_floor, file_allows, derived_vars))

    return findings


def _lint_sweep_coverage(parsed: list[tuple[Path, dict, str]]) -> list[LintFinding]:
    """Directory-level sweep rules: W007 (sweep-first missing) + W011 (orphans).

    Positions are ``(file_index, group_index)`` in suite sort order, so
    "sorts before" is literal execution order.
    """
    findings: list[LintFinding] = []
    has_sweep = any(raw.get("sweep") for _path, raw, _text in parsed)

    first_create: tuple[int, int] | None = None
    first_cleanup: tuple[int, int] | None = None
    created_prefixes: dict[str, str] = {}  # prefix -> "file:test" of first creator
    coverage: list[str] = []

    for fi, (path, raw, _text) in enumerate(parsed):
        for step in raw.get("sweep") or []:
            coverage.extend(s for s in _iter_strings(step) if isinstance(s, str))
        for gi, g in enumerate(raw.get("groups") or []):
            if g.get("cleanup") and first_cleanup is None:
                first_cleanup = (fi, gi)
            for t in g.get("tests") or []:
                if g.get("cleanup"):
                    coverage.extend(s for s in _iter_strings(t) if isinstance(s, str))
                    continue
                if _is_create_shaped(t) and not _is_negative_test(t):
                    if first_create is None:
                        first_create = (fi, gi)
                    for s in list(_iter_strings(t.get("body"))) + list(
                        _iter_strings(t.get("args"))
                    ):
                        for m in _FAMILY_PREFIX_RE.finditer(s):
                            created_prefixes.setdefault(
                                m.group(1), f"{path.name}:{t.get('id', '-')}"
                            )

    # W007 — no sweep: block AND no cleanup group sorting before the first
    # create-shaped test. A suite that creates nothing needs no sweep.
    if (
        not has_sweep
        and first_create is not None
        and (first_cleanup is None or first_cleanup > first_create)
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
    orphans = sorted(p for p in created_prefixes if not any(p in c for c in coverage))
    if orphans:
        listed = ", ".join(f"'{p}' ({created_prefixes[p]})" for p in orphans)
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
