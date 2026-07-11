"""Unit tests (TDD-RED) for the failure-diagnostics data model.

Pins the contract of the *full failure diagnostics by default* feature at the
model level, BEFORE the implementation exists. The new symbols are imported
INSIDE each test so a missing module / field surfaces as a per-test FAILURE
(not a whole-file collection error).

Target contract (analysis.md §4.1):

* ``regrun.runners.base.RequestEcho`` -- per-runner echo of what was actually
  sent (after template rendering).
* ``regrun.engine.reporter.FailureDiagnostics`` -- populated ONLY when a test
  did not pass. Flat shape::

      request: RequestEcho | None
      response_status: int | None
      response_body: str | None          # redacted + truncated at build time
      failed_assertions: list[AssertionResult]   # ALL failures, UNtruncated
      attempts: int                      # 1 unless an ``eventually`` block ran

* ``regrun.engine.reporter.TestResult`` gains ``diagnostics: FailureDiagnostics
  | None`` (``None`` for a passing test).
* ``regrun.engine.diagnostics.build_failure_diagnostics(...)`` -- the pure
  builder the executor delegates to (keeps the executor thin).
* ``format_json`` OMITS ``diagnostics`` entirely when it is ``None`` (a passing
  test's JSON must not carry a ``diagnostics`` key).
"""

from regrun.engine.assertions import AssertionResult
from regrun.engine.reporter import RunResult, TestResult, format_json
from regrun.runners.base import RunnerResponse


def _failed_status_assertion() -> AssertionResult:
    return AssertionResult(
        passed=False,
        assertion_type="status",
        expected=200,
        actual=500,
        message="Status 500 != 200",
    )


def test_failing_result_carries_full_diagnostics() -> None:
    """A failing test's TestResult.diagnostics captures request echo, response
    status + body, EVERY failed assertion (full length), attempts == 1."""
    from regrun.engine.diagnostics import build_failure_diagnostics
    from regrun.runners.base import RequestEcho

    request = RequestEcho(
        runner="httpx",
        method="GET",
        url="http://demo.localhost/tools",
        headers={"Content-Type": "application/json"},
    )
    response = RunnerResponse(
        status_code=500,
        body={"error": "RecursionError: maximum recursion depth exceeded"},
    )
    failed = [_failed_status_assertion()]

    diag = build_failure_diagnostics(
        request=request,
        response=response,
        failed_assertions=failed,
        attempts=1,
    )

    tr = TestResult(
        test_id="A9.1",
        test_name="List tools",
        group_name="Tools",
        passed=False,
        diagnostics=diag,
    )

    assert tr.diagnostics is not None
    assert tr.diagnostics.request is not None
    assert tr.diagnostics.request.method == "GET"
    assert tr.diagnostics.request.url == "http://demo.localhost/tools"
    assert tr.diagnostics.response_status == 500
    assert "RecursionError" in tr.diagnostics.response_body
    assert len(tr.diagnostics.failed_assertions) == 1
    assert tr.diagnostics.failed_assertions[0].message == "Status 500 != 200"
    assert tr.diagnostics.attempts == 1


def test_all_failed_assertions_captured_untruncated() -> None:
    """Diagnostics keeps EVERY failed assertion, full-length -- not just the
    first, not truncated to 60 chars (the old table-row behaviour)."""
    from regrun.engine.diagnostics import build_failure_diagnostics
    from regrun.runners.base import RequestEcho

    long_message = "Value != expected " + ("x" * 120)
    failed = [
        _failed_status_assertion(),
        AssertionResult(
            passed=False,
            assertion_type="json_path($.name).equals",
            expected="alpha",
            actual="beta",
            message=long_message,
        ),
    ]

    diag = build_failure_diagnostics(
        request=RequestEcho(runner="httpx", method="GET", url="http://x/y"),
        response=RunnerResponse(status_code=500, body={"k": "v"}),
        failed_assertions=failed,
    )

    assert len(diag.failed_assertions) == 2
    assert diag.failed_assertions[1].message == long_message
    assert len(diag.failed_assertions[1].message) > 60


def test_passing_result_has_no_diagnostics_and_absent_from_json() -> None:
    """A passing test carries ``diagnostics is None`` and the key is OMITTED
    from ``format_json`` output entirely."""
    tr_pass = TestResult(
        test_id="A1.1",
        test_name="health",
        group_name="Health",
        passed=True,
        assertion_results=[AssertionResult(passed=True, assertion_type="status")],
    )

    # AttributeError here is the RED signal: the field does not exist yet.
    assert tr_pass.diagnostics is None

    run = RunResult(
        product="demo",
        total=1,
        passed=1,
        test_results=[tr_pass],
    )
    js = format_json(run)
    assert "diagnostics" not in js
