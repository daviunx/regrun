# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
