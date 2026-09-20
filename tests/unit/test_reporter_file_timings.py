"""Unit tests for the per-file timing table and budget breaches (0.10.0).

"Where did the time go" must be one line of reading, not archaeology across a
2000-row test table.

Contract:

  * ``build_file_timings(test_results) -> list[FileTiming]`` — one row per
    ``file_stem``: ``tests`` count, summed ``duration_ms``, ``share`` of the run's
    total as a FRACTION (0.0-1.0). Sorted slowest first; ties broken by stem so
    two runs of the same suite render the same table.
  * ``RunResult.file_timings`` is the single source both formatters render, so the
    text table and ``report.json`` can never diverge.
  * ``RunResult.budget_breaches`` names each overrun (file-scoped or run-scoped)
    and the text report renders it.
"""

import json

from regrun.engine.reporter import (
    BudgetBreach,
    RunResult,
    TestResult,
    build_file_timings,
    format_json,
    format_text,
)


def _result(test_id: str, file_stem: str, duration_ms: float) -> TestResult:
    return TestResult(
        test_id=test_id,
        test_name=f"test {test_id}",
        group_name="Surface",
        passed=True,
        duration_ms=duration_ms,
        file_stem=file_stem,
    )


def _results() -> list[TestResult]:
    """600ms in 01_slow (2 tests), 300ms in 02_medium (1), 100ms in 03_fast (1)."""
    return [
        _result("A.1", "01_slow", 400.0),
        _result("A.2", "01_slow", 200.0),
        _result("B.1", "02_medium", 300.0),
        _result("C.1", "03_fast", 100.0),
    ]


# ------------------------------------------------------------------- build_file_timings


def test_timings_are_sorted_slowest_first() -> None:
    rows = build_file_timings(_results())
    assert [row.stem for row in rows] == ["01_slow", "02_medium", "03_fast"]


def test_timings_sum_durations_per_file() -> None:
    rows = {row.stem: row for row in build_file_timings(_results())}
    assert rows["01_slow"].duration_ms == 600.0
    assert rows["03_fast"].duration_ms == 100.0


def test_timings_count_tests_per_file() -> None:
    rows = {row.stem: row for row in build_file_timings(_results())}
    assert rows["01_slow"].tests == 2
    assert rows["02_medium"].tests == 1


def test_share_is_the_fraction_of_total_duration() -> None:
    rows = {row.stem: row for row in build_file_timings(_results())}
    assert rows["01_slow"].share == 0.6
    assert rows["02_medium"].share == 0.3
    assert rows["03_fast"].share == 0.1


def test_shares_sum_to_one() -> None:
    rows = build_file_timings(_results())
    assert sum(row.share for row in rows) == 1.0


def test_empty_run_yields_no_timing_rows() -> None:
    assert build_file_timings([]) == []


def test_zero_duration_run_does_not_divide_by_zero() -> None:
    rows = build_file_timings([_result("A.1", "01_slow", 0.0)])
    assert rows[0].share == 0.0


def test_ties_are_broken_by_stem() -> None:
    rows = build_file_timings([_result("B.1", "02_beta", 100.0), _result("A.1", "01_alpha", 100.0)])
    assert [row.stem for row in rows] == ["01_alpha", "02_beta"]


# ------------------------------------------------------------------------- text report


def _run(**overrides: object) -> RunResult:
    results = _results()
    fields: dict = {
        "product": "demo",
        "total": len(results),
        "passed": len(results),
        "duration_ms": 1000.0,
        "test_results": results,
        "file_timings": build_file_timings(results),
    }
    fields.update(overrides)
    return RunResult(**fields)


def test_text_report_has_a_per_file_timing_section() -> None:
    text = format_text(_run())
    assert "01_slow" in text
    assert "02_medium" in text
    assert "03_fast" in text


def test_text_timing_section_is_ordered_slowest_first() -> None:
    text = format_text(_run())
    assert text.index("01_slow") < text.index("02_medium") < text.index("03_fast")


def test_json_report_carries_file_timings() -> None:
    data = json.loads(format_json(_run()))
    rows = data["file_timings"]
    assert [row["stem"] for row in rows] == ["01_slow", "02_medium", "03_fast"]
    assert rows[0]["tests"] == 2
    assert rows[0]["duration_ms"] == 600.0
    assert rows[0]["share"] == 0.6


def test_file_timings_default_to_empty() -> None:
    assert RunResult(product="demo").file_timings == []


# ---------------------------------------------------------------------- budget breaches


def test_budget_breaches_default_to_empty() -> None:
    assert RunResult(product="demo").budget_breaches == []


def test_text_report_names_a_file_budget_breach() -> None:
    breach = BudgetBreach(scope="file", stem="01_slow", budget_seconds=0.1, actual_seconds=0.6)
    text = format_text(_run(budget_breaches=[breach]))
    assert "01_slow" in text
    assert "BUDGET" in text.upper()


def test_text_report_names_a_run_budget_breach() -> None:
    breach = BudgetBreach(scope="run", stem=None, budget_seconds=0.5, actual_seconds=1.0)
    text = format_text(_run(budget_breaches=[breach]))
    assert "BUDGET" in text.upper()


def test_json_report_carries_budget_breaches() -> None:
    breach = BudgetBreach(scope="file", stem="01_slow", budget_seconds=0.1, actual_seconds=0.6)
    data = json.loads(format_json(_run(budget_breaches=[breach])))
    assert data["budget_breaches"] == [
        {
            "scope": "file",
            "stem": "01_slow",
            "budget_seconds": 0.1,
            "actual_seconds": 0.6,
        }
    ]
