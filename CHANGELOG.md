# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-07-17

### Added

- **`sql` runner.** A first-class runner for Postgres statements that absorbs the psql half of the fleet's hand-rolled bash steps and resolves the docker-exec-vs-direct-psql dispatch once, in Python — no more copy-pasting the `command -v docker && docker info` guard into every suite. Declare `meta.sql_connection` (`docker_container`, `docker_user`, `database`, `fallback_dsn` — all Jinja-renderable, preserving the product-prefixed env convention) and put SQL in a test's `sql:` field. The runner probes for docker (`shutil.which` + `docker info`, cached per run) → `docker exec -i {container} psql -U {user} -d {db}`, else `psql {fallback_dsn}`; every invocation carries `-v ON_ERROR_STOP=1 -q -t -A` and receives the statement on stdin. Stdout is parsed JSON-or-string exactly like the bash runner, so `contains` / `json_path` on `to_jsonb(...)` output transfer 1:1. **No new DB driver dependency** — it shells out to `psql` just as the bash steps did. Scope is SQL only: app-command exec steps and OpenSearch curl steps stay `runner: bash`. `RequestEcho.sql` echoes the rendered statement into failure diagnostics.
- **`preflight:` dependency-health checks.** A top-level `preflight:` block lists read-only probes (a `Test`-shaped body on any runner + a `name` + a `timeout` defaulting to 10s) that run once, before any group, and abort the whole run in seconds naming the failed dependency — killing the degraded-backend grind regime. Checks are collected across all loaded files in file order; the first failure prints `PREFLIGHT FAILED: <name>` + diagnostics and exits non-zero having executed zero groups. `eventually:` and `capture:` are rejected at validation (a health probe must not retry a degraded backend into looking healthy, nor feed run state). `--skip-preflight` bypasses; `--dry-run` lists the checks; a passing run's report header prints `preflight: N checks passed`.
- **Per-product run lock.** Every run holds an exclusive `fcntl.flock` on `{REGRUN_RUNS_DIR|~/.regrun/runs}/{product}/.lock` for its duration, mechanically enforcing the sweep-first no-concurrency assumption. A second concurrent run for the same product exits code 2 naming the product + lock path. flock self-releases on process death (incl. SIGKILL) — no stale-lock protocol. `--no-lock` bypasses. An unusable runs dir degrades to running unlocked (best-effort), never a crash.
- **Lint rule W006 (warn).** The suite directory declares no `preflight:` block in any file — a missing-dependency-health-probes adoption nudge, complementing the `preflight: N checks` report header so a suite silently ignoring preflight on an old pin stays detectable.

### Compatibility

- `runner: sql` **hard-fails to parse on a pre-0.8.0 binary** (Literal enforcement) — a suite may adopt it only after its CI pin is ≥ 0.8.0. This release migrates no suite YAML; pin bumps and step migration ride sibling consumer tasks.
- `preflight:` is **silently ignored by a pre-0.8.0 binary** (unknown key), mitigated by lint W006 + the `preflight:` report header line.

## [0.7.0] - 2026-07-16

### Added

- **JUnit XML report output for GitLab MR Tests tab.** Every `regrun run` now emits a `junit.xml` alongside the existing `report.txt` and `report.json` in the run artifacts directory (`{REGRUN_RUNS_DIR}/{product}/{timestamp}/junit.xml`). The XML follows the JUnit spec as consumed by GitLab: one `<testsuite>` per source YAML file, one `<testcase>` per test with `classname="{product}.{file_stem}.{group_name}"`. Failed tests carry a `<failure>` element with the full diagnostics body (request echo, response, failed assertions -- same redaction as report.txt), errored tests carry `<error>`, and skipped tests carry `<skipped/>`. All text is XML-escaped; failure/error bodies are capped at 16 KB to avoid GitLab's poor handling of huge bodies. No new CLI flags needed -- JUnit output is always generated. Wire it in CI with:
  ```yaml
  artifacts:
    when: always
    reports:
      junit: regrun-runs/**/junit.xml
    paths:
      - regrun-runs/
  ```
- `TestResult.file_stem` field tracks the source YAML file stem for JUnit suite grouping.

## [0.6.0] - 2026-07-11

### Added

