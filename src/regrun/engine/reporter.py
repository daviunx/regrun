"""Result formatting: text tables and JSON output."""

from pydantic import BaseModel, Field

from regrun.engine.assertions import AssertionResult
from regrun.runners.base import RequestEcho


class FailureDiagnostics(BaseModel):
    """Everything needed to understand a failure from a single run.

    Populated only when a test did not pass (see
    ``regrun.engine.diagnostics.build_failure_diagnostics``). Secrets are already
    redacted / scrubbed and the response body already truncated by the builder,
    so this object is safe to render and serialize as-is.
    """

    request: RequestEcho | None = None
    response_status: int | None = None
    response_body: str | None = None
    failed_assertions: list[AssertionResult] = Field(default_factory=list)
    attempts: int = 1


class TestResult(BaseModel):
    """Result of executing a single test."""

    test_id: str
    test_name: str
    group_name: str
    passed: bool
    skipped: bool = False
    error: str | None = None
    duration_ms: float = 0.0
    file_stem: str = ""
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    diagnostics: FailureDiagnostics | None = None


class RunResult(BaseModel):
    """Aggregate result of a full regression run."""

    product: str
    layer: str | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    test_results: list[TestResult] = Field(default_factory=list)

    # Preflight phase (dependency-health probes run before any group).
    # ``preflight_count`` is the number of checks executed; the ``preflight_*``
    # failure fields are populated only when a check aborted the run.
    preflight_count: int = 0
    preflight_failed: bool = False
    preflight_failed_name: str | None = None
    preflight_diagnostics: FailureDiagnostics | None = None
    preflight_error: str | None = None


def format_text(run_result: RunResult) -> str:
    """Format run results as a human-readable text table.

    Args:
        run_result: The aggregate run result.

    Returns:
        Formatted string with test results table and summary.
    """
    lines: list[str] = []

    # Header
    header = f"Regression Run: {run_result.product}"
    if run_result.layer:
        header += f" (layer: {run_result.layer})"
    lines.append(header)
    lines.append("=" * len(header))
    # Preflight visibility: a CI log missing this line was run by a pre-0.8.0
    # binary that silently ignored the suite's preflight blocks.
    if run_result.preflight_count > 0:
        lines.append(f"preflight: {run_result.preflight_count} checks passed")
    lines.append("")

    # Column widths
    id_width = max(
        (len(tr.test_id) for tr in run_result.test_results),
        default=6,
    )
    id_width = max(id_width, 6)

    name_width = max(
        (len(tr.test_name) for tr in run_result.test_results),
        default=10,
    )
    name_width = min(max(name_width, 10), 50)

    # Table header
    lines.append(
        f"  {'ID':<{id_width}}  {'Test':<{name_width}}  {'Status':<8}  {'Time':>8}  Details"
    )
    lines.append(f"  {'-' * id_width}  {'-' * name_width}  {'-' * 8}  {'-' * 8}  {'-' * 30}")

    # Table rows
    current_group = ""
    for tr in run_result.test_results:
        if tr.group_name != current_group:
            current_group = tr.group_name
            lines.append(f"\n  [{current_group}]")

        status = _status_label(tr)
        time_str = f"{tr.duration_ms:.0f}ms"
        details = _details_str(tr)

        name_display = tr.test_name[:name_width]
        lines.append(
            f"  {tr.test_id:<{id_width}}  {name_display:<{name_width}}  {status:<8}  {time_str:>8}  {details}"
        )

    # Failures section: rendered BETWEEN the table and the summary so a
    # tail-clipped terminal still shows the diagnostics, while "Result:" stays
    # the last line for tooling that parses it.
    lines.extend(_failures_section(run_result))

    # Summary
    lines.append("")
    lines.append("-" * 60)
    total_time = f"{run_result.duration_ms:.0f}ms"
    lines.append(
        f"  Total: {run_result.total}  "
        f"Passed: {run_result.passed}  "
        f"Failed: {run_result.failed}  "
        f"Skipped: {run_result.skipped}  "
        f"Errors: {run_result.errors}  "
        f"Time: {total_time}"
    )

    overall = "PASS" if run_result.failed == 0 and run_result.errors == 0 else "FAIL"
    lines.append(f"  Result: {overall}")
    lines.append("")

    return "\n".join(lines)


def format_json(run_result: RunResult) -> str:
    """Format run results as JSON.

    Args:
        run_result: The aggregate run result.

    Returns:
        JSON string of the run result.
    """
    return run_result.model_dump_json(indent=2, exclude_none=True)


def _failures_section(run_result: RunResult) -> list[str]:
    """Render per-failure diagnostics for every test carrying a diagnostics block.

    Returns an empty list when no test failed (so all-pass runs stay terse).
    """
    failing = [tr for tr in run_result.test_results if tr.diagnostics is not None]
    if not failing:
        return []

    lines: list[str] = ["", "", f"Failures ({len(failing)})", "=" * 60]
    for tr in failing:
        lines.extend(_one_failure(tr))
    return lines


def _one_failure(tr: TestResult) -> list[str]:
    """Render a single failed test's diagnostics block (full length)."""
    diag = tr.diagnostics
    lines: list[str] = ["", f"[{tr.group_name}] {tr.test_id}  {tr.test_name}"]

    if tr.error:
        lines.append(f"  error: {tr.error}")

    if diag is None:
        return lines

    req = diag.request
    if req is not None:
        if req.method or req.url:
            lines.append(f"  request: {req.method or ''} {req.url or ''}".rstrip())
        if req.tool:
            lines.append(f"  tool: {req.tool}  args: {req.args}")
        if req.commands:
            for cmd in req.commands:
                lines.append(f"  $ {cmd}")
        if req.sql:
            lines.append(f"  sql: {req.sql}")
        if req.headers:
            lines.append(f"  headers: {req.headers}")
        if req.body is not None:
            lines.append(f"  body: {req.body}")
        if req.send is not None:
            lines.append(f"  send: {req.send}  wait_for: {req.wait_for}")

    if diag.response_status is not None:
        lines.append(f"  response status: {diag.response_status}")
    if diag.response_body is not None:
        lines.append(f"  response body: {diag.response_body}")

    for ar in diag.failed_assertions:
        lines.append(f"  ✗ {ar.assertion_type}: {ar.message}")
        lines.append(f"      expected: {ar.expected}")
        lines.append(f"      actual:   {ar.actual}")

    if diag.attempts > 1:
        lines.append(f"  attempts: {diag.attempts}")

    return lines


def _status_label(tr: TestResult) -> str:
    """Get the display status label for a test result."""
    if tr.skipped:
        return "SKIP"
    if tr.error:
        return "ERROR"
    return "PASS" if tr.passed else "FAIL"


def _details_str(tr: TestResult) -> str:
    """Build a details string for a test result row."""
    if tr.error:
        return tr.error[:60]
    if tr.skipped:
        return "skipped"

    failed_assertions = [a for a in tr.assertion_results if not a.passed]
    if not failed_assertions:
        return ""

    first_fail = failed_assertions[0]
    return first_fail.message[:60]
