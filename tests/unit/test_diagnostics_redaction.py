"""Unit tests (TDD-RED) for diagnostics redaction.

Redaction has two mechanisms, both pinned here against the CANONICAL
``SENSITIVE_PATTERNS`` field set from
``documentation/standards/development/observability.md`` §4 -- NOT an invented
list:

1. Field-name redaction of the request headers at capture time
   (``redact_headers``): any header whose lowercased name CONTAINS a canonical
   pattern gets its value replaced with ``[REDACTED]``.
2. Value scrubbing of any resolved auth-token value appearing anywhere in the
   diagnostic block (``scrub_secrets``) -- covers a token echoed back in a
   response body.

``build_failure_diagnostics`` applies BOTH before the diagnostics object exists,
so a rendered text report and its JSON never leak the raw secret.

New symbols imported inside each test so their absence is a per-test FAILURE.
"""

from regrun.engine.assertions import AssertionResult
from regrun.engine.reporter import TestResult, format_json, format_text
from regrun.runners.base import RunnerResponse

# The canonical field-name pattern set (observability.md §4). The regrun
# redactor MUST reuse exactly this set, not a bespoke one.
CANONICAL_SENSITIVE_PATTERNS = {
    "password",
    "token",
    "secret",
    "key",
    "authorization",
    "cookie",
    "credit_card",
    "ssn",
    "api_key",
    "access_token",
    "refresh_token",
    "client_secret",
}


def test_sensitive_patterns_are_the_canonical_set() -> None:
    """regrun reuses the canonical observability SENSITIVE_PATTERNS verbatim."""
    from regrun.engine.diagnostics import SENSITIVE_PATTERNS

    assert set(SENSITIVE_PATTERNS) == CANONICAL_SENSITIVE_PATTERNS


def test_redact_headers_redacts_by_canonical_field_name() -> None:
    """Header values are redacted when the header NAME matches a canonical
    pattern; benign headers are left untouched."""
    from regrun.engine.diagnostics import redact_headers

    headers = {
        "Authorization": "Bearer SECRET-TOKEN-123",
        "X-API-Key": "abc123",
        "Cookie": "session=xyz",
        "Content-Type": "application/json",
        "X-Org-Slug": "acme",
    }

    redacted = redact_headers(headers)

    assert redacted["Authorization"] == "[REDACTED]"  # "authorization"
    assert redacted["X-API-Key"] == "[REDACTED]"  # contains "key"
    assert redacted["Cookie"] == "[REDACTED]"  # "cookie"
    # Non-sensitive headers pass through unchanged.
    assert redacted["Content-Type"] == "application/json"
    assert redacted["X-Org-Slug"] == "acme"


def test_scrub_secrets_replaces_token_value_anywhere() -> None:
    """A resolved token value is replaced with [REDACTED] wherever it appears."""
    from regrun.engine.diagnostics import scrub_secrets

    text = 'response body {"access_token": "SECRET-TOKEN-123", "user": "bob"}'
    scrubbed = scrub_secrets(text, ["SECRET-TOKEN-123"])

    assert "SECRET-TOKEN-123" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_diagnostics_render_never_leaks_secret_in_text_or_json() -> None:
    """Authorization header + a token value echoed in the response body are both
    redacted in the rendered text report AND the JSON output."""
    from regrun.engine.diagnostics import build_failure_diagnostics
    from regrun.runners.base import RequestEcho

    token = "SECRET-TOKEN-123"
    request = RequestEcho(
        runner="httpx",
        method="POST",
        url="http://demo.localhost/login",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    response = RunnerResponse(
        status_code=200,
        body={"access_token": token, "user": "bob"},
    )
    failed = [
        AssertionResult(
            passed=False,
            assertion_type="status",
            expected=201,
            actual=200,
            message="Status 200 != 201",
        )
    ]

    diag = build_failure_diagnostics(
        request=request,
        response=response,
        failed_assertions=failed,
        secrets=[token],
    )

    # Header redacted by canonical field name.
    assert diag.request.headers["Authorization"] == "[REDACTED]"
    # Token value scrubbed out of the captured body.
    assert token not in (diag.response_body or "")

    tr = TestResult(
        test_id="L.1",
        test_name="login",
        group_name="Auth",
        passed=False,
        diagnostics=diag,
    )
    from regrun.engine.reporter import RunResult

    run = RunResult(product="demo", total=1, failed=1, test_results=[tr])

    text = format_text(run)
    js = format_json(run)

    assert token not in text
    assert token not in js
    assert "[REDACTED]" in text
    assert "[REDACTED]" in js
