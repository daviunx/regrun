"""Credential-reference rules: E003 (``auth:`` null) and E004 (dangling profile)."""

from regrun.engine.lint_rules.context import ERROR, FileContext, LintFinding

# Runner types whose requests carry auth credentials. Keep in lockstep with
# ``regrun.engine.runner_factory.AUTH_CONSUMING_RUNNERS`` (the runtime guard) —
# the linter stays import-free of the engine on purpose; a unit test asserts the
# two sets are equal.
AUTH_CONSUMING_RUNNERS = frozenset({"httpx", "fastmcp", "websocket"})

__all__ = ["AUTH_CONSUMING_RUNNERS", "check_test"]


def check_test(ctx: FileContext, _group_index: int, _group: dict, test: dict) -> list[LintFinding]:
    """E003 + E004 for one test."""
    findings: list[LintFinding] = []
    tid = test.get("id", "-")

    # E003 — auth: null (the ``auth: none`` string-literal trap: bare ``auth:``
    # parses as YAML null, so the request goes out unauthenticated).
    if "auth" in test and test["auth"] is None:
        findings.append(
            ctx.finding(tid, "E003", ERROR, "auth: key is null (use the string literal 'none')")
        )

    # E004 — auth profile referenced but not defined in THIS file. Mirrors the
    # executor's runtime guard (unknown_auth_profile_error): profiles are
    # per-file, and a dangling reference used to degrade to an unauthenticated
    # request.
    effective_runner = test.get("runner") or ctx.meta_runner
    if effective_runner not in AUTH_CONSUMING_RUNNERS:
        return findings

    effective_auth = test.get("auth") if test.get("auth") is not None else ctx.default_auth
    if (
        isinstance(effective_auth, str)
        and effective_auth != "none"
        and effective_auth not in ctx.auth_profiles
    ):
        source = "auth" if test.get("auth") is not None else "meta.default_auth"
        findings.append(
            ctx.finding(
                tid,
                "E004",
                ERROR,
                (
                    f"{source}={effective_auth!r} is not defined in this "
                    f"file's auth: block (profiles are per-file — "
                    f"redeclare it here or use 'none')"
                ),
            )
        )
    return findings
