"""Unit tests for the `sql` runner (Phase 1) — RED gate.

These tests are written BEFORE the implementation exists. They assert the
planned 0.8.0 contract:

  * `runner: sql` accepted by both `TestMeta.runner` and `Test.runner` Literals.
  * new per-test `sql:` field on `Test`.
  * new `SqlConnection` model + `TestMeta.sql_connection`.
  * new `RequestEcho.sql` echo field.
  * `SqlRunner` dispatch: docker-probe true -> `docker exec -i {c} psql -U {u}
    -d {db}`; probe false -> `psql {fallback_dsn}`; `-v ON_ERROR_STOP=1` on
    every invocation; stdout JSON-vs-string parse parity with the bash runner.

The runner branch tests fake the subprocess layer (`asyncio.create_subprocess_exec`)
and the docker probe (`shutil.which`) via monkeypatch — no real docker/psql. The
schema tests use only already-existing modules so they fail on
ValidationError/AttributeError (meaningful red), not on a missing import.
"""

import asyncio
import importlib

import pytest
from pydantic import ValidationError

from regrun import models
from regrun.engine.variables import VariableStore
from regrun.models import Assertion
from regrun.runners.base import RequestEcho


# ---------------------------------------------------------------------------
# Schema acceptance (no sql_runner import — fails on Validation/AttributeError)
# ---------------------------------------------------------------------------


def test_test_meta_runner_literal_accepts_sql() -> None:
    meta = models.TestMeta(product="demo", layer="api", runner="sql")
    assert meta.runner == "sql"


def test_test_runner_literal_accepts_sql() -> None:
    t = models.Test.model_validate(
        {"id": "Q.1", "name": "q", "runner": "sql", "sql": "SELECT 1;", "assert": {"last_exit_code": 0}}
    )
    assert t.runner == "sql"


def test_test_has_sql_field() -> None:
    t = models.Test.model_validate(
        {"id": "Q.1", "name": "q", "sql": "SELECT count(*) FROM t;", "assert": {"last_exit_code": 0}}
    )
    assert t.sql == "SELECT count(*) FROM t;"


def test_sql_connection_model_exists() -> None:
    conn = models.SqlConnection(
        docker_container="regr_pg",
        docker_user="postgres",
        database="testdb",
        fallback_dsn="postgres://localhost/testdb",
    )
    assert conn.docker_container == "regr_pg"
    assert conn.docker_user == "postgres"
    assert conn.database == "testdb"
    assert conn.fallback_dsn == "postgres://localhost/testdb"


def test_test_meta_sql_connection_field() -> None:
    meta = models.TestMeta.model_validate(
        {
            "product": "demo",
            "layer": "api",
            "runner": "sql",
            "sql_connection": {
                "docker_container": "regr_pg",
                "docker_user": "postgres",
                "database": "testdb",
                "fallback_dsn": "postgres://localhost/testdb",
            },
        }
    )
    assert meta.sql_connection is not None
    assert meta.sql_connection.database == "testdb"


def test_test_file_accepts_sql_runner_and_connection() -> None:
    tf = models.TestFile.model_validate(
        {
            "meta": {
                "product": "demo",
                "layer": "api",
                "runner": "sql",
                "sql_connection": {
                    "docker_container": "regr_pg",
                    "docker_user": "postgres",
                    "database": "testdb",
                    "fallback_dsn": "postgres://localhost/testdb",
                },
            },
            "groups": [
                {
                    "id": 5,
                    "name": "SQL",
                    "tests": [
                        {"id": "Q.1", "name": "q", "sql": "SELECT 1;", "assert": {"last_exit_code": 0}}
                    ],
                }
            ],
        }
    )
    assert tf.meta.sql_connection.database == "testdb"
    assert tf.groups[0].tests[0].sql == "SELECT 1;"


def test_request_echo_has_sql_field() -> None:
    echo = RequestEcho(runner="sql", sql="SELECT 1;")
    assert echo.sql == "SELECT 1;"


def test_old_runner_literals_still_rejected_for_bad_value() -> None:
    # Guard: adding "sql" must not open the Literal to arbitrary strings.
    with pytest.raises(ValidationError):
        models.TestMeta(product="demo", layer="api", runner="postgres")


# ---------------------------------------------------------------------------
# SqlRunner dispatch (fake subprocess + docker probe)
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.inputs: list[object] = []


class _FakeProc:
    def __init__(self, rec: _Recorder, out: bytes = b"", err: bytes = b"", rc: int = 0) -> None:
        self._rec = rec
        self._out = out
        self._err = err
        self.returncode = rc

    async def communicate(self, input=None):  # noqa: A002 - mirrors asyncio API
        self._rec.inputs.append(input)
        return self._out, self._err

    async def wait(self):
        return self.returncode

    def kill(self) -> None:
        pass


