# regrun — Agent Reference

> YAML-driven regression test runner for APIs, MCP servers, and WebSocket streams.
> Read this before writing or running regression tests.

---

## Runner Selection

| Use Case | Runner | Key Test Fields |
|----------|--------|-----------------|
| REST API endpoints | `httpx` | `method`, `path`, `body`, `query_params` |
| MCP tool calls | `fastmcp` | `tool`, `args` |
| Shell commands / DB setup | `bash` | `commands: [{cmd, capture}]` |
| WebSocket streaming / chat | `websocket` | `url`, `send`, `wait_for`, `timeout` |

---

## YAML File Template

```yaml
meta:
  product: myapp
  layer: api            # setup | api | mcp | chat
  runner: httpx         # httpx | fastmcp | bash | websocket
  endpoint: "http://localhost:8000"
  mcp_endpoint: "http://localhost:8001"   # optional — fastmcp runner only
  default_auth: prod    # optional — applies to all tests in file
  env_file: ".env.test" # optional — relative to the test file's directory

variables:
  RUN_ID: "{{timestamp}}"
  BASE_EMAIL: "test@example.com"

auth:
  prod:
    type: bearer          # bearer | api_key
    token: "{{PROD_JWT}}"
    org_header: "my-org"  # sets X-Org-Slug header; omit to suppress
  prod_mcp:
    type: api_key
    token: "{{env.MCP_API_KEY}}"

groups:
  - id: 1
    name: "Group Name"
    priority: high        # high | medium | low
    context: prod         # prod | fresh | both
    tests:
      - id: "T.1"
        name: "Test name"
        # ... runner-specific fields
        assert:
          # ... assertions
        capture:
          VAR_NAME: "$.json.path"
```

---

## One Example Per Runner

### httpx

```yaml
- id: "T.1"
  name: "Create item"
  method: POST
  path: "/api/v1/items"
  auth: prod
  body:
    name: "item-{{RUN_ID}}"
    active: true
  assert:
    status: 201
    json_path:
      "$.id": { exists: true }
      "$.name": { starts_with: "item-" }
  capture:
    ITEM_ID: "$.id"
```

### fastmcp

```yaml
- id: "T.2"
  name: "List open tasks"
  tool: tasks_list
  args:
    status: "open"
  auth: prod_mcp
  assert:
    is_error: false
    json_path:
      "$[0].id": { exists: true }
      "$[0].status": { equals: "open" }
```

### bash

```yaml
- id: "S.1"
  name: "Reset test user"
  runner: bash
  commands:
    - cmd: |
        psql -U postgres -d mydb \
          -c "UPDATE users SET active = true WHERE email = 'test@example.com';"
      capture:
        RAW_OUTPUT: stdout
  assert:
    last_exit_code: 0
    contains: "UPDATE 1"
```

### websocket

```yaml
- id: "C.1"
  name: "Chat session completes"
  url: "{{WS_HOST}}/ws/chat/{{AGENT_ID}}?session={{SESSION_ID}}"
  send:
    message: "Hello"
    session_id: "{{SESSION_ID}}"
  wait_for: "agent_completed"
  timeout: 60000
  assert:
    has_error: false
    json_path:
      "$.response_text": { not_empty: true }
      "$.event_count": { gt: 1 }
```

**WebSocket aggregated response fields** (available in `json_path` assertions):

| Field | Type | Description |
|-------|------|-------------|
| `$.response_text` | string | Concatenated text deltas |
| `$.event_count` | int | Total events received |
| `$.events` | list | Ordered event type names |
| `$.tool_calls` | list | Tool names invoked |
| `$.error` | string\|null | Error content if error event received |
| `$.duration_ms` | float | Wall-clock duration |

---

## Assertion Reference

### Top-level assertions

| Key | Runner | Example |
|-----|--------|---------|
| `status` | httpx | `status: 201` or `status: [200, 201]` |
| `is_error` | fastmcp | `is_error: false` |
| `has_error` | websocket | `has_error: false` |
| `last_exit_code` | bash | `last_exit_code: 0` |
| `contains` | all | `contains: "UPDATE 1"` |

### json_path operators