- **Full failure diagnostics by default — one run tells you everything about a failure.** A failing test used to emit a single truncated line (`Status 500 != 200`), forcing a second `--verbose` run (bodies for *every* test) plus manual log archaeology to find the actual cause. Now every failed/errored test carries a `FailureDiagnostics` block — the request echo (method/URL/redacted headers/body, or tool+args, or the rendered bash commands, or ws url/send/wait_for), the response status + body, **all** failed assertions at full length (no 60-char cut), and the `eventually` attempt count — populated automatically. Passing tests stay terse (no diagnostics). A new **`Failures` section** renders between the results table and the summary, so `Result: PASS|FAIL` stays the last line (existing tooling parses it) while a tail-clipped terminal now shows the diagnostics. `--output json` carries `diagnostics` as an additive field (omitted entirely when null); `--verbose` is unchanged.
- **Persistent run artifacts.** Every run — pass, fail, or `--fail-fast` abort — writes the complete `report.txt` + `report.json` to `{REGRUN_RUNS_DIR or ~/.regrun/runs}/{product}/{YYYYMMDD-HHMMSS}/`, and stdout ends with a parseable pointer line `Full report: <path>/report.txt (json: report.json)`. AI agents (and humans) read the file instead of re-running a multi-minute suite to see a truncated error. Logic lives in `engine/artifacts.py`; the diagnostics builder + redaction/truncation helpers in `engine/diagnostics.py`.
- **Secret redaction in diagnostics.** Request headers are redacted at capture time by the canonical `SENSITIVE_PATTERNS` field-name set (observability standard §4 — `authorization`, `*token*`, `*key*`, `cookie`, …), and any resolved auth-token value is scrubbed wherever it appears (e.g. echoed back in a response body). Response bodies are truncated to 2000 chars (`REGRUN_DIAG_BODY_LIMIT` override) with a `…[truncated, N total chars]` annotation. Resolved token values ride an `exclude=True` field and never reach any serialized output.

## [0.5.0] - 2026-07-05

### Added

- **`cleanup: true` group flag + cleanup-always guarantee.** The teardown mirror of the setup-always guarantee. A group flagged `cleanup: true` survives `--group` / `--priority` filtering (so filtered iteration runs still sweep the environment) and still **executes** when `--fail-fast` aborts the run — in the failing file AND in every later file — while all other remaining tests are marked skipped. The run's exit code still reflects the original failure. The new `--skip-cleanup` flag (mirror of `--skip-setup`) suppresses both behaviours for local iteration. Rationale: within-run cleanup can never be guaranteed (a SIGKILL or crashed run defeats any teardown), so the durable pattern is a capture-independent, pattern-based sweep at the START of the next run — and only such sweeps should be flagged. The group-execution loop moved to a new `engine/executor.py` to keep `cli.py` under the size limit.
- **`regrun lint TARGET` command.** Static analysis of a suite — no network, no execution — that encodes the regression-testing discipline as mechanical checks so violations surface at commit time instead of months later as flakes. Errors (exit 1): duplicate group ids within a file (E001), an mcp-layer file sorting after a `*cleanup*` file (E002, the shared-api_key-revoked ordering trap), a null `auth:` value (E003, the `auth: none` string-literal trap). Warnings: `is_error`-only MCP asserts (W001), positional array `equals`/`contains` (W002, suppressible with an inline `# lint: allow-positional` comment or `--allow-positional GLOB`), under-budgeted `eventually:` polls below a `--budget-floor` (W003, default 75s, computed with the real retry formula), create-shaped tests missing `{{RUN_ID}}`/`{{timestamp}}` (W004), and capture-dependent cleanup groups (W005). `--strict` elevates warnings to errors. Rules live in `engine/linter.py`.

## [0.4.2] - 2026-07-02

### Added

