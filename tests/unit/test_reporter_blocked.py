"""Unit tests for BLOCKED reporting (0.10.0).

BLOCKED is a DISCRIMINATED sub-kind of the existing ``skipped`` state: a blocked
test keeps ``skipped=True`` (so every existing consumer's counts keep their
meaning) and additionally carries ``blocked_by``. A plain skip keeps
``blocked_by = None``.

Contract:

  * ``TestResult.blocked_by: str | None`` and ``RunResult.blocked: int`` exist.
  * the text table renders BLOCKED distinctly from SKIP and names the blocker.
  * the summary line carries a separate ``Blocked:`` count while ``Skipped:``
    still counts blocked tests too.
  * ``report.json`` carries ``blocked`` and per-test ``blocked_by``.
  * a fully-blocked run whose provider failed is still FAIL, never green.
"""

import json

from regrun.engine.reporter import RunResult, TestResult, format_json, format_text


def _failed(test_id: str = "P.1") -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name="provider test",
        group_name="Provider",
        passed=False,
        duration_ms=10.0,
        file_stem="01_provider",
    )


def _blocked(test_id: str = "C.1", blocker: str = "01_provider") -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name="consumer test",
        group_name="Consumer",
        passed=False,
        skipped=True,
        blocked_by=blocker,
        file_stem="02_consumer",
    )


def _plain_skip(test_id: str = "S.1") -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name="skipped test",
        group_name="Consumer",
        passed=False,
        skipped=True,
        file_stem="02_consumer",
    )


def _run(**overrides: object) -> RunResult:
    fields: dict = {
        "product": "demo",
        "target": "demo.localhost",
        "regrun_version": "0.10.0",
        "total": 3,
        "passed": 0,
        "failed": 1,
        "skipped": 2,
        "blocked": 1,
        "test_results": [_failed(), _blocked(), _plain_skip()],
    }
    fields.update(overrides)
    return RunResult(**fields)


# ----------------------------------------------------------------------- model fields


def test_test_result_blocked_by_defaults_to_none() -> None:
    assert _plain_skip().blocked_by is None


def test_blocked_result_keeps_skipped_true() -> None:
    result = _blocked()
    assert result.skipped is True
    assert result.blocked_by == "01_provider"


def test_run_result_blocked_defaults_to_zero() -> None:
    assert RunResult(product="demo").blocked == 0


# ------------------------------------------------------------------------- text report


def test_text_table_renders_blocked_distinctly_from_skip() -> None:
    text = format_text(_run())
    assert "BLOCKED" in text


def test_text_table_names_the_blocker_on_the_blocked_row() -> None:
    text = format_text(_run())
    blocked_rows = [line for line in text.splitlines() if "C.1" in line]
    assert blocked_rows, text
    assert "01_provider" in blocked_rows[0]


def test_text_summary_counts_blocked_separately() -> None:
    text = format_text(_run())
    assert "Blocked: 1" in text


def test_text_summary_still_counts_blocked_within_skipped() -> None:
    """``skipped`` keeps its old meaning so no existing consumer changes."""
    text = format_text(_run())
    assert "Skipped: 2" in text


def test_a_blocked_run_with_a_real_failure_is_fail() -> None:
    text = format_text(_run())
    assert "Result: FAIL" in text


def test_plain_skip_row_is_not_labelled_blocked() -> None:
    run = _run(total=1, failed=0, skipped=1, blocked=0, test_results=[_plain_skip()])
    text = format_text(run)
    assert "BLOCKED" not in text
    assert "SKIP" in text


# ------------------------------------------------------------------------- json report


def test_json_report_carries_the_blocked_count() -> None:
    data = json.loads(format_json(_run()))
    assert data["blocked"] == 1


def test_json_report_carries_per_test_blocked_by() -> None:
    data = json.loads(format_json(_run()))
    by_id = {tr["test_id"]: tr for tr in data["test_results"]}
    assert by_id["C.1"]["blocked_by"] == "01_provider"
    assert by_id["C.1"]["skipped"] is True


def test_json_report_omits_blocked_by_on_a_plain_skip() -> None:
    """``exclude_none`` keeps a plain skip's payload identical to 0.9.x."""
    data = json.loads(format_json(_run()))
    by_id = {tr["test_id"]: tr for tr in data["test_results"]}
    assert "blocked_by" not in by_id["S.1"]