| Operator | Example | Notes |
|----------|---------|-------|
| `exists` | `{ exists: true }` | Field presence check; use `{ exists: false }` to assert absence |
| `equals` | `{ equals: "active" }` | Exact match; falls back to string-coerced comparison on type mismatch |
| `contains` | `{ contains: "test" }` | Substring in string value |
| `not_empty` | `{ not_empty: true }` | Non-null, non-empty string/list/dict |
| `gt` | `{ gt: 0 }` | Numeric greater-than |
| `gte` | `{ gte: 1 }` | Numeric greater-than-or-equal |
| `lt` | `{ lt: 100 }` | Numeric less-than |
| `lte` | `{ lte: 99 }` | Numeric less-than-or-equal |
| `starts_with` | `{ starts_with: "ntk_" }` | String prefix |
| `matches` | `{ matches: "^[a-z]+$" }` | Regex search (`re.search`) |

### Required assertions by operation

| Operation | Required assertions |
|-----------|---------------------|
| POST (create) | `status: 201` + json_path for created ID |
| GET (read) | `status: 200` + json_path for expected field |
| GET (list) | `status: 200` (count may vary — do not assert exact length) |
| PUT/PATCH | `status: 200` + json_path for changed field |
| DELETE | `status: 204` or `status: [200, 204]` |
| MCP tool | `is_error: false` + at least one json_path |
| Bash command | `last_exit_code: 0` + `contains` for expected output |
| WebSocket | `has_error: false` + json_path for STRUCTURE only |

---

## Variable System

### Built-in template variables

| Template | Produces | Example output |
|----------|----------|----------------|
| `{{timestamp}}` | Unix timestamp + 4 random hex chars | `1717891234a3f2` |
| `{{date}}` | ISO date (YYYY-MM-DD) | `2026-06-09` |
| `{{uuid}}` | UUID4 string | `550e8400-e29b-41d4-a716-446655440000` |
| `{{env.VAR_NAME}}` | Environment variable value | value of `$VAR_NAME` |

Full Jinja2 syntax is supported. Undefined variables warn (via `StrictUndefined`) but do not crash — the template string is returned as-is.

### Variable rules

- Variables declared in `variables:` are only set if the key does not already exist in the store. This prevents downstream files from overwriting setup captures.
- `capture:` uses JSONPath to extract values from the response body into the store.
- For bash tests, `capture: { VAR: stdout }` captures the entire stripped stdout; `capture: { VAR: "$.field" }` parses stdout as JSON first.
- Captured variables propagate cross-file — a JWT captured in `00_setup.yaml` is available in all subsequent files without re-declaration.
- Always suffix resource names with `{{RUN_ID}}` to prevent collisions across parallel runs.

---

## Auth Patterns

| Pattern | When | How |
|---------|------|-----|
| File default | All or most tests use the same auth | `meta.default_auth: prod` |
| Per-test override | One test needs different auth | `auth: fresh` on that test |
| No auth | Login / register / bare-domain endpoints | `auth: none` (string literal) |
| Suppress org header for one test | Test uses default auth but endpoint rejects X-Org-Slug | `org_header: false` on that test |

**Auth types:**
- `bearer` — sets `Authorization: Bearer <token>`
- `api_key` — sets `X-API-Key: <token>`

`org_header` in an auth config sets `X-Org-Slug`. Setting `org_header: false` on the test suppresses it even when the auth config has it set.

---

## Gotchas

- **`auth: none` is a string literal** — writing `auth:` with no value parses as YAML null and causes a runner error. Always write `auth: none` explicitly.

- **Per-test `runner:` override is only for setup files** — setup files mix runners (bash for DB, httpx for auth). Pure `api` or `mcp` files should not use per-test runner overrides; the file's `meta.runner` applies to all tests.

- **`org_header: false` on auth endpoints** — login, register, and org-creation endpoints are bare-domain requests. Omitting `org_header: false` causes the runner to send `X-Org-Slug`, which produces 400 errors.

- **Cross-file variable propagation** — variables captured in `00_setup.yaml` (e.g. `PROD_JWT`) are available in `01_api_surface.yaml` without re-declaration. The store skips keys already present, so order of declaration is safe.

- **Bash commands run from CWD** — the BashRunner sets `cwd` to `Path.cwd()` (the directory where `regrun` is invoked). Use absolute paths or `docker exec` rather than paths relative to the test file.

