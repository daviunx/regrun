"""SQL runner: executes psql statements against a Postgres database.

Absorbs the psql half of the fleet's hand-rolled bash steps and resolves the
docker-exec-vs-direct-psql dispatch once, in Python. No new DB driver is added:
the runner shells out to ``psql`` exactly as the bash steps did, so the auth /
driver surface is unchanged.

Dispatch: probe for docker (``shutil.which("docker")`` + ``docker info``, cached
per runner instance). When docker is available, statements run via
``docker exec -i {container} psql -U {user} -d {db}``; otherwise via
``psql {fallback_dsn}``. Every invocation carries ``-v ON_ERROR_STOP=1 -q -t
-A`` and receives the statement on stdin (heredoc parity with the bash steps).

Note: ``asyncio`` and ``shutil`` are referenced module-qualified on purpose —
the unit tests monkeypatch ``sql_runner.asyncio.create_subprocess_exec`` and
``sql_runner.shutil.which`` as their subprocess/probe seams.
"""

import asyncio
import shutil
import time

import structlog

from regrun.engine.variables import VariableStore
from regrun.models import SqlConnection, Test
from regrun.runners.bash_runner import _parse_stdout
from regrun.runners.base import RequestEcho, RunnerResponse

logger = structlog.get_logger()

# Flags applied to every psql invocation:
#   -v ON_ERROR_STOP=1  fail (non-zero exit) on the first SQL error
#   -q                  quiet (suppress informational chatter)
#   -t                  tuples-only (no header/footer decoration)
#   -A                  unaligned (raw column output, JSON-safe)
_PSQL_FLAGS = ["-v", "ON_ERROR_STOP=1", "-q", "-t", "-A"]


class SqlRunner:
    """Runner for ``runner: sql`` tests using ``psql`` via asyncio subprocesses."""

    def __init__(self, sql_connection: SqlConnection | None, cwd: str, timeout: int = 30) -> None:
        self._conn = sql_connection
        self._cwd = cwd
        self._timeout = timeout
        # Docker availability is probed once per runner instance and cached.
        self._docker_available: bool | None = None

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Render + execute the test's SQL statement and return the response.

        Returns a :class:`RunnerResponse` with the psql exit code in
        ``status_code``, the parsed stdout (JSON-or-string) in ``body``, and any
        stderr / non-zero exit surfaced via ``error``.
        """
        if self._conn is None:
            return RunnerResponse(
                status_code=126,
                error="sql runner requires meta.sql_connection",
                request_echo=RequestEcho(runner="sql", sql=test.sql),
            )
        if not test.sql:
            return RunnerResponse(
                status_code=126,
                error="No sql statement defined for sql test",
                request_echo=RequestEcho(runner="sql", sql=None),
            )

        timeout = test.timeout if test.timeout is not None else self._timeout
        statement = variables.render_string(test.sql)
        argv = await self._build_argv(variables)

        start = time.monotonic()
        stdout, stderr, exit_code = await self._run_psql(argv, statement, timeout)
        duration_ms = (time.monotonic() - start) * 1000

        echo = RequestEcho(runner="sql", sql=statement)

        if exit_code != 0:
            error_msg = f"psql exited with code {exit_code}"
            if stderr:
                error_msg = f"{error_msg}: {stderr[:500]}"
            logger.warning("sql_command_failed", test_id=test.id, exit_code=exit_code)
            return RunnerResponse(
                status_code=exit_code,
                body=_parse_stdout(stdout),
                error=error_msg,
                duration_ms=duration_ms,
                request_echo=echo,
            )

        return RunnerResponse(
            status_code=exit_code,
            body=_parse_stdout(stdout),
            duration_ms=duration_ms,
            request_echo=echo,
        )

    async def _build_argv(self, variables: VariableStore) -> list[str]:
        """Build the psql argv, choosing docker-exec or direct dispatch."""
        assert self._conn is not None
        if await self._docker_is_available():
            container = variables.render_string(self._conn.docker_container)
            user = variables.render_string(self._conn.docker_user)
            database = variables.render_string(self._conn.database)
            return [
                "docker",
                "exec",
                "-i",
                container,
                "psql",
                "-U",
                user,
                "-d",
                database,
                *_PSQL_FLAGS,
            ]
        fallback_dsn = variables.render_string(self._conn.fallback_dsn)
        return ["psql", fallback_dsn, *_PSQL_FLAGS]

    async def _docker_is_available(self) -> bool:
        """Probe for a usable docker daemon (``docker info``), cached per instance.

        Mirrors the ``command -v docker && docker info`` guard the bash steps
        hand-rolled: ``docker`` must be on PATH AND the daemon must respond.
        """
        if self._docker_available is not None:
            return self._docker_available

        available = False
        if shutil.which("docker") is not None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "info",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                rc = await proc.wait()
                available = rc == 0
            except OSError:
                available = False

        self._docker_available = available
        return available

    async def _run_psql(
        self, argv: list[str], statement: str, timeout: int
    ) -> tuple[str, str, int]:
        """Run one psql invocation, feeding ``statement`` on stdin.

        Returns ``(stdout, stderr, exit_code)``.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=statement.encode("utf-8")),
                timeout=timeout,
            )

            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0
            return stdout, stderr, exit_code

        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            logger.error("sql_command_timeout", timeout=timeout)
            return "", f"SQL command timed out after {timeout}s", 124

        except OSError as e:
            logger.error("sql_command_oserror", error=str(e))
            return "", f"OS error: {e}", 126
