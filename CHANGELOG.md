# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
