"""Bash command runner for setup surface tests."""

import asyncio
import json
import time

import structlog

from regrun.engine.variables import VariableStore
from regrun.models import Test
from regrun.runners.base import RunnerResponse

logger = structlog.get_logger()


class BashRunner:
    """Runner for setup/bash surface tests using asyncio subprocesses.

    Executes shell commands sequentially, captures stdout for assertions
    and variable extraction. Commands run via ``asyncio.create_subprocess_shell``
    to support pipes, redirects, and shell expansions.
    """

    def __init__(self, cwd: str, timeout: int = 30) -> None:
        self._cwd = cwd
        self._timeout = timeout

    async def execute(self, test: Test, variables: VariableStore) -> RunnerResponse:
        """Execute all commands in a bash test sequentially.

        Each command can capture stdout into variables that subsequent commands
        in the same test can reference. Stops on the first non-zero exit code.

        Args:
            test: The test definition with ``commands`` list.
            variables: The current variable store for template rendering and capture.

        Returns:
            RunnerResponse with exit code in ``status_code``, stdout in ``body``,
            and stderr in ``error`` (only if a command failed).
        """
        if not test.commands:
            return RunnerResponse(
                status_code=0,
                body="",
                error="No commands defined for bash test",
            )

        # Per-test ``timeout`` (seconds) overrides the runner default when set.
        # Long-running polls (worker re-index waits) declare a wider budget in
        # YAML; without this every command was capped at the construction-time
        # default and killed mid-poll.
        timeout = test.timeout if test.timeout is not None else self._timeout

        start = time.monotonic()
        last_stdout = ""
        last_exit_code = 0
        collected_stderr: list[str] = []

        for idx, bash_cmd in enumerate(test.commands):
            # Render Jinja2 variables in the command string
            rendered_cmd = variables.render_string(bash_cmd.cmd)

            logger.debug(
                "bash_command_start",
                test_id=test.id,
                command_index=idx,
                cmd=rendered_cmd[:200],
            )

            stdout, stderr, exit_code = await self._run_command(rendered_cmd, timeout)

            logger.debug(
                "bash_command_done",
                test_id=test.id,
                command_index=idx,
                exit_code=exit_code,
                stdout_len=len(stdout),
                stderr_len=len(stderr),
            )

            last_stdout = stdout
            last_exit_code = exit_code

            if stderr:
                collected_stderr.append(stderr)

            # Capture per-command variables
            if bash_cmd.capture:
                captured = _extract_captures(bash_cmd.capture, stdout)
                variables.merge(captured)
                logger.debug(
                    "bash_captured",
                    test_id=test.id,
                    command_index=idx,
                    variables=list(captured.keys()),
                )

            # Stop on non-zero exit code
            if exit_code != 0:
                duration_ms = (time.monotonic() - start) * 1000
                error_msg = f"Command {idx} exited with code {exit_code}"
                if stderr:
                    error_msg = f"{error_msg}: {stderr[:500]}"

                logger.warning(
                    "bash_command_failed",
                    test_id=test.id,
                    command_index=idx,
                    exit_code=exit_code,
                    stderr=stderr[:500],
                )

                return RunnerResponse(
                    status_code=exit_code,
                    body=_parse_stdout(last_stdout),
                    error=error_msg,
                    duration_ms=duration_ms,
                )

        duration_ms = (time.monotonic() - start) * 1000

        return RunnerResponse(
            status_code=last_exit_code,
            body=_parse_stdout(last_stdout),
            duration_ms=duration_ms,
        )

    async def _run_command(self, cmd: str, timeout: int) -> tuple[str, str, int]:
        """Run a single shell command with timeout.

        Args:
            cmd: The shell command string to execute.
            timeout: Wall-clock timeout in seconds for this command.

        Returns:
            Tuple of (stdout, stderr, exit_code).
        """
        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            return stdout, stderr, exit_code

        except asyncio.TimeoutError:
            # Kill the subprocess on timeout
            if process.returncode is None:
                process.kill()
                await process.wait()

            logger.error(
                "bash_command_timeout",
                cmd=cmd[:200],
                timeout=timeout,
            )
            return "", f"Command timed out after {timeout}s", 124

        except OSError as e:
            logger.error("bash_command_oserror", cmd=cmd[:200], error=str(e))
            return "", f"OS error: {e}", 126


def _parse_stdout(stdout: str) -> dict | str:
    """Attempt to parse stdout as JSON; fall back to raw string.

    Args:
        stdout: The raw stdout string from the command.

    Returns:
        Parsed dict if stdout is valid JSON, otherwise the raw string.
    """
    if not stdout:
        return stdout

    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout


def _extract_captures(
    capture_spec: dict[str, str],
    stdout: str,
) -> dict[str, str]:
    """Extract capture values from command stdout.

    Supports two capture modes:
    - ``"stdout"``: captures the entire stripped stdout as the value.
    - JSONPath expression: parses stdout as JSON and evaluates the path.

    Args:
        capture_spec: Mapping of variable_name -> ``"stdout"`` or JSONPath expr.
        stdout: The raw stdout string from the command.

    Returns:
        Dict of variable_name -> extracted string value.
    """
    from jsonpath_ng import parse as jsonpath_parse

    results: dict[str, str] = {}

    for var_name, expression in capture_spec.items():
        if expression == "stdout":
            results[var_name] = stdout
            continue

        # Try JSONPath on parsed JSON stdout
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "bash_capture_not_json",
                variable=var_name,
                expression=expression,
                stdout_preview=stdout[:100],
            )
            continue

        if not isinstance(parsed, dict):
            logger.warning(
                "bash_capture_not_dict",
                variable=var_name,
                expression=expression,
            )
            continue

        try:
            parsed_expr = jsonpath_parse(expression)
            matches = parsed_expr.find(parsed)
            if matches:
                results[var_name] = str(matches[0].value)
                logger.debug(
                    "bash_captured_jsonpath",
                    variable=var_name,
                    value=results[var_name][:100],
                )
            else:
                logger.warning(
                    "bash_capture_no_match",
                    variable=var_name,
                    expression=expression,
                )
        except Exception as e:
            logger.error(
                "bash_capture_error",
                variable=var_name,
                expression=expression,
                error=str(e),
            )

    return results
