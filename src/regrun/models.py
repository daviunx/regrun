"""Pydantic models for YAML regression test file schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SqlConnection(BaseModel):
    """Connection descriptor for the ``sql`` runner.

    All values are Jinja-renderable strings so the product-prefixed env
    convention is preserved (e.g. ``database: "{{ env.get('RALLY_DB',
    'rally_prod') }}"``) without introducing any new ``REGRUN_SQL_*`` vars.
    The runner probes for docker at run time: when available it uses
    ``docker exec -i {docker_container} psql -U {docker_user} -d {database}``;
    otherwise it falls back to ``psql {fallback_dsn}``.
    """

    model_config = ConfigDict(strict=False)

    docker_container: str
    docker_user: str
    database: str
    fallback_dsn: str


class TestMeta(BaseModel):
    """Top-level metadata for a test file."""

    model_config = ConfigDict(strict=True)

    product: str
    layer: Literal["api", "mcp", "setup", "chat"]
    runner: Literal["httpx", "fastmcp", "bash", "websocket", "sql"]
    endpoint: str | None = None
    mcp_endpoint: str | None = None
    default_auth: str | None = None
    env_file: str | None = None
    sql_connection: SqlConnection | None = None
    # Strict variable resolution (default ON): an unresolved {{VAR}} in any
    # rendered string FAILS the test instead of silently becoming a literal.
    # Per-file opt-out for suites that deliberately template literal braces.
    strict_vars: bool = True
    # Declared file dependencies, by stem (filename without ``.yaml``). A file
    # whose dependency FAILED is not run: its tests are reported BLOCKED rather
    # than failed, so one broken producer yields one actionable failure instead
    # of a cascade. Setup is the bootstrap contract every file already depends
    # on and is never listed. Empty means "depends on nothing but setup".
    requires: list[str] = Field(default_factory=list)
    # The file asserts process-global behaviour (a rate limit, a global counter,
    # a singleton lock) and therefore cannot share a run with unrelated traffic.
    # Sharding keeps every serial file to itself.
    serial: bool = False
    # Per-file wall-clock budget. Exceeding it is reported as a breach; whether
    # a breach reddens the run is the caller's opt-in, never implicit.
    budget_seconds: float | None = Field(default=None, gt=0)
    # Read by external orchestration (the health probe that waits for a stack
    # before invoking the runner) from the first suite file. The engine never
    # consumes them; they are declared so a suite carrying them still parses.
    health_path: str | None = None
    mcp_health_path: str | None = None


class AuthConfig(BaseModel):
    """Authentication configuration for a named auth context."""

    model_config = ConfigDict(strict=True)

    type: Literal["bearer", "api_key"]
    token: str
    org_header: str | None = None


class Assertion(BaseModel):
    """Assertion block for a test. Mapped from the YAML 'assert' key.

    ``extra="forbid"`` is load-bearing: a typo'd key (``statuss``, ``jsonpath``)
    would otherwise be silently ignored, the block would evaluate zero
    assertions, and the executor's ``all([])`` would report the test PASSED
    having checked nothing — the same false-green family as
    ``eventually.max_attempts: 0`` (closed in 0.8.3).
    """

    model_config = ConfigDict(strict=False, extra="forbid")

    status: int | list[int] | None = None
    is_error: bool | None = None
    has_error: bool | None = None
    last_exit_code: int | None = None
    json_path: dict[str, dict] | None = None
    contains: str | None = None


class BashCommand(BaseModel):
    """A single command in a bash test."""

    model_config = ConfigDict(strict=False)

    cmd: str
    capture: dict[str, str] | None = None


class EventuallyConfig(BaseModel):
    """Retry/poll configuration for asserting eventually-consistent operations.

    When present on a test, the request + assertions are re-run until all
    assertions pass or ``max_attempts`` is exhausted (see engine/retry.py).
    """

    model_config = ConfigDict(strict=False)

    # ge=1 is load-bearing, not cosmetic. At 0 the retry loop body never runs,
    # run_with_retry returns (None, []), and the executor's
    # `all_passed = all(assertion_results)` is True on an empty list -- the test
    # reports PASSED having evaluated ZERO assertions.
    max_attempts: int = Field(default=10, ge=1)
    interval: float = Field(default=2.0, ge=0.0)  # seconds between attempts
    backoff: float = Field(default=1.0, ge=0.0)  # multiplier (1.0 = fixed interval)
    initial_delay: float = Field(default=0.0, ge=0.0)  # optional wait before the first attempt


class WebSocketConfig(BaseModel):
    """Product-agnostic WebSocket event parsing configuration."""

    model_config = ConfigDict(strict=False)

    event_type_field: str = "event_type"
    event_type_fallback: str = "type"
    text_event: str = "text_delta"
    text_field: str = "data.delta"
    tool_call_event: str = "tool_call"
    tool_name_field: str = "data.tool_name"
    error_event: str = "error"
    error_field: str = "data.content"


class Test(BaseModel):
    """A single test case within a group.

    ``extra="forbid"`` so a misspelled field (``bodyy``, ``json:`` instead of
    ``body:``) fails at load instead of being silently dropped. Verified against
    every fleet suite (71 files) before enabling: zero extra keys in use.
    """

    model_config = ConfigDict(strict=False, populate_by_name=True, extra="forbid")

    id: str
    name: str

    # API-specific fields
    method: str | None = None
    path: str | None = None
    auth: str | None = None
    org_header: bool | None = None
    body: dict | None = None
    query_params: dict[str, str] | None = None

    # MCP-specific fields
    tool: str | None = None
    args: dict | None = None

    # Bash-specific fields
    commands: list[BashCommand] | None = None

    # SQL-specific field (statement(s); Jinja-rendered like ``commands``)
    sql: str | None = None

    # WebSocket-specific fields
    url: str | None = None
    send: dict | None = None
    wait_for: str | None = None
    timeout: int | None = None
    ws_config: WebSocketConfig | None = None

    # Per-test runner override (used when meta.runner differs, e.g. setup file)
    runner: Literal["httpx", "fastmcp", "bash", "websocket", "sql"] | None = None

    # Common fields
    assert_: Assertion = Field(alias="assert")
    capture: dict[str, str] | None = None

    # Retry/poll for eventually-consistent operations (see engine/retry.py)
    eventually: EventuallyConfig | None = None


class Group(BaseModel):
    """A named group of tests."""

    model_config = ConfigDict(strict=False)

    name: str
    id: int
    priority: Literal["high", "medium", "low"] = "medium"
    context: Literal["prod", "fresh", "both"] = "prod"
    # Sweep-first cleanup discipline: a cleanup-flagged group survives
    # --group/--priority filtering (like the setup layer) and still EXECUTES
    # when --fail-fast aborts the run, so the environment is swept even on
    # partial/aborted runs. Only capture-INDEPENDENT pattern sweeps may be
    # flagged (see testing/regression.md). Suppress with --skip-cleanup.
    cleanup: bool = False
    tests: list[Test]


class PreflightCheck(BaseModel):
    """A dependency-health probe run once, before any group.

    A preflight check carries a ``Test``-shaped body (any runner) plus a
    ``name`` naming the dependency it asserts (used in the abort message).
    Checks are strictly read-only probes: ``eventually:`` and ``capture:`` are
    REJECTED at validation — a health probe must not retry a degraded backend
    into looking healthy, nor feed run state. ``timeout`` defaults to 10s.
    """

    model_config = ConfigDict(strict=False, populate_by_name=True)

    name: str

    # Test-shaped body (subset relevant to a read-only probe on any surface).
    runner: Literal["httpx", "fastmcp", "bash", "websocket", "sql"] | None = None
    method: str | None = None
    path: str | None = None
    auth: str | None = None
    org_header: bool | None = None
    body: dict | None = None
    query_params: dict[str, str] | None = None
    tool: str | None = None
    args: dict | None = None
    commands: list[BashCommand] | None = None
    sql: str | None = None

    assert_: Assertion = Field(alias="assert")
    timeout: float = 10.0

    @model_validator(mode="before")
    @classmethod
    def _reject_stateful_keys(cls, data: object) -> object:
        """Preflight probes must be stateless: forbid ``eventually`` / ``capture``."""
        if isinstance(data, dict):
            if "eventually" in data:
                raise ValueError("preflight checks may not use 'eventually:' (read-only probes)")
            if "capture" in data:
                raise ValueError("preflight checks may not use 'capture:' (read-only probes)")
        return data

    def as_test(self) -> "Test":
        """Materialize the probe as a ``Test`` for the runner layer."""
        return Test(
            id=f"preflight:{self.name}",
            name=self.name,
            runner=self.runner,
            method=self.method,
            path=self.path,
            auth=self.auth,
            org_header=self.org_header,
            body=self.body,
            query_params=self.query_params,
            tool=self.tool,
            args=self.args,
            commands=self.commands,
            sql=self.sql,
            timeout=int(self.timeout),
            assert_=self.assert_,
        )


class SweepStep(BaseModel):
    """A pre-run sweep step: pattern-based cleanup of prior-run artifacts.

    Declared as a top-level ``sweep:`` block (peer of ``preflight:``), typically
    in the setup file. Steps run once, after ``preflight:`` and before any
    group; a failure ABORTS the run with zero groups executed — a suite must
    not create fixtures into an environment it could not sweep. Steps are
    restricted to the ``bash`` / ``sql`` / ``httpx`` runners and MUST be
    capture-independent: ``capture:`` (test-level or per-command) is rejected
    at validation, as is ``eventually:`` (a sweep is a delete, not a poll).
    ``timeout`` defaults to 60s (pattern deletes can be slower than health
    probes). ``--skip-sweep`` suppresses the block; ``cleanup: true`` group
    semantics are untouched and remain the tail-end backstop.
    """

    model_config = ConfigDict(strict=False, populate_by_name=True)

    name: str

    # Test-shaped body (subset relevant to a capture-independent sweep).
    runner: Literal["httpx", "bash", "sql"] | None = None
    method: str | None = None
    path: str | None = None
    auth: str | None = None
    org_header: bool | None = None
    body: dict | None = None
    query_params: dict[str, str] | None = None
    commands: list[BashCommand] | None = None
    sql: str | None = None

    assert_: Assertion = Field(alias="assert")
    timeout: float = 60.0

    @model_validator(mode="before")
    @classmethod
    def _reject_capture(cls, data: object) -> object:
        """Sweeps must be capture-independent, and are deletes, not polls."""
        if isinstance(data, dict):
            if "capture" in data:
                raise ValueError(
                    "sweep steps may not use 'capture:' (sweeps must be capture-independent)"
                )
            if "eventually" in data:
                raise ValueError("sweep steps may not use 'eventually:' (a sweep is not a poll)")
            for cmd in data.get("commands") or []:
                if isinstance(cmd, dict) and "capture" in cmd:
                    raise ValueError(
                        "sweep steps may not use per-command 'capture:' "
                        "(sweeps must be capture-independent)"
                    )
        return data

    def as_test(self) -> "Test":
        """Materialize the step as a ``Test`` for the runner layer."""
        return Test(
            id=f"sweep:{self.name}",
            name=self.name,
            runner=self.runner,
            method=self.method,
            path=self.path,
            auth=self.auth,
            org_header=self.org_header,
            body=self.body,
            query_params=self.query_params,
            commands=self.commands,
            sql=self.sql,
            timeout=int(self.timeout),
            assert_=self.assert_,
        )


class TestFile(BaseModel):
    """Top-level model representing a parsed YAML test file."""

    model_config = ConfigDict(strict=False)

    meta: TestMeta
    variables: dict[str, str] = Field(default_factory=dict)
    auth: dict[str, AuthConfig] = Field(default_factory=dict)
    preflight: list[PreflightCheck] | None = None
    sweep: list[SweepStep] | None = None
    groups: list[Group]
