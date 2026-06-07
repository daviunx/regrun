# regrun

YAML-driven regression test runner for HTTP APIs, MCP servers, and shell commands.

Define your test cases declaratively in YAML files, then run them against live services with a single command.

## Install

```bash
pip install regrun
```

## Quick Start

```bash
# Run all tests in a directory
regrun run tests/regression/

# Filter by layer (setup auto-included)
regrun run tests/regression/ --layer api

# Dry run — show test plan without executing
regrun run tests/regression/ --dry-run

# JSON output with verbose logging
regrun run tests/regression/ --output json --verbose
```

## CLI Reference

```
regrun run <test-dir> [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--layer` | `setup\|api\|mcp\|chat` | all | Filter to one layer (setup auto-included unless `--skip-setup`) |
| `--group` | `1,2,3` | all | Comma-separated group IDs |
| `--priority` | `high\|medium\|low` | all | Filter groups by priority |
| `--dry-run` | flag | false | Print test plan, do not execute |
| `--output` | `text\|json` | `text` | Output format |
| `--verbose`, `-v` | flag | false | Log request/response bodies |
| `--fail-fast` | flag | false | Stop on first failure |
| `--skip-setup` | flag | false | Skip setup layer |

## YAML Format

Each `.yaml` file in the test directory defines a set of test groups:

```yaml
meta:
  product: my-api
  layer: api                # setup | api | mcp | chat
  runner: httpx             # httpx | fastmcp | bash | websocket
  endpoint: "http://localhost:8000"
  default_auth: prod

variables:
  RUN_ID: "{{timestamp}}"   # Built-in: unix timestamp + 4 hex chars

auth:
  prod:
    type: bearer
    token: "{{PROD_JWT}}"
    org_header: "my-org"

groups:
  - id: 1
    name: "Health Check"
    priority: high
    tests:
      - id: "T.1"
        name: "GET /health"
        method: GET
        path: "/health"
        auth: none
        assert:
          status: 200
```

### Runners

- **httpx** — HTTP API tests (`method`, `path`, `body`, `query_params`)
- **fastmcp** — MCP tool calls via fastmcp CLI (`tool`, `args`)
- **bash** — Shell commands (`commands` list with `cmd` and optional `capture`)
- **websocket** — Streaming WebSocket tests (`url`, `send`, `wait_for`)

### Assertion Operators

| Assertion | Scope | Example |
|-----------|-------|---------|
| `status` | HTTP status code | `status: 200` or `status: [200, 201]` |
| `is_error` | MCP error flag | `is_error: false` |
| `has_error` | WebSocket error | `has_error: false` |
| `last_exit_code` | Bash exit code | `last_exit_code: 0` |
| `contains` | Body substring | `contains: "success"` |

**JSONPath operators** (under `json_path:`):

`exists`, `not_empty`, `equals`, `contains`, `gt`, `gte`, `lt`, `lte`, `starts_with`, `matches`

### Variable Capture

```yaml
capture:
  TASK_ID: "$.id"        # JSONPath from response
  RAW: stdout            # Full stdout (bash only)
```

Captured variables propagate to all subsequent tests across files.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REGRUN_API_ENDPOINT` | — | Override `meta.endpoint` in all files |
| `REGRUN_MCP_ENDPOINT` | — | Override `meta.mcp_endpoint` in all files |
| `REGRUN_TIMEOUT` | `30` | Per-test HTTP timeout (seconds) |
| `REGRUN_MCP_TIMEOUT` | `60` | Per-test MCP call timeout (seconds) |
| `REGRUN_WS_TIMEOUT` | `30` | Per-test WebSocket timeout (seconds) |
| `REGRUN_VERBOSE` | `false` | Log request/response bodies |

## License

MIT
