"""Unit tests for report provenance (0.9.0).

A persisted report must be attributable to the engine version and the target
stack that produced it (RGRN-16): ``regrun <version>``, resolved endpoints,
``run_id`` and lock ``target`` in the text header, same fields in report.json.
"""

import json

from regrun.engine.reporter import RunResult, format_json, format_text


def _result(**overrides) -> RunResult:
    fields: dict = {
        "product": "demo",
        "layer": "api",
        "run_id": "17530000001a2b",
        "target": "demo.localhost",
        "regrun_version": "0.9.0",
        "api_endpoint": "http://demo.localhost",
        "mcp_endpoint": "http://demo-mcp.localhost",
        "total": 1,
        "passed": 1,
    }
    fields.update(overrides)
    return RunResult(**fields)


def test_text_header_carries_full_provenance() -> None:
    text = format_text(_result())
    assert "regrun 0.9.0" in text
    assert "endpoint: http://demo.localhost / http://demo-mcp.localhost" in text
    assert "run_id: 17530000001a2b" in text
    assert "target: demo.localhost" in text


def test_text_header_omits_absent_endpoints() -> None:
    text = format_text(_result(api_endpoint=None, mcp_endpoint=None))
    assert "endpoint:" not in text


def test_json_report_carries_provenance_fields() -> None:
    data = json.loads(format_json(_result()))
    assert data["regrun_version"] == "0.9.0"
    assert data["api_endpoint"] == "http://demo.localhost"
    assert data["mcp_endpoint"] == "http://demo-mcp.localhost"
    assert data["run_id"] == "17530000001a2b"
    assert data["target"] == "demo.localhost"


def test_unit_built_results_stay_terse() -> None:
    # Results built without provenance (older tests, ad-hoc tooling) print no
    # provenance lines at all.
    text = format_text(RunResult(product="demo"))
    assert "regrun " not in text.splitlines()[2]
    assert "target:" not in text
