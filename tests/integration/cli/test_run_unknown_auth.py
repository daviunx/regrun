"""CLI integration tests: an undefined auth profile FAILS the test loudly.

Before 0.9.1 a test whose ``auth:`` (or ``meta.default_auth``) named a profile
absent from that file's ``auth:`` block logged a warning and sent the request
with NO credentials — a 401-instead-of-403 that reads like a product bug
(auth profiles are per-file; only captured variables propagate cross-file).
Same closed-world doctrine as strict-vars: dangling references fail, never
silently weaken the request.

The guard fires BEFORE any request is built, so these suites point at an
unroutable endpoint on purpose — a guard miss would surface as a connection
error instead of the unknown-auth message.
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli
from regrun.engine.executor import AUTH_CONSUMING_RUNNERS
from regrun.engine.linter import _AUTH_CONSUMING_RUNNERS


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _httpx_suite(
    *,
    test_auth: str | None = None,
    default_auth: str | None = None,
    auth_block: dict | None = None,
) -> dict:
    meta: dict = {
        "product": "demo",
        "layer": "api",
        "runner": "httpx",
        "endpoint": "http://127.0.0.1:1",  # unroutable — the guard must fire first
    }
    if default_auth is not None:
        meta["default_auth"] = default_auth
    test: dict = {
        "id": "UA.1",
        "name": "t",
        "method": "GET",
        "path": "/x",
        "assert": {"status": 200},
    }
    if test_auth is not None:
        test["auth"] = test_auth
    doc: dict = {
        "meta": meta,
        "groups": [{"id": 1, "name": "G", "priority": "high", "tests": [test]}],
    }
    if auth_block is not None:
        doc["auth"] = auth_block
    return doc


def _invoke(test_dir: Path, runs_dir: Path, *args: str):
    return CliRunner().invoke(
        cli, ["run", str(test_dir), *args], env={"REGRUN_RUNS_DIR": str(runs_dir)}
    )


def test_undefined_test_auth_fails_loudly(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _httpx_suite(test_auth="ghost"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "unknown auth profile" in result.output
    assert "'ghost'" in result.output
    # The request must never have been attempted.
    assert "Connection" not in result.output


def test_undefined_default_auth_fails_loudly(tmp_path: Path) -> None:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _httpx_suite(default_auth="ghost"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "unknown auth profile" in result.output
    assert "meta.default_auth" in result.output


def test_auth_none_is_exempt(tmp_path: Path) -> None:
    # 'none' opts out of auth entirely; the run proceeds to the (unroutable)
    # request and fails on the connection — NOT on the auth guard.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _httpx_suite(test_auth="none"))

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "unknown auth profile" not in result.output


def test_defined_profile_passes_guard(tmp_path: Path) -> None:
    # Profile IS defined -> the guard stays silent and the failure is the
    # unroutable endpoint, proving the guard has zero false positives.
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(
        test_dir,
        "01_api.yaml",
        _httpx_suite(test_auth="prod", auth_block={"prod": {"type": "bearer", "token": "t"}}),
    )

    result = _invoke(test_dir, tmp_path / "runs")

    assert result.exit_code == 1, result.output
    assert "unknown auth profile" not in result.output


def test_auth_consuming_runner_sets_lockstep() -> None:
    # The linter deliberately imports nothing from the engine — this is the
    # drift guard for its private copy of the runner set.
    assert AUTH_CONSUMING_RUNNERS == _AUTH_CONSUMING_RUNNERS
