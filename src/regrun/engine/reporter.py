"""Result formatting: text tables and JSON output."""

from pydantic import BaseModel, Field

from regrun.engine.assertions import AssertionResult


class TestResult(BaseModel):
    """Result of executing a single test."""

    test_id: str
    test_name: str
    group_name: str
    passed: bool
    skipped: bool = False
    error: str | None = None
    duration_ms: float = 0.0
    assertion_results: list[AssertionResult] = Field(default_factory=list)


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
    return run_result.model_dump_json(indent=2)


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
