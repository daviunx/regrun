"""Static linter for YAML regression suites (no network, no execution).

Encodes the regression-testing discipline as mechanical checks so violations
surface at commit time instead of months later as flakes. This module is the
COORDINATOR: it parses the suite, builds a per-file context, and dispatches to
the rule families in ``engine/lint_rules/``. Rule logic lives there, never here.

Rules
-----
==== ==== ================================================================
Rule Sev  Meaning
==== ==== ================================================================
E000 err  The file is not parseable YAML.
E001 err  Duplicate group id within a single file.
E002 err  An mcp-layer file (``runner: fastmcp`` or ``default_auth: mcp``)
          sorts AFTER a file whose name contains ``cleanup`` in the same
          directory — cleanup revokes the shared api_key, so mcp files must
          sort before it (the 16x-before-17_cleanup rule).
E003 err  A test has an ``auth:`` key with a null value (the ``auth: none``
          string-literal trap — bare ``auth:`` parses as YAML null).
E004 err  A test on an auth-consuming runner (httpx/fastmcp/websocket)
          references an auth profile — via ``auth:`` or ``meta.default_auth``
          — that is not defined in THIS file's ``auth:`` block. Profiles are
          per-file; before regrun 0.9.1 the request silently went out with NO
          credentials (401-instead-of-403 masquerading as a product bug).
E005 err  A ``meta.requires:`` entry no run can satisfy: an unknown stem, the
          file itself, a cycle, or a file that runs AFTER its dependent in the
          canonical order. Directory-level.
E006 err  The file parses as YAML but does not validate against the test-file
          schema: an undeclared key (a misplaced or misspelt one, silently
          dropped before the schema models forbade extras), a missing required
          key, or a wrong type. Every schema model forbids extra keys, so the
          engine refuses such a file at load; E006 keeps the linter from being
          blinder than the engine. One finding per pydantic error, carrying the
          failing location and message.
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
W012 warn A file uses a variable ANOTHER suite file captures without declaring
          ``meta.requires:`` for it — invisible coupling that survives only
          while the full suite runs in order. Cleared by declaring the
          dependency, by producing the value locally, or by the producer being
          a ``layer: setup`` file. Directory-level.
==== ==== ================================================================
"""

import fnmatch
from pathlib import Path

import yaml

from regrun.engine.lint_rules import (
    DIRECTORY_RULES,
    FILE_RULES,
    TEST_RULES,
    build_file_context,
    derived_variables,
)
from regrun.engine.lint_rules.auth import AUTH_CONSUMING_RUNNERS as _AUTH_CONSUMING_RUNNERS
from regrun.engine.lint_rules.context import ERROR, WARN, LintFinding
from regrun.engine.lint_rules.timing import eventually_ceiling
from regrun.engine.ordering import within_layer_key

__all__ = [
    "ERROR",
    "WARN",
    "LintFinding",
    # Re-exported for callers that imported it from this module before the
    # rule families were split out into ``engine/lint_rules/``.
    "_AUTH_CONSUMING_RUNNERS",
    "eventually_ceiling",
    "format_lint_report",
    "lint_directory",
    "lint_exit_code",
]


def _parse_suite(
    yaml_files: list[Path],
) -> tuple[list[tuple[Path, dict, str]], list[LintFinding]]:
    """Read + parse every file; unparseable files become E000 findings."""
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
    return parsed, findings


def _lint_file(
    path: Path,
    raw: dict,
    text: str,
    budget_floor: float,
    file_allows: bool,
    derived_vars: set[str],
) -> list[LintFinding]:
    """Run every file-level and per-test rule against one suite file."""
    ctx = build_file_context(path, raw, text, budget_floor, file_allows, derived_vars)

    findings: list[LintFinding] = []
    for file_rule in FILE_RULES:
        findings.extend(file_rule(ctx))

    for group_index, group in enumerate(ctx.groups):
        for test in group.get("tests") or []:
            for test_rule in TEST_RULES:
                findings.extend(test_rule(ctx, group_index, group, test))

    return findings


def lint_directory(
    test_dir: Path,
    budget_floor: float = 75.0,
    allow_positional: tuple[str, ...] = (),
) -> list[LintFinding]:
    """Statically lint every ``*.yaml`` file in ``test_dir``. No network."""
    yaml_files = sorted(test_dir.glob("*.yaml"), key=lambda path: within_layer_key(path.stem))
    if not yaml_files:
        return []

    parsed, findings = _parse_suite(yaml_files)

    for directory_rule in DIRECTORY_RULES:
        findings.extend(directory_rule(parsed))

    derived_vars = derived_variables(parsed)
    for path, raw, text in parsed:
        file_allows = any(fnmatch.fnmatch(path.name, g) for g in allow_positional)
        findings.extend(_lint_file(path, raw, text, budget_floor, file_allows, derived_vars))

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
