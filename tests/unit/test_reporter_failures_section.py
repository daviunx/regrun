"""Unit tests (TDD-RED) for the reporter ``Failures`` section.

Contract (analysis.md §4.2):

* ``format_text`` renders a new ``Failures`` section BETWEEN the results table
  and the final summary block.
* ``Result: PASS|FAIL`` stays the LAST line of the report (existing tooling
  parses it), so the ordering is: table -> Failures -> summary(``Total:``) ->
  ``Result:``.
* Failed-assertion messages are rendered FULL length there (NOT truncated to
  the 60-char table-row limit).
* When every test passes, the ``Failures`` section is ABSENT.

``FailureDiagnostics`` is imported inside the tests; its absence (and the
missing ``TestResult.diagnostics`` field) is the RED signal.
"""

from regrun.engine.assertions import AssertionResult
from regrun.engine.reporter import RunResult, TestResult, format_text

LONG_ASSERTION_MESSAGE = (
    "Status 500 != 200 -- server raised RecursionError: maximum recursion "
    "depth exceeded while calling a Python object (this is far over 60 chars)"
)


def _passing_test() -> TestResult:
    return TestResult(
        test_id="A1.1",
        test_name="health check",
        group_name="Health",
        passed=True,
        assertion_results=[AssertionResult(passed=True, assertion_type="status")],
    )


def _failing_test() -> TestResult:
    from regrun.engine.diagnostics import build_failure_diagnostics
    from regrun.runners.base import RequestEcho

    failed = [
        AssertionResult(
            passed=False,
            assertion_type="status",
            expected=200,
            actual=500,
            message=LONG_ASSERTION_MESSAGE,
        )
    ]
    from regrun.runners.base import RunnerResponse

    diag = build_failure_diagnostics(
        request=RequestEcho(runner="httpx", method="GET", url="http://demo.localhost/tools"),
        response=RunnerResponse(
            status_code=500,
            body={"detail": "RecursionError: maximum recursion depth exceeded"},
        ),
        failed_assertions=failed,
        attempts=1,
    )
    return TestResult(
        test_id="A9.1",
        test_name="List tools",
        group_name="Tools",
        passed=False,
        diagnostics=diag,
    )


def test_failures_section_between_table_and_summary_result_last() -> None:
    run = RunResult(
        product="demo",
        total=2,
        passed=1,
        failed=1,
        test_results=[_passing_test(), _failing_test()],
    )
    out = format_text(run)

    assert "Failures" in out

    idx_table_row = out.index("A9.1")  # first hit == the results-table row
    idx_failures = out.index("Failures")
    idx_total = out.index("Total:")
    idx_result = out.rindex("Result:")

    # table -> Failures -> summary -> Result
    assert idx_table_row < idx_failures < idx_total < idx_result

    # The failed test id also appears again inside the Failures section.
    assert out.count("A9.1") >= 2

    # Result: is the final (non-blank) line of the report.
    assert out.strip().splitlines()[-1].strip().startswith("Result:")


def test_failed_assertion_message_not_truncated_in_failures_section() -> None:
    run = RunResult(
        product="demo",
        total=1,
        failed=1,
        test_results=[_failing_test()],
    )
    out = format_text(run)

    # Full-length message present verbatim (would be cut at 60 chars in a row).
    assert LONG_ASSERTION_MESSAGE in out
    assert "RecursionError: maximum recursion depth exceeded" in out


def test_no_failures_section_when_all_pass() -> None:
    run = RunResult(
        product="demo",
        total=1,
        passed=1,
        test_results=[_passing_test()],
    )
    # A passing test still exposes the (None) diagnostics field -- AttributeError
    # here is the RED signal that the feature is not yet wired in.
    assert all(tr.diagnostics is None for tr in run.test_results)

    out = format_text(run)

    assert "Failures" not in out
    # Result: still the last line.
    assert out.strip().splitlines()[-1].strip().startswith("Result:")
