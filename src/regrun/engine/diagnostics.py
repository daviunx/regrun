"""Failure-diagnostics helpers: redaction, truncation, and the pure builder.

Keeps the executor thin (SRP): the executor hands over the ``Test`` echo, the
``RunnerResponse`` already in scope, the failed assertions, and the attempt
count; :func:`build_failure_diagnostics` produces a sanitized, truncated
:class:`~regrun.engine.reporter.FailureDiagnostics` ready to attach to a
``TestResult``.

Redaction reuses the canonical ``SENSITIVE_PATTERNS`` field set from
``documentation/standards/development/observability.md`` §4 (field-name
redaction of headers) plus value scrubbing of any resolved auth-token value
that could be echoed back in a response body.
"""

import json
import os
from collections.abc import Iterable, Mapping
from typing import Any

from regrun.engine.assertions import AssertionResult
from regrun.engine.reporter import FailureDiagnostics
from regrun.runners.base import RequestEcho, RunnerResponse

REDACTED = "[REDACTED]"
DEFAULT_BODY_LIMIT = 2000

# Canonical field-name patterns (observability.md §4). A header whose lowercased
# name CONTAINS any of these has its value redacted. Reused verbatim -- do not
# fork into a bespoke list.
SENSITIVE_PATTERNS: frozenset[str] = frozenset(
    {
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
)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Redact header values whose NAME matches a canonical sensitive pattern.

    Field-name based (the observability §4 approach): the whole value is
    replaced with ``[REDACTED]`` when the lowercased header name contains any
    pattern. Non-sensitive headers pass through unchanged.
    """
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        lname = name.lower()
        if any(pattern in lname for pattern in SENSITIVE_PATTERNS):
            redacted[name] = REDACTED
        else:
            redacted[name] = value
    return redacted


def scrub_secrets(text: str, secrets: Iterable[str] | None) -> str:
    """Replace every occurrence of each secret value in ``text`` with the
    ``[REDACTED]`` marker. Empty / falsy secrets are ignored."""
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _body_limit() -> int:
    """Resolve the body char limit, honouring ``REGRUN_DIAG_BODY_LIMIT`` at call
    time (so a var set after import is respected)."""
    raw = os.getenv("REGRUN_DIAG_BODY_LIMIT")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_BODY_LIMIT


def _stringify(body: Any) -> str | None:
    """Render a response/request body as a string for the diagnostics block."""
    if body is None:
        return None
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, default=str)
    except (TypeError, ValueError):
        return str(body)


def truncate_body(body: Any, limit: int | None = None) -> str | None:
    """Return ``body`` as a string, truncated to ``limit`` chars when longer.

    A truncated body carries the annotation ``…[truncated, N total chars]``
    where ``N`` is the FULL pre-truncation length. ``limit`` defaults to
    ``REGRUN_DIAG_BODY_LIMIT`` (read at call time) or ``2000``.
    """
    if body is None:
        return None
    text = body if isinstance(body, str) else _stringify(body)
    if text is None:
        return None
    if limit is None:
        limit = _body_limit()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[truncated, {len(text)} total chars]"


def _scrub_obj(obj: Any, secrets: Iterable[str]) -> Any:
    """Recursively scrub secret values from any string inside a structure."""
    if isinstance(obj, str):
        return scrub_secrets(obj, secrets)
    if isinstance(obj, dict):
        return {k: _scrub_obj(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(v, secrets) for v in obj]
    return obj


def _sanitize_request(request: RequestEcho, secrets: list[str]) -> RequestEcho:
    """Redact headers by field name and scrub secret values from echoed fields."""
    update: dict[str, Any] = {}
    if request.headers is not None:
        redacted = redact_headers(request.headers)
        update["headers"] = {k: scrub_secrets(v, secrets) for k, v in redacted.items()}
    if request.url is not None:
        update["url"] = scrub_secrets(request.url, secrets)
    if request.body is not None:
        update["body"] = _scrub_obj(request.body, secrets)
    if request.send is not None:
        update["send"] = _scrub_obj(request.send, secrets)
    if request.commands is not None:
        update["commands"] = [scrub_secrets(c, secrets) for c in request.commands]
    return request.model_copy(update=update)


def build_failure_diagnostics(
    request: RequestEcho | None,
    response: RunnerResponse,
    failed_assertions: list[AssertionResult],
    attempts: int = 1,
    secrets: Iterable[str] | None = None,
    body_limit: int | None = None,
) -> FailureDiagnostics:
    """Build a sanitized, truncated diagnostics block for a failed test.

    Redacts request headers (canonical field-name patterns), scrubs any resolved
    auth-token value from every echoed string, and truncates the response body.
    Failed assertions are kept full-length (no 60-char table cut).
    """
    secret_list = [s for s in (secrets or []) if s]

    safe_request = _sanitize_request(request, secret_list) if request is not None else None

    body_text = _stringify(response.body)
    if body_text is not None:
        body_text = scrub_secrets(body_text, secret_list)
        body_text = truncate_body(body_text, limit=body_limit)

    return FailureDiagnostics(
        request=safe_request,
        response_status=response.status_code,
        response_body=body_text,
        failed_assertions=list(failed_assertions),
        attempts=attempts,
    )
