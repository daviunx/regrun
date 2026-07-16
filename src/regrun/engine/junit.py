"""JUnit XML report emitter for GitLab MR Tests tab integration.

Produces a JUnit XML string from a ``RunResult`` that GitLab renders natively
in the MR "Tests" tab via ``artifacts: reports: junit:``.

Mapping:
- ``<testsuites>`` root element
- ``<testsuite>`` per source YAML file (keyed by ``TestResult.file_stem``)
- ``<testcase>`` per test with ``classname="{product}.{file_stem}.{group_name}"``
- FAIL -> ``<failure>``, ERROR -> ``<error>``, SKIP -> ``<skipped/>``

All text is XML-escaped. Failure/error bodies are capped at ``JUNIT_BODY_CAP``
(16 KB) to avoid GitLab's poor handling of huge bodies.
"""

from collections import defaultdict
from xml.sax.saxutils import escape, quoteattr

from regrun.engine.reporter import RunResult, TestResult

JUNIT_BODY_CAP = 16384  # 16 KB


def _cap_body(text: str) -> str:
    """Truncate text to JUNIT_BODY_CAP with an annotation if longer."""
    if len(text) <= JUNIT_BODY_CAP:
        return text
    return f"{text[:JUNIT_BODY_CAP]}...[truncated, {len(text)} total chars]"


def _failure_body(tr: TestResult) -> str:
    """Render the failure diagnostics body for a <failure> element.

    Reuses the same information the text reporter renders in the Failures
    section: request echo, response status/body, failed assertions.
    """
    lines: list[str] = []
    diag = tr.diagnostics
    if diag is None:
        return ""

    req = diag.request
    if req is not None:
        if req.method or req.url:
            lines.append(f"request: {req.method or ''} {req.url or ''}".rstrip())
        if req.tool:
            lines.append(f"tool: {req.tool}  args: {req.args}")
        if req.commands:
            for cmd in req.commands:
                lines.append(f"$ {cmd}")
        if req.headers:
            lines.append(f"headers: {req.headers}")
        if req.body is not None:
            lines.append(f"body: {req.body}")

    if diag.response_status is not None:
        lines.append(f"response status: {diag.response_status}")
    if diag.response_body is not None:
        lines.append(f"response body: {diag.response_body}")

    for ar in diag.failed_assertions:
        lines.append(f"FAIL {ar.assertion_type}: {ar.message}")
        lines.append(f"  expected: {ar.expected}")
        lines.append(f"  actual:   {ar.actual}")

    if diag.attempts > 1:
        lines.append(f"attempts: {diag.attempts}")

    return "\n".join(lines)


def _failure_message(tr: TestResult) -> str:
    """Extract a short message for the failure 'message' attribute."""
    if tr.diagnostics and tr.diagnostics.failed_assertions:
        first = tr.diagnostics.failed_assertions[0]
        return (
            first.message
            or f"{first.assertion_type}: expected={first.expected} actual={first.actual}"
        )
    return "Test failed"


def _testcase_xml(tr: TestResult, product: str) -> str:
    """Render a single <testcase> element."""
    classname = (
        f"{product}.{tr.file_stem}.{tr.group_name}"
        if tr.file_stem
        else f"{product}.{tr.group_name}"
    )
    name = f"{tr.test_id} {tr.test_name}"
    time_s = f"{tr.duration_ms / 1000:.3f}"

    parts = [
        f"<testcase classname={quoteattr(classname)} name={quoteattr(name)} time={quoteattr(time_s)}>"
    ]

    if tr.skipped:
        parts.append("<skipped/>")
    elif tr.error:
        body = _cap_body(tr.error)
        parts.append(f"<error message={quoteattr(tr.error[:200])}>{escape(body)}</error>")
    elif not tr.passed:
        msg = _failure_message(tr)
        body = _failure_body(tr)
        body = _cap_body(body)
        parts.append(f"<failure message={quoteattr(msg)}>{escape(body)}</failure>")

    parts.append("</testcase>")
    return "\n".join(parts)


def _testsuite_xml(
    suite_name: str,
    tests: list[TestResult],
    product: str,
) -> str:
    """Render a <testsuite> element for a group of tests sharing a file_stem."""
    total = len(tests)
    failures = sum(1 for t in tests if not t.passed and not t.skipped and not t.error)
    errors = sum(1 for t in tests if t.error)
    skipped = sum(1 for t in tests if t.skipped)
    time_s = sum(t.duration_ms for t in tests) / 1000

    header = (
        f"<testsuite name={quoteattr(suite_name)} "
        f'tests="{total}" failures="{failures}" errors="{errors}" '
        f'skipped="{skipped}" time="{time_s:.3f}">'
    )
    cases = "\n".join(_testcase_xml(t, product) for t in tests)
    return f"{header}\n{cases}\n</testsuite>"


def format_junit(run_result: RunResult) -> str:
    """Format run results as JUnit XML.

    Args:
        run_result: The aggregate run result.

    Returns:
        JUnit XML string suitable for ``artifacts: reports: junit:``.
    """
    # Group tests by file_stem to create one <testsuite> per source file
    suites: dict[str, list[TestResult]] = defaultdict(list)
    for tr in run_result.test_results:
        key = tr.file_stem or "unknown"
        suites[key].append(tr)

    suite_xmls = "\n".join(
        _testsuite_xml(name, tests, run_result.product) for name, tests in suites.items()
    )

    return f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites>\n{suite_xmls}\n</testsuites>'