def _make_exec(rec: _Recorder, psql_stdout: bytes = b"", psql_rc: int = 0):
    async def _exec(*argv, **kwargs):
        rec.calls.append(tuple(argv))
        # docker availability probe ("docker info") always succeeds.
        if "info" in argv and "psql" not in argv:
            return _FakeProc(rec, out=b"", err=b"", rc=0)
        return _FakeProc(rec, out=psql_stdout, err=b"", rc=psql_rc)

    return _exec


def _load(monkeypatch, *, docker: bool, psql_stdout: bytes = b"", psql_rc: int = 0):
    """Import the (not-yet-existing) sql_runner and fake its subprocess/probe.

    Raises ModuleNotFoundError until Phase 1 lands — a valid red signal.
    """
    sql_mod = importlib.import_module("regrun.runners.sql_runner")
    rec = _Recorder()
    monkeypatch.setattr(sql_mod.asyncio, "create_subprocess_exec", _make_exec(rec, psql_stdout, psql_rc))
    monkeypatch.setattr(
        sql_mod.shutil,
        "which",
        (lambda name: "/usr/bin/docker") if docker else (lambda name: None),
    )
    return sql_mod, sql_mod.SqlRunner, rec


def _conn():
    return models.SqlConnection(
        docker_container="regr_pg",
        docker_user="postgres",
        database="testdb",
        fallback_dsn="postgres://localhost/testdb",
    )


def _sql_test(statement: str = "SELECT 1;"):
    return models.Test(id="Q.1", name="q", sql=statement, assert_=Assertion())


def _psql_call(rec: _Recorder) -> tuple[str, ...]:
    psql_calls = [c for c in rec.calls if "psql" in c]
    assert psql_calls, f"expected a psql invocation, got: {rec.calls}"
    return psql_calls[-1]


def test_docker_available_branch_uses_docker_exec(monkeypatch) -> None:
    _mod, SqlRunner, rec = _load(monkeypatch, docker=True)
    runner = SqlRunner(sql_connection=_conn(), cwd="/tmp", timeout=10)
    asyncio.run(runner.execute(_sql_test(), VariableStore()))

    call = _psql_call(rec)
    assert "docker" in call and "exec" in call and "-i" in call
    assert "regr_pg" in call
    assert "-U" in call and "postgres" in call
    assert "-d" in call and "testdb" in call
    assert "ON_ERROR_STOP=1" in call
    assert "-v" in call


def test_docker_absent_branch_uses_fallback_dsn(monkeypatch) -> None:
    _mod, SqlRunner, rec = _load(monkeypatch, docker=False)
    runner = SqlRunner(sql_connection=_conn(), cwd="/tmp", timeout=10)
    asyncio.run(runner.execute(_sql_test(), VariableStore()))

    assert all("docker" not in c for c in rec.calls), f"must not invoke docker: {rec.calls}"
    call = _psql_call(rec)
    assert call[0] == "psql"
    assert any("postgres://localhost/testdb" in tok for tok in call)
    assert "ON_ERROR_STOP=1" in call


def test_on_error_stop_present_and_nonzero_exit_populates_error(monkeypatch) -> None:
    _mod, SqlRunner, rec = _load(monkeypatch, docker=False, psql_rc=1)
    runner = SqlRunner(sql_connection=_conn(), cwd="/tmp", timeout=10)
    resp = asyncio.run(runner.execute(_sql_test(), VariableStore()))

    assert "ON_ERROR_STOP=1" in _psql_call(rec)
    assert resp.error is not None


def test_stdout_json_parsed_to_dict(monkeypatch) -> None:
    _mod, SqlRunner, _rec = _load(monkeypatch, docker=False, psql_stdout=b'{"count": 3}')
    runner = SqlRunner(sql_connection=_conn(), cwd="/tmp", timeout=10)
    resp = asyncio.run(runner.execute(_sql_test(), VariableStore()))
    assert resp.body == {"count": 3}


def test_stdout_plain_string_kept_as_string(monkeypatch) -> None:
    _mod, SqlRunner, _rec = _load(monkeypatch, docker=False, psql_stdout=b"hello world")
    runner = SqlRunner(sql_connection=_conn(), cwd="/tmp", timeout=10)
    resp = asyncio.run(runner.execute(_sql_test(), VariableStore()))
    assert resp.body == "hello world"


def test_request_echo_sql_echoes_statement(monkeypatch) -> None:
    _mod, SqlRunner, _rec = _load(monkeypatch, docker=False)
    runner = SqlRunner(sql_connection=_conn(), cwd="/tmp", timeout=10)
    resp = asyncio.run(runner.execute(_sql_test("SELECT 42;"), VariableStore()))
    assert resp.request_echo is not None
    assert resp.request_echo.runner == "sql"
    assert resp.request_echo.sql == "SELECT 42;"
