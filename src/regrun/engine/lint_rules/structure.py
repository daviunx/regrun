"""Suite-structure rules: E001 (duplicate group id), E002 (mcp file after a
cleanup file) and W006 (no preflight anywhere in the suite).
"""

from pathlib import Path

from regrun.engine.lint_rules.context import (
    ERROR,
    WARN,
    FileContext,
    LintFinding,
    is_mcp_file,
)

__all__ = ["check_file", "check_directory"]


def check_file(ctx: FileContext) -> list[LintFinding]:
    """E001 — duplicate group ids within one file."""
    findings: list[LintFinding] = []
    seen: set[object] = set()
    for g in ctx.groups:
        gid = g.get("id")
        if gid in seen:
            findings.append(ctx.finding("-", "E001", ERROR, f"duplicate group id {gid}"))
        seen.add(gid)
    return findings


def check_directory(parsed: list[tuple[Path, dict, str]]) -> list[LintFinding]:
    """E002 + W006 — rules that can only be decided across the whole suite."""
    findings: list[LintFinding] = []

    # E002 — cross-file: an mcp file sorting after a cleanup-named file. Cleanup
    # revokes the shared api_key, so mcp files must sort before it.
    cleanup_files = [p.name for p, _, _ in parsed if "cleanup" in p.name.lower()]
    for path, raw, _ in parsed:
        if not is_mcp_file(raw):
            continue
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

    # W006 — no preflight block anywhere in the suite (adoption nudge).
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

    return findings
