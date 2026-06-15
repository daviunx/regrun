# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
