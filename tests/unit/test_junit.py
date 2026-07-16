"""Unit tests for the JUnit XML emitter (engine/junit.py).

Tests cover: happy path (all pass), failure with diagnostics, error,
skipped, XML escaping of special characters, body size cap (16KB),
and classname with file_stem.
"""

import xml.etree.ElementTree as ET

from regrun.engine.assertions import AssertionResult
from regrun.engine.reporter import RunResult, TestResult


def _passing_test(file_stem: str = "01_api_surface") -> TestResult:
    return TestResult(
        test_id="A1.1",
        test_name="health check",
        group_name="Health",
        passed=True,
        duration_ms=120.5,
        file_stem=file_stem,
        assertion_results=[AssertionResult(passed=True, assertion_type="status")],
    )


def _failing_test(file_stem: str = "02_tools") -> TestResult:
    from regrun.engine.diagnostics import build_failure_diagnostics
    from regrun.runners.base import RequestEcho, RunnerResponse

    failed = [
        AssertionResult(
            passed=False,
            assertion_type="status",
            expected=200,
            actual=500,
            message="Status 500 != 200",
        )
    ]
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
        duration_ms=350.0,
        file_stem=file_stem,
        diagnostics=diag,
    )


def _error_test(file_stem: str = "02_tools") -> TestResult:
    return TestResult(
        test_id="A9.2",
        test_name="Create tool",
        group_name="Tools",
        passed=False,
        error="ConnectionRefusedError: [Errno 111] Connection refused",
        duration_ms=50.0,
        file_stem=file_stem,
    )


def _skipped_test(file_stem: str = "03_chat") -> TestResult:
    return TestResult(
        test_id="C1.1",
        test_name="Chat flow",
        group_name="Chat",
        passed=False,
        skipped=True,
        file_stem=file_stem,
    )


def test_junit_happy_path_all_pass() -> None:
    """All-pass run produces valid XML with correct structure."""
    from regrun.engine.junit import format_junit

    run = RunResult(
        product="demo",
        total=1,
        passed=1,
        duration_ms=120.5,
        test_results=[_passing_test()],
    )
    xml_str = format_junit(run)
    root = ET.fromstring(xml_str)

    assert root.tag == "testsuites"
    suites = root.findall("testsuite")
    assert len(suites) == 1
    assert suites[0].attrib["name"] == "01_api_surface"

    cases = suites[0].findall("testcase")
    assert len(cases) == 1
    assert cases[0].attrib["name"] == "A1.1 health check"
    assert "classname" in cases[0].attrib
    assert cases[0].attrib["classname"] == "demo.01_api_surface.Health"

    # No failure/error/skipped children
    assert cases[0].find("failure") is None
    assert cases[0].find("error") is None
    assert cases[0].find("skipped") is None


def test_junit_failure_with_diagnostics() -> None:
    """Failed test produces <failure> element with diagnostics body."""
    from regrun.engine.junit import format_junit

    run = RunResult(
        product="demo",
        total=1,
        failed=1,
        duration_ms=350.0,
        test_results=[_failing_test()],
    )
    xml_str = format_junit(run)
    root = ET.fromstring(xml_str)

    case = root.find(".//testcase")
    failure = case.find("failure")
    assert failure is not None
    assert "message" in failure.attrib
    assert failure.text is not None
    assert "500" in failure.text


def test_junit_error_test() -> None:
    """Errored test produces <error> element."""
    from regrun.engine.junit import format_junit

    run = RunResult(
        product="demo",
        total=1,
        errors=1,
        duration_ms=50.0,
        test_results=[_error_test()],
    )
    xml_str = format_junit(run)
    root = ET.fromstring(xml_str)

    case = root.find(".//testcase")
    error = case.find("error")
    assert error is not None
    assert "ConnectionRefusedError" in error.attrib["message"]


def test_junit_skipped_test() -> None:
    """Skipped test produces <skipped/> element."""
    from regrun.engine.junit import format_junit

    run = RunResult(
        product="demo",
        total=1,
        skipped=1,
        duration_ms=0.0,
        test_results=[_skipped_test()],
    )
    xml_str = format_junit(run)
    root = ET.fromstring(xml_str)

    case = root.find(".//testcase")
    skipped = case.find("skipped")
    assert skipped is not None


def test_junit_xml_escaping() -> None:
    """Special XML characters in names/bodies are escaped properly."""
    from regrun.engine.junit import format_junit

    tr = TestResult(
        test_id="X1.1",
        test_name='Test with <special> & "chars"',
        group_name="Group <A>",
        passed=True,
        duration_ms=10.0,
        file_stem="special_chars",
    )
    run = RunResult(
        product="demo & co",
        total=1,
        passed=1,
        duration_ms=10.0,
        test_results=[tr],
    )
    xml_str = format_junit(run)
    # Must parse without error (proper escaping)
    root = ET.fromstring(xml_str)
    case = root.find(".//testcase")
    assert 'Test with <special> & "chars"' in case.attrib["name"]


def test_junit_body_size_cap() -> None:
    """Failure bodies over 16KB are truncated."""
    from regrun.engine.junit import JUNIT_BODY_CAP, format_junit

    # Create a test with a huge error message
    huge_error = "x" * 20000
    tr = TestResult(
        test_id="B1.1",
        test_name="Big error",
        group_name="Big",
        passed=False,
        error=huge_error,
        duration_ms=10.0,
        file_stem="big_test",
    )
    run = RunResult(
        product="demo",
        total=1,
        errors=1,
        duration_ms=10.0,
        test_results=[tr],
    )
    xml_str = format_junit(run)
    root = ET.fromstring(xml_str)

    error_el = root.find(".//error")
    assert error_el is not None
    # Body text should be capped
    assert len(error_el.text) <= JUNIT_BODY_CAP + 100  # some slack for truncation annotation


def test_junit_file_stem_classname() -> None:
    """Classname uses product.file_stem.group_name format."""
    from regrun.engine.junit import format_junit

    tr = TestResult(
        test_id="A1.1",
        test_name="check",
        group_name="MyGroup",
        passed=True,
        duration_ms=5.0,
        file_stem="04_admin",
    )
    run = RunResult(
        product="myproduct",
        total=1,
        passed=1,
        duration_ms=5.0,
        test_results=[tr],
    )
    xml_str = format_junit(run)
    root = ET.fromstring(xml_str)

    case = root.find(".//testcase")
    assert case.attrib["classname"] == "myproduct.04_admin.MyGroup"