- **`any_contains` json_path operator.** The all-matches positive counterpart to `not_contains`: `"$.results[*].content_preview": { any_contains: "{{RUN_ID}}" }` passes when at least one value matched by the path contains the substring (`str(value)` tested with Python `in`, mirroring `contains`' substring semantics — but scanning **every** match instead of only `matches[0]`). Enables order-independent presence assertions on array paths where the target may not be rank 0 (ranking-fragile write-then-search probes). Opposite empty-set rule to `not_contains`: zero matches **fails** (a presence check against nothing means the target is absent), never a vacuous pass.

## [0.4.1] - 2026-06-21

### Internal

- **Release plumbing only — no runtime/assertion behaviour change.** Cuts a tagged
  release so the `not_contains` operator (shipped in 0.4.0) propagates to fresh
  builds of the regression-runner image, and fires the `docker-publish` workflow
  added after 0.4.0 (publishes `ghcr.io/daviunx/regrun:{version}` + `:latest` on
  `v*.*.*` tags).

## [0.4.0] - 2026-06-16

### Added

- **`not_contains` json_path operator.** Array-exclusion assertion: `"$.results[*].id": { not_contains: "{{FORBIDDEN_ID}}" }` passes when no value matched by the path equals the expected value (evaluated across **all** matches, with the same string-coerced equality fallback as `equals`). An empty or missing match set passes (the value is vacuously absent). Enables state-independent cross-tenant isolation checks — assert a forbidden id is absent from a result set regardless of how many own-account results the query returns — without relying on `total == 0` or test-ordering tricks. Equality coercion shared with `equals` via a new internal `_loose_eq` helper.

## [0.3.0] - 2026-06-15

### Added

- **`eventually:` retry primitive.** An optional `eventually:` block on any test re-runs the request and its assertions until they all pass or a retry budget is exhausted — for asserting on asynchronously-propagated state (search indexing, event processing, webhook delivery) without flaky fixed `sleep`s. Config: `max_attempts` (default 10), `interval` seconds (default 2.0), `backoff` multiplier (default 1.0 = fixed interval), `initial_delay` (default 0.0). On exhaustion the last attempt's assertion results are reported normally; runner exceptions are caught and surfaced as a failed result rather than propagated. Wired once at the run coordinator, so both the HTTP and MCP runners support it with no per-runner changes.

### Internal

- `pytest-cov` added with branch coverage enforcement; the retry loop lives in a self-contained `engine/retry.py` so the runners stay single-purpose.

## [0.2.0] - 2026-06-14

### Changed

- **MCP runner is now in-process.** The `fastmcp` runner previously spawned `uvx fastmcp call` as a subprocess on every test — paying CLI startup plus a fresh HTTP connection and MCP `initialize` handshake per call. It now uses an in-process `fastmcp.Client` with a persistent session: one connection per auth context, reused across all tests, closed at run end. On a 229-test MCP suite this cut wall time from ~402s to ~92s (4.4×) with identical results. Response normalization is unchanged — the asserted body is still sourced from the tool result's text content — so existing `json_path` / `is_error` assertions and captures work without modification.

### Added

- `fastmcp` (`>=3.4.2,<4.0`) runtime dependency for the in-process client. `uvx` is no longer required to run MCP tests.

### Notes

- `requires-python` narrowed to `>=3.11,<4.0` to satisfy the transitive `openapi-pydantic` constraint pulled in by `fastmcp`.

## [0.1.2] - 2026-06-11

### Fixed

- Setup-layer files were silently dropped when `--group` or `--priority` filters excluded their groups. The setup file's groups (IDs 1-2, priority `high`) did not match e.g. `--group 16` or `--priority medium`, leaving the file with zero groups so it was discarded — losing captured variables (`PROD_JWT`, `PROD_MCP_KEY`, `RUN_ID`, …) that every downstream layer depends on. Setup now always runs in full when auto-included as a dependency; group/priority filters are skipped for `meta.layer: setup` files unless setup is the explicit target (`--layer setup`). `--skip-setup` remains the only way to suppress it.

## [0.1.0] - 2026-06-08

### Added

- Initial public release extracted from internal tooling.
- YAML-driven test definitions with `meta`, `variables`, `auth`, and `groups` blocks.
- Four test runners: `httpx` (HTTP APIs), `fastmcp` (MCP servers), `bash` (shell commands), `websocket` (streaming).
- Assertion engine with `status`, `is_error`, `has_error`, `last_exit_code`, `contains`, and `json_path` operators.
- JSONPath operators: `exists`, `not_empty`, `equals`, `contains`, `gt`, `gte`, `lt`, `lte`, `starts_with`, `matches`.
- Variable capture from responses using JSONPath or `stdout`.
- Jinja2 template rendering with built-in `{{timestamp}}`, `{{date}}`, `{{uuid}}`, and `{{env.*}}` variables.
- Cross-file variable propagation via `VariableStore`.
- CLI with `regrun run <test-dir>` entry point.
- Layer filtering (`--layer`), group filtering (`--group`), priority filtering (`--priority`).
- Dry-run mode (`--dry-run`), JSON output (`--output json`), fail-fast (`--fail-fast`).
- Environment variable configuration with `REGRUN_` prefix.
