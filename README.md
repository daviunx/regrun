# regrun

Deterministic YAML-driven regression test runner for APIs, MCP servers, and WebSocket streams.

[![PyPI](https://img.shields.io/pypi/v/regrun)](https://pypi.org/project/regrun/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What is regrun?

regrun lets you define regression tests as YAML files and run them against live services — no test framework required. You describe what to call, what to assert, and what to capture; regrun handles execution, variable interpolation, and reporting. It supports four runners: REST APIs (httpx), MCP tools (fastmcp CLI), shell commands (bash), and WebSocket streams (websocket). Tests share a variable store across files, so a JWT captured in setup is available to every subsequent test without any wiring.

---

## Installation

```bash
pip install regrun
```

Requires Python 3.12 or later.

The MCP runner requires `uvx` and the `fastmcp` CLI available on `PATH`:

```bash
pip install fastmcp
```

---

## Quick Start

Create two test files for a fictional `myapp` running at `http://localhost:8000`.

**`tests/regression/00_setup.yaml`** — acquire a JWT:

```yaml
meta:
  product: myapp
  layer: setup
  runner: httpx
  endpoint: "http://localhost:8000"

variables:
  RUN_ID: "{{timestamp}}"
  TEST_EMAIL: "regtest-{{RUN_ID}}@example.com"
  TEST_PASSWORD: "TestPass123!"

groups:
  - id: 1
    name: "Auth"
    priority: high
    tests:
      - id: "S.1"
        name: "Login and capture JWT"
        method: POST
        path: "/api/v1/auth/login"
        auth: none
        org_header: false
        body:
          email: "{{TEST_EMAIL}}"
          password: "{{TEST_PASSWORD}}"
        assert:
          status: 200
          json_path:
            "$.access_token": { exists: true }
        capture:
          APP_JWT: "$.access_token"
```

**`tests/regression/01_api.yaml`** — exercise the API with the captured token:

```yaml
meta:
  product: myapp
  layer: api
  runner: httpx
  endpoint: "http://localhost:8000"
  default_auth: prod

auth:
  prod:
    type: bearer
    token: "{{APP_JWT}}"

groups:
  - id: 1
    name: "Items"
    priority: high
    tests:
      - id: "A.1"
        name: "List items returns array"
        method: GET
        path: "/api/v1/items"
        assert:
          status: 200
          json_path:
            "$": { not_empty: true }
```

Run the tests:

```bash
regrun run tests/regression/
```

Expected output:

```
tests/regression/  •  2 tests

  [PASS]  S.1  Login and capture JWT         (142ms)
  [PASS]  A.1  List items returns array       (38ms)

  2 passed, 0 failed  •  180ms
```

---

## How It Works (Execution Model)

**File ordering:** The setup layer always runs first. All other files run alphabetically by filename. Numeric prefixes (`00_`, `01_`, `02_`) enforce the intended order.

**Setup dependency:** When you pass `--layer api` or `--layer mcp`, the setup file is auto-included and runs before the target layer. When setup runs as a dependency, `--group` and `--priority` filters are not applied to it — it always runs in full so captured variables stay available. Filters apply to setup only when it is the explicit target (`--layer setup`). Skip setup entirely with `--skip-setup` when variables are already populated from a prior run segment.

**Cleanup dependency (sweep-first):** A group flagged `cleanup: true` is the mirror of the setup layer on the teardown side. It is always retained under `--group` / `--priority` filters (so filtered iteration runs still sweep), and it still **executes** when `--fail-fast` aborts the run — in the failing file and every later file — while all other remaining tests are skipped. The run's exit code still reflects the original failure. Suppress cleanup groups with `--skip-cleanup` when iterating locally. Because within-run cleanup can never be guaranteed (a SIGKILL or crashed run defeats any teardown), the durable pattern is a *pattern-based, capture-independent* sweep at the **start** of the run (in `00_setup`) that deletes all prior-run artifacts — the run that needs a clean environment is the one that sweeps it. Only such capture-independent sweeps should be flagged `cleanup: true`.

**Variable persistence:** File-level variables are merged once per file at parse time. A variable already set by an earlier file — for example `RUN_ID` defined in setup — is never overwritten by a later file's `variables` block. This ensures identifiers stay consistent across the entire run.

**Layer concept:** Tests are organised into four layers, processed in this order:

| Layer | Purpose |
|-------|---------|
| `setup` | Auth, seed data, environment configuration |
| `api` | REST API surface tests |
| `mcp` | MCP tool tests |
| `chat` | WebSocket and streaming tests |

---

## CLI Reference

```
regrun run TEST_DIR [OPTIONS]
```

`TEST_DIR` is a path to a directory containing YAML test files.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--layer` | `setup\|api\|mcp\|chat` | all | Filter to one layer (setup auto-included) |
| `--group` | `1,2,3` | all | Comma-separated group IDs |
| `--priority` | `high\|medium\|low` | all | Filter groups by priority |
| `--dry-run` | flag | false | Print test plan without executing |
| `--output` | `text\|json` | text | Output format |
| `--verbose`, `-v` | flag | false | Log full request/response bodies |
| `--fail-fast` | flag | false | Stop on first failure (cleanup groups still run) |
| `--skip-setup` | flag | false | Skip setup layer |
| `--skip-cleanup` | flag | false | Skip cleanup-flagged groups (use when iterating; leaks must be swept later) |

Examples:

```bash
# Smoke test only
regrun run tests/regression/ --priority high

# MCP layer only
regrun run tests/regression/ --layer mcp

# Specific groups as JSON
regrun run tests/regression/ --group 1,3 --output json

# Preview without running
regrun run tests/regression/ --dry-run

# Iterate on group 5 without running the sweep groups
regrun run tests/regression/ --group 5 --skip-cleanup
```

### `regrun lint`

Static analysis of a suite — no network, no execution. Encodes the regression-testing discipline (assertion strength, budget floors, sweep hygiene, the `auth: none` trap) as mechanical checks so violations surface at commit time instead of months later as flakes. Exit code is `1` if any **error** rule fires (`0` otherwise); `--strict` elevates warnings to errors.

```
regrun lint TARGET [OPTIONS]
```

`TARGET` is a directory path or a product name from `regrun.yaml`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--strict` | flag | false | Treat warnings as errors |
| `--budget-floor` | float | `75.0` | Minimum `eventually:` ceiling (seconds) before W003 fires |
| `--allow-positional` | glob (repeatable) | — | File glob(s) where positional array asserts (W002) are permitted |

| Rule | Sev | Meaning |
|------|-----|---------|
| E001 | error | Duplicate group id within a file |
| E002 | error | mcp-layer file (`runner: fastmcp` / `default_auth: mcp`) sorts after a `*cleanup*` file (the shared api_key is revoked by cleanup) |
| E003 | error | A test has `auth:` with a null value (the `auth: none` string-literal trap) |
| W001 | warn | MCP tool test asserts `is_error` with no `json_path` on the response |
| W002 | warn | `equals`/`contains` on a positional array path (`[0]`/`[*]`) — rank-0 fragile. Suppress per-test with an inline `# lint: allow-positional` comment, or per-file with `--allow-positional` |
| W003 | warn | `eventually:` worst-case ceiling below the budget floor (default 75s) |
| W004 | warn | POST/create-shaped test whose body/args carry no `{{RUN_ID}}`/`{{timestamp}}` (4xx-asserting negative tests are skipped) |
| W005 | warn | Cleanup-flagged group references a variable captured in another group (capture-dependent sweep) |

```bash
# Lint before committing suite changes
regrun lint tests/regression/

# CI gate: fail on any warning too
regrun lint tests/regression/ --strict
```

---

## YAML Schema Reference

### `meta` block (required)

```yaml
meta:
  product: myapp              # Used for reporting only — does not need to match any registered name
  layer: api                  # setup | api | mcp | chat
  runner: httpx               # httpx | fastmcp | bash | websocket
  endpoint: "http://localhost:8000"      # Base URL for httpx runner
  mcp_endpoint: "http://localhost:9000"  # MCP base URL — falls back to endpoint if omitted
  default_auth: prod          # Auth key applied to all tests without explicit auth:
  env_file: ".env.test"       # Path to .env file, relative to the test file's directory
```

The `product` field appears in report output. It does not need to match any external registry.

### `variables` block

```yaml
variables:
  RUN_ID: "{{timestamp}}"               # Unix timestamp + 4 hex chars (unique per run)
  TODAY: "{{date}}"                     # YYYY-MM-DD
  REQUEST_ID: "{{uuid}}"               # UUID4
  API_TOKEN: "{{env.MY_SECRET_TOKEN}}"  # Environment variable passthrough
  BASE_EMAIL: "admin@myapp.io"          # Static value
```

Built-in variables:

| Variable | Description |
|----------|-------------|
| `{{timestamp}}` | Unix timestamp + 4 hex chars — unique per run, use as resource name suffix |
| `{{date}}` | Current date as `YYYY-MM-DD` |
| `{{uuid}}` | UUID4 |
| `{{env.VAR_NAME}}` | Reads `VAR_NAME` from the process environment |

Full Jinja2 template syntax is supported. The engine runs in `StrictUndefined` mode: an undefined variable logs a warning and returns the raw template string rather than raising an exception.

Variables set by earlier files are preserved. Downstream files skip re-initialization of keys that already exist in the store.

### `auth` block

```yaml
auth:
  prod:
    type: bearer                # bearer | api_key
    token: "{{APP_JWT}}"
    org_header: "myapp"         # Sets X-Org-Slug header — omit if not needed
  service_key:
    type: api_key
    token: "{{SERVICE_API_KEY}}"
```

### `groups` block

```yaml
groups:
  - id: 1
    name: "Auth Flow"
    priority: high          # high | medium | low  (default: medium)
    context: prod           # prod | fresh | both  (default: prod)
    tests:
      - ...
  - id: 2
    name: "CRUD Operations"
    priority: medium
    tests:
      - ...
  - id: 3
    name: "Environment Sweep"
    cleanup: true           # survives filters; runs even on --fail-fast abort (default: false)
    tests:
      - ...
```

`cleanup: true` marks a group as a teardown/sweep that must run even on filtered or aborted runs (see *Cleanup dependency* above). Reserve it for capture-independent, pattern-based sweeps only — `regrun lint` flags (W005) a cleanup group that depends on variables captured elsewhere.

### Test fields by runner

**httpx (REST API)**

```yaml
- id: "A.2"
  name: "Create item"
  method: POST
  path: "/api/v1/items"
  auth: prod                  # Named auth key, "none", or omit to use default_auth
  org_header: true            # false to suppress X-Org-Slug
  body:
    name: "Widget {{RUN_ID}}"
    price: 9.99
  query_params:
    expand: metadata
  assert:
    status: 201
    json_path:
      "$.id": { exists: true }
      "$.name": { starts_with: "Widget" }
  capture:
    ITEM_ID: "$.id"
```

**fastmcp (MCP tools)**

```yaml
- id: "M.1"
  name: "List items via MCP"
  tool: items_list
  args:
    status: "active"
    limit: 10
  auth: service_key
  assert:
    is_error: false
    json_path:
      "$[0].id": { exists: true }
      "$": { not_empty: true }
  capture:
    FIRST_ITEM_ID: "$[0].id"
```

**bash (shell commands)**

```yaml
- id: "S.2"
  name: "Seed test user"
  runner: bash
  commands:
    - cmd: |
        docker exec myapp-postgres psql -U postgres -d myapp \
          -c "INSERT INTO users (email) VALUES ('seed@example.com') ON CONFLICT DO NOTHING;"
      capture:
        RAW_OUTPUT: stdout
  assert:
    last_exit_code: 0
    contains: "INSERT"
```

Bash commands run from the directory where you invoke `regrun`, not from the test file location. Use absolute paths or `docker exec` rather than relative paths.

**websocket (streaming)**

```yaml
- id: "C.1"
  name: "Chat session produces response"
  url: "ws://localhost:8000/api/v1/ws/chat?session_id={{SESSION_ID}}"
  send:
    message: "What is the status of my account?"
    session_id: "{{SESSION_ID}}"
  wait_for: "agent_completed"      # Event type that terminates collection
  timeout: 60000                   # Milliseconds (overrides file-level timeout)
  ws_config:
    text_event: text_delta         # Override only if your server uses non-default field names
  assert:
    has_error: false
    json_path:
      "$.response_text": { not_empty: true }
      "$.event_count": { gt: 1 }
  capture:
    CHAT_RESPONSE: "$.response_text"
```

The runner connects, sends `send` as a JSON frame, collects events until `wait_for` is received, and returns an aggregated result dict:

| Field | Type | Description |
|-------|------|-------------|
| `response_text` | `str` | All `text_delta` fragments joined |
| `events` | `list[str]` | Ordered list of all event types received |
| `event_count` | `int` | Total number of events |
| `tool_calls` | `list[str]` | Tool names from `tool_call` events |
| `duration_ms` | `float` | Wall time from connect to termination event |
| `error` | `str\|null` | Error message if an `error` event was received or timeout occurred |

**`ws_config` options** (all have defaults — omit unless overriding):

| Field | Default | Description |
|-------|---------|-------------|
| `event_type_field` | `event_type` | Primary key used to read the event type from each frame |
| `event_type_fallback` | `type` | Fallback key if primary is absent |
| `text_event` | `text_delta` | Event type whose payload contributes to `response_text` |
| `text_field` | `data.delta` | Dot-path to the text content within a text event |
| `tool_call_event` | `tool_call` | Event type that signals a tool was called |
| `tool_name_field` | `data.tool_name` | Dot-path to the tool name within a tool call event |
| `error_event` | `error` | Event type that signals an error |
| `error_field` | `data.content` | Dot-path to the error message within an error event |

**Per-test runner override** — used in setup files that mix bash and httpx:

```yaml
# In a file with meta.runner: bash, a single test can use httpx instead:
- id: "P.1"
  runner: httpx               # Overrides the file-level meta.runner
  method: POST
  path: "/api/v1/auth/login"
  auth: none
  org_header: false
  body:
    email: "{{TEST_EMAIL}}"
    password: "{{TEST_PASSWORD}}"
  assert:
    status: 200
  capture:
    APP_JWT: "$.access_token"
```

Pure `api` or `mcp` files should not use per-test `runner:` overrides — the file's `meta.runner` applies uniformly.

---

## Assertion Vocabulary

### Top-level assertions

| Key | Values | Runner |
|-----|--------|--------|
| `status` | `200` or `[200, 201]` | httpx |
| `is_error` | `true\|false` | fastmcp |
| `has_error` | `true\|false` | websocket |
| `last_exit_code` | `0` | bash |
| `contains` | substring string | all runners |

### `json_path` operators

Each entry under `json_path:` maps a JSONPath expression to one operator:

| Operator | Example | Description |
|----------|---------|-------------|
| `exists` | `"$.id": { exists: true }` | Field presence check |
| `equals` | `"$.status": { equals: "active" }` | Exact match (string-coerced fallback) |
| `contains` | `"$.name": { contains: "Widget" }` | Substring |
| `gt` | `"$.total": { gt: 0 }` | Greater than |
| `gte` | `"$.count": { gte: 1 }` | Greater than or equal |
| `lt` | `"$.errors": { lt: 10 }` | Less than |
| `lte` | `"$.errors": { lte: 5 }` | Less than or equal |
| `starts_with` | `"$.key": { starts_with: "ntk_" }` | Prefix check |
| `matches` | `"$.slug": { matches: "^[a-z0-9-]+$" }` | Regex search |
| `not_empty` | `"$.items": { not_empty: true }` | Value is non-empty string, list, or dict |
| `not_contains` | `"$.results[*].id": { not_contains: "{{FORBIDDEN_ID}}" }` | Array exclusion — passes when no value matched by the path equals the expected value (all matches, string-coerced); empty/missing match set passes |

Note: numeric operators (`gt`, `gte`, `lt`, `lte`) are the correct names. `greater_than`, `less_than`, `>=`, and `<=` are not valid.

---

## Variable Capture

```yaml
capture:
  ITEM_ID: "$.id"                # JSONPath from JSON response
  OWNER_EMAIL: "$.owner.email"   # Nested path
  RAW_OUTPUT: stdout             # Full stdout (bash runner only)
```

Captured variables are stored in the shared `VariableStore` and are available to all subsequent tests in the run — including tests in later YAML files. This is how a JWT captured in `00_setup.yaml` is accessible in `01_api_surface.yaml` without any re-declaration.

**Collision avoidance:** suffix resource names with `{{RUN_ID}}` to prevent conflicts across runs:

```yaml
body:
  name: "Test item {{RUN_ID}}"
```

---

## Auth Patterns Guide

| Pattern | YAML | When to use |
|---------|------|-------------|
| File default | `meta.default_auth: prod` | All tests in file use the same auth |
| Per-test override | `auth: admin` | One test needs different credentials |
| No auth | `auth: none` | Login, register, org creation endpoints |
| Suppress org header | `org_header: false` | Bare-domain endpoints where `X-Org-Slug` causes 400 errors |

`auth: none` is a string literal, not YAML null. Always write `auth: none` explicitly — writing `auth:` with no value parses as `null` and fails.

**Multi-file auth flow:** setup acquires credentials, downstream files consume them.

`00_setup.yaml`:
```yaml
meta:
  runner: httpx
  endpoint: "http://localhost:8000"
# No default_auth — login endpoint needs no auth

groups:
  - id: 1
    tests:
      - id: "S.1"
        name: "Login"
        method: POST
        path: "/api/v1/auth/login"
        auth: none
        org_header: false
        body:
          email: "{{TEST_EMAIL}}"
          password: "{{TEST_PASSWORD}}"
        assert:
          status: 200
        capture:
          APP_JWT: "$.access_token"
```

`01_api_surface.yaml`:
```yaml
meta:
  runner: httpx
  endpoint: "http://localhost:8000"
  default_auth: prod          # APP_JWT now available from setup

auth:
  prod:
    type: bearer
    token: "{{APP_JWT}}"      # Captured in 00_setup.yaml
    org_header: "myapp"
```

---

## Complete Example

A self-contained two-file example for a fictional `myapp` REST service.

**`tests/regression/00_setup.yaml`**

```yaml
meta:
  product: myapp
  layer: setup
  runner: bash
  endpoint: "http://localhost:8000"

variables:
  RUN_ID: "{{timestamp}}"
  TEST_EMAIL: "regtest-{{RUN_ID}}@example.com"
  TEST_PASSWORD: "TestPass123!"

groups:
  - id: 1
    name: "Seed"
    priority: high
    tests:
      - id: "S.1"
        name: "Verify database is ready"
        runner: bash
        commands:
          - cmd: "docker exec myapp-postgres pg_isready -U postgres"
            capture:
              RAW_OUTPUT: stdout
        assert:
          last_exit_code: 0
          contains: "accepting connections"

      - id: "S.2"
        name: "Login and capture JWT"
        runner: httpx
        method: POST
        path: "/api/v1/auth/login"
        auth: none
        org_header: false
        body:
          email: "{{TEST_EMAIL}}"
          password: "{{TEST_PASSWORD}}"
        assert:
          status: 200
          json_path:
            "$.access_token": { exists: true }
        capture:
          APP_JWT: "$.access_token"

      - id: "S.3"
        name: "Create API key"
        runner: httpx
        method: POST
        path: "/api/v1/api-keys"
        auth: session
        body:
          name: "regression-key-{{RUN_ID}}"
        assert:
          status: 201
          json_path:
            "$.key": { starts_with: "ak_" }
        capture:
          API_KEY: "$.key"

auth:
  session:
    type: bearer
    token: "{{APP_JWT}}"
```

**`tests/regression/01_api_surface.yaml`**

```yaml
meta:
  product: myapp
  layer: api
  runner: httpx
  endpoint: "http://localhost:8000"
  default_auth: prod

auth:
  prod:
    type: bearer
    token: "{{APP_JWT}}"
    org_header: "myapp"

groups:
  - id: 1
    name: "Items CRUD"
    priority: high
    tests:
      - id: "A.1"
        name: "List items"
        method: GET
        path: "/api/v1/items"
        assert:
          status: 200
          json_path:
            "$": { not_empty: true }

      - id: "A.2"
        name: "Create item"
        method: POST
        path: "/api/v1/items"
        body:
          name: "Regression item {{RUN_ID}}"
          price: 19.99
        assert:
          status: 201
          json_path:
            "$.id": { exists: true }
            "$.name": { contains: "Regression item" }
        capture:
          ITEM_ID: "$.id"

      - id: "A.3"
        name: "Get item by ID"
        method: GET
        path: "/api/v1/items/{{ITEM_ID}}"
        assert:
          status: 200
          json_path:
            "$.id": { equals: "{{ITEM_ID}}" }
            "$.price": { equals: "19.99" }

      - id: "A.4"
        name: "Delete item"
        method: DELETE
        path: "/api/v1/items/{{ITEM_ID}}"
        assert:
          status: 204
```

Run it:

```bash
regrun run tests/regression/
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REGRUN_TIMEOUT` | `30` | Per-test HTTP timeout (seconds) |
| `REGRUN_MCP_TIMEOUT` | `60` | Per-test MCP call timeout (seconds) |
| `REGRUN_WS_TIMEOUT` | `30` | Per-test WebSocket timeout (seconds) |
| `REGRUN_VERBOSE` | `false` | Log full request/response bodies |
| `REGRUN_API_ENDPOINT` | — | Override `meta.endpoint` globally (for CI) |
| `REGRUN_MCP_ENDPOINT` | — | Override `meta.mcp_endpoint` globally (for CI) |

---

## CI Integration

In CI, services run as Docker containers with network aliases instead of `*.localhost` domains. Use the endpoint override variables to point regrun at the container aliases.

**GitLab CI:**

```yaml
regression:
  stage: test
  services:
    - name: myapp-api:latest
      alias: api
    - name: myapp-mcp:latest
      alias: mcp
  variables:
    REGRUN_API_ENDPOINT: "http://api:8000"
    REGRUN_MCP_ENDPOINT: "http://mcp:9000"
  script:
    - pip install regrun
    - regrun run tests/regression/
```

**GitHub Actions:**

```yaml
jobs:
  regression:
    runs-on: ubuntu-latest
    services:
      api:
        image: myapp-api:latest
        ports:
          - 8000:8000
    steps:
      - uses: actions/checkout@v4
      - run: pip install regrun
      - run: regrun run tests/regression/
        env:
          REGRUN_API_ENDPOINT: "http://localhost:8000"
```

The endpoint override applies to every test file in the run. YAML files keep their local `*.localhost` URLs for developer use; CI overrides them without any file changes.

---

## File Structure

**Recommended test directory layout:**

```
tests/regression/
  00_setup.yaml          # Setup: auth, seed data, environment checks
  01_api_surface.yaml    # REST API surface tests
  02_mcp_surface.yaml    # MCP tool tests
  03_chat_surface.yaml   # WebSocket / streaming tests
```

Numeric prefixes control alphabetical sort order. The setup layer is always processed first regardless of filename, but `00_` makes the intent explicit and keeps directory listings readable.

---

## Development

Install dependencies and run the test suite:

```bash
poetry install
poetry run pytest
```

Tests live at `tests/integration/cli/` and cover CLI behaviour end-to-end.

---

## License

MIT