- **Numeric operators are `gt`, `gte`, `lt`, `lte`** — not `greater_than`, `less_than`, or `>=`. Using the wrong form silently skips the assertion.

- **`default_auth` covers all tests in the file** — only add an explicit `auth:` field to tests that need different auth than the default. Repeating the default auth on every test is unnecessary and creates noise.

- **Never re-declare `RUN_ID` in api or mcp files** — `RUN_ID` is set once in `00_setup.yaml` via `{{timestamp}}`. Re-declaring it in downstream files overwrites the value mid-run, breaking resource naming consistency.

- **`env_file` path is relative to the test file** — not to CWD or the runner. The runner resolves it as `test_file_directory / env_file`.

- **WebSocket: never assert exact LLM text** — `$.response_text` is non-deterministic. Use `not_empty: true` and assert structure (e.g. `$.event_count` with `gt: 1`). Never use `equals` or `contains` on `$.response_text`.

- **`status` accepts a list** — `status: [200, 201]` matches either code. Use for endpoints that may return different success codes depending on whether a resource was created or already existed.

- **`capture:` on bash uses per-command, not per-test** — place `capture:` inside each `commands` list item, not at the test level. Test-level `capture:` is for httpx/fastmcp JSONPath extraction from the response body.

- **`meta.product` is for reporting only** — it does not need to match any registry or config file. Use a meaningful name for log output and CI summaries.

- **`--group` and `--priority` do not filter auto-included setup files** — when setup runs as a dependency (you did not pass `--layer setup`), it runs in full. Filters apply to setup only when explicitly targeted via `--layer setup`. `--skip-setup` still excludes setup entirely.

---

## Running Tests

```bash
# Run all tests in a directory
regrun run tests/regression/

# Smoke test (high priority groups only)
regrun run tests/regression/ --priority high

# Single layer (setup auto-included unless --skip-setup)
regrun run tests/regression/ --layer api

# Specific groups by ID
regrun run tests/regression/ --group 1,2,3

# Preview test plan without executing
regrun run tests/regression/ --dry-run

# Verbose output (request/response bodies in logs)
regrun run tests/regression/ --verbose

# Stop on first failure
regrun run tests/regression/ --fail-fast

# JSON output for CI parsing
regrun run tests/regression/ --output json

# Skip setup (variables already populated from a prior run)
regrun run tests/regression/ --layer mcp --skip-setup
```

**CI endpoint overrides** (override `meta.endpoint` / `meta.mcp_endpoint` for all files):

```bash
REGRUN_API_ENDPOINT=http://api:8000 regrun run tests/regression/
REGRUN_MCP_ENDPOINT=http://mcp:8000 regrun run tests/regression/
```

**All env overrides** (`REGRUN_` prefix):

| Env var | Default | Description |
|---------|---------|-------------|
| `REGRUN_API_ENDPOINT` | — | Overrides `meta.endpoint` in all files |
| `REGRUN_MCP_ENDPOINT` | — | Overrides `meta.mcp_endpoint` in all files |
| `REGRUN_TIMEOUT` | 30 | HTTP per-test timeout (seconds) |
| `REGRUN_MCP_TIMEOUT` | 60 | MCP per-test timeout (seconds) |
| `REGRUN_WS_TIMEOUT` | 30 | WebSocket default timeout (seconds) |
| `REGRUN_VERBOSE` | false | Log request/response bodies |

**Exit codes:** 0 = all passed, 1 = any failure or error.

---

## File Conventions

```
tests/regression/
  00_setup.yaml          # layer: setup — auth, seed data, health checks
  01_api_surface.yaml    # layer: api   — REST endpoint tests
  02_mcp_surface.yaml    # layer: mcp   — MCP tool tests
  03_chat_surface.yaml   # layer: chat  — WebSocket streaming tests
```

- Pass the directory path directly: `regrun run tests/regression/` or `regrun run ./my-tests/`.
- Files are sorted alphabetically; setup files (`layer: setup`) always run first regardless of name.
- One layer per file, except setup files which may mix runners via per-test `runner:` overrides.
- Numeric prefixes (`00_`, `01_`) determine execution order within the same layer.
- `meta.product` is used for reporting and log labels only — no config file lookup is performed.
