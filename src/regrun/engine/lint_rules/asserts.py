"""Assertion-quality rules: W001, W002, W009, W010 — the false-green family.

Each of these passes a test that checked nothing meaningful: an ``is_error``-only
MCP assert, a rank-0 positional match, an ``exists: true`` satisfied by null, and
a ``$.data.*`` path that can never match a normalized MCP body.
"""

from regrun.engine.lint_rules.context import (
    ALLOW_EXISTS,
    ALLOW_POSITIONAL,
    WARN,
    FileContext,
    LintFinding,
)

__all__ = ["check_test", "is_data_path"]


def is_data_path(path: object) -> bool:
    """W010: a JSONPath rooted at ``$.data`` (segment-exact — ``$.database`` is not)."""
    if not isinstance(path, str):
        return False
    return path == "$.data" or path.startswith("$.data.") or path.startswith("$.data[")


def check_test(ctx: FileContext, _group_index: int, _group: dict, test: dict) -> list[LintFinding]:
    """W001 + W002 + W009 + W010 for one test."""
    findings: list[LintFinding] = []
    tid = test.get("id", "-")
    assertion = test.get("assert") or {}
    json_path = assertion.get("json_path")

    # W001 — MCP tool test asserting is_error with no json_path.
    if test.get("tool") and "is_error" in assertion and "json_path" not in assertion:
        findings.append(
            ctx.finding(
                tid,
                "W001",
                WARN,
                "MCP test asserts is_error only (no json_path on the response)",
            )
        )

    if isinstance(json_path, dict):
        findings.extend(_check_json_path(ctx, tid, json_path))

    # W010 — $.data.* on an mcp-layer file: asserts/captures run on the
    # POST-normalize body (data hoisted to top level) — never matches.
    if ctx.is_mcp:
        findings.extend(_check_data_paths(ctx, tid, test, json_path))

    return findings


def _check_json_path(ctx: FileContext, tid: str, json_path: dict) -> list[LintFinding]:
    """W002 (positional fragile match) + W009 (exists-only) per condition."""
    findings: list[LintFinding] = []
    for jp, cond in json_path.items():
        if not isinstance(cond, dict):
            continue

        positional = "[0]" in jp or "[*]" in jp
        fragile_op = "equals" in cond or "contains" in cond
        if (
            positional
            and fragile_op
            and not (ctx.file_allows or ctx.has_marker(tid, ALLOW_POSITIONAL))
        ):
            op = "equals" if "equals" in cond else "contains"
            findings.append(
                ctx.finding(
                    tid,
                    "W002",
                    WARN,
                    f"positional {op} on array path '{jp}' (rank-0 fragile)",
                )
            )

        # W009 — exists: true as the ONLY operator: satisfied by a null value,
        # so the assert cannot fail on the very null the code under test
        # produces.
        if cond == {"exists": True} and not ctx.has_marker(tid, ALLOW_EXISTS):
            findings.append(
                ctx.finding(
                    tid,
                    "W009",
                    WARN,
                    (
                        f"'{jp}' asserts only exists: true (passes on null — "
                        f"use not_empty/a value, or '# lint: allow-exists')"
                    ),
                )
            )
    return findings


def _check_data_paths(
    ctx: FileContext, tid: str, test: dict, json_path: object
) -> list[LintFinding]:
    """W010 over both the assert paths and the capture paths of one test."""
    data_paths: list[str] = []
    if isinstance(json_path, dict):
        data_paths.extend(jp for jp in json_path if is_data_path(jp))
    cap = test.get("capture")
    if isinstance(cap, dict):
        data_paths.extend(v for v in cap.values() if is_data_path(v))

    return [
        ctx.finding(
            tid,
            "W010",
            WARN,
            (
                f"'{jp}' targets $.data.* on an mcp-layer file — asserts/"
                f"captures run on the POST-normalize body (data is hoisted)"
            ),
        )
        for jp in data_paths
    ]
