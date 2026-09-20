"""Unit tests for BLOCKED in the JUnit emitter (0.10.0).

``junit.py`` emits a bare ``<skipped/>`` today. A BLOCKED test must carry the
blocker in a ``message`` attribute (``blocked by <stem>``) so the MR Tests tab
says WHY the test did not run, while a plain skip keeps the bare form. GitLab
maps both to its existing skipped state, so the deploy gate's meaning is
unchanged and the ``skipped=`` count still includes blocked tests.

A new file rather than cases appended to ``test_junit.py``: the blocked contract
is its own concern and the existing file stays untouched.
"""

import xml.etree.ElementTree as ET

from regrun.engine.junit import format_junit
from regrun.engine.reporter import RunResult, TestResult


def _blocked(test_id: str = "C.1", blocker: str = "01_provider") -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name="consumer test",
        group_name="Consumer",
        passed=False,
        skipped=True,
        blocked_by=blocker,
        duration_ms=0.0,
        file_stem="02_consumer",
    )


def _plain_skip(test_id: str = "S.1") -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name="skipped test",
        group_name="Consumer",
        passed=False,
        skipped=True,
        duration_ms=0.0,
        file_stem="02_consumer",
    )


def _run(results: list[TestResult]) -> RunResult:
    return RunResult(
        product="demo",
        total=len(results),
        skipped=len(results),
        blocked=sum(1 for r in results if r.blocked_by),
        test_results=results,
    )


def _skipped_elements(xml: str) -> list[ET.Element]:
    root = ET.fromstring(xml)
    return root.findall(".//skipped")


def test_blocked_test_emits_a_skipped_message_naming_the_blocker() -> None:
    elements = _skipped_elements(format_junit(_run([_blocked()])))
    assert len(elements) == 1
    assert elements[0].get("message") == "blocked by 01_provider"


def test_plain_skip_stays_a_bare_skipped_element() -> None:
    elements = _skipped_elements(format_junit(_run([_plain_skip()])))
    assert len(elements) == 1
    assert elements[0].get("message") is None


def test_blocked_message_is_xml_escaped() -> None:
    xml = format_junit(_run([_blocked(blocker="01_a&b")]))
    assert "01_a&b" not in xml
    assert _skipped_elements(xml)[0].get("message") == "blocked by 01_a&b"


def test_testsuite_skipped_count_includes_blocked_tests() -> None:
    xml = format_junit(_run([_blocked(), _plain_skip()]))
    suite = ET.fromstring(xml).find("testsuite")
    assert suite is not None
    assert suite.get("skipped") == "2"


def test_blocked_test_is_not_counted_as_a_failure() -> None:
    xml = format_junit(_run([_blocked()]))
    suite = ET.fromstring(xml).find("testsuite")
    assert suite is not None
    assert suite.get("failures") == "0"
    assert suite.get("errors") == "0"
