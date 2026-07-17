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
| Postgres SQL statements | `sql` | `sql:` (+ `meta.sql_connection`) |
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

`json_path` asserts (and captures) run against the **normalized** body — an envelope's `data` is hoisted to top level (never `$.data.*`), and a plain-string error body is exposed as `$._raw_text`. See README "Response normalization".

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

### sql

```yaml
# meta:
#   runner: sql
#   sql_connection:
#     docker_container: "{{ env.get('RALLY_COMPOSE_PROJECT', 'rally') }}-db-1"
#     docker_user: postgres
#     database: "{{ env.get('RALLY_DB', 'rally_prod') }}"
#     fallback_dsn: "{{ env.get('RALLY_DSN', 'postgres://postgres@localhost:5432/rally_prod') }}"
- id: "SQL.1"
  name: "no orphaned rows"
  runner: sql
  sql: "SELECT to_jsonb(count(*)) FROM events WHERE org_id IS NULL;"
  assert:
    last_exit_code: 0
    contains: "0"
```

Docker-probe dispatch is automatic (`docker exec … psql` when docker is up, else `psql {fallback_dsn}`); every call carries `-v ON_ERROR_STOP=1 -q -t -A` and the statement on stdin. Connection values are Jinja-renderable — keep the product-prefixed env convention, no new `REGRUN_SQL_*` vars. **SQL only** — app-command exec + OpenSearch curl steps stay `runner: bash`. `runner: sql` hard-fails to parse on a pre-0.8.0 binary; adopt only after the CI pin is ≥ 0.8.0.

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
| `not_contains` | `"$.results[*].id": { not_contains: "{{FORBIDDEN_ID}}" }` | Array exclusion: passes when NO value matched by the path equals the expected value (all matches, string-coerced). Empty/missing match set passes. Use for isolation checks — assert a forbidden value is absent regardless of how many results return |

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

- **`cleanup: true` groups are the teardown mirror of setup** — a cleanup-flagged group survives `--group`/`--priority` filters AND still runs when `--fail-fast` aborts (in the failing file and later files), so filtered/aborted iteration runs still sweep the environment. The run's exit code still reflects the original failure. `--skip-cleanup` suppresses them. **Only flag capture-independent, pattern-based sweeps** (`slug LIKE 'regr-%'` + OpenSearch `_delete_by_query`) — never capture-dependent tail deletes (those false-red in filtered runs; the next run's start-of-run sweep covers their leaks). `regrun lint` W005 flags a cleanup group that references a variable captured elsewhere.

- **Sweep-first, not cleanup-last** — within-run cleanup can never be guaranteed (SIGKILL / crashed run defeats any teardown). The only guaranteed cleanup is the pattern-based sweep at the START of the next run. Every suite must open (in `00_setup`, after auth) with a self-healing sweep that deletes all prior-run artifacts of every fixture family, across BOTH Postgres and OpenSearch, followed by a preflight group asserting zero leftovers + quota headroom.

- **Budget floor ≥75s** — indexing/async `eventually:` poll ceilings must be ≥75s under load. `regrun lint` W003 flags under-budgeted polls (ceiling = `initial_delay + interval·Σ backoff^k`, k=0..max_attempts-2).

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

# Verbose output (request/response bodies in logs for ALL tests; rarely needed —
# failures are always fully explained by default, see "Failure diagnostics" below)
regrun run tests/regression/ --verbose

# Stop on first failure
regrun run tests/regression/ --fail-fast

# JSON output for CI parsing
regrun run tests/regression/ --output json

# Skip setup (variables already populated from a prior run)
regrun run tests/regression/ --layer mcp --skip-setup

# Iterate on one group without running the sweep/cleanup groups
regrun run tests/regression/ --group 5 --skip-cleanup

# Skip preflight dependency-health checks (deliberate local override)
regrun run tests/regression/ --skip-preflight

# Bypass the per-product run lock (allow a concurrent run for this product)
regrun run tests/regression/ --no-lock
```

### Preflight checks

A top-level `preflight:` block lists read-only probes that run **once, before any group**, and abort the run in seconds naming the failed dependency (kills the degraded-backend grind regime). Each check is a `Test`-shaped body on any runner + a `name` + a `timeout` (default 10s). **No `eventually:` / `capture:`** (validation-rejected). First failure → `PREFLIGHT FAILED: <name>` + diagnostics, non-zero exit, **zero groups run**. Passing runs print `preflight: N checks passed`. `--skip-preflight` bypasses; `--dry-run` lists them. Silently ignored by a pre-0.8.0 binary → lint **W006** + the header line make that detectable.

```yaml
preflight:
  - name: db-reachable
    runner: sql
    sql: "SELECT 1;"
    assert: { last_exit_code: 0 }
```

### Run lock

Every run holds an exclusive `fcntl.flock` on `{REGRUN_RUNS_DIR|~/.regrun/runs}/{product}/.lock`. A second concurrent run for the same product **exits code 2** naming the product + lock path. flock self-releases on process death (incl. SIGKILL) — no stale-lock protocol. `--no-lock` bypasses. `REGRUN_RUNS_DIR` must be local (flock is unreliable over NFS).

## Failure Diagnostics (default) & Run Artifacts

A failing test is fully explained on the FIRST run — no `--verbose`, no re-run. regrun prints a `Failures` section (between the results table and the summary) with the request echo, response status + body, every failed assertion at full length, and the `eventually` attempt count. `Result: PASS|FAIL` stays the last line. Auth headers and resolved token values are redacted; response bodies are truncated to 2000 chars (`REGRUN_DIAG_BODY_LIMIT`).

Every run also persists the complete report to `{REGRUN_RUNS_DIR or ~/.regrun/runs}/{product}/{timestamp}/report.txt` + `report.json`, and stdout ends with `Full report: <path>/report.txt (json: report.json)`. **Read that file instead of re-running the suite** to inspect a failure — `--output json`'s `diagnostics` field carries the same data.

## Linting a Suite

Static analysis — no network, no execution. Run before committing suite changes.

```bash
# Lint (exit 1 on any error rule)
regrun lint tests/regression/

# Fail on warnings too
regrun lint tests/regression/ --strict

# Raise/lower the eventually: budget floor, allow positional asserts in a file
regrun lint tests/regression/ --budget-floor 90 --allow-positional '06_*.yaml'
```

| Rule | Sev | Meaning |
|------|-----|---------|
| E001 | error | Duplicate group id within a file |
| E002 | error | mcp-layer file sorts after a `*cleanup*` file (shared api_key revoked) |
| E003 | error | Test has `auth:` with a null value (the `auth: none` trap) |
| W001 | warn | MCP tool test asserts `is_error` with no `json_path` |
| W002 | warn | `equals`/`contains` on a positional array path (`[0]`/`[*]`) — suppress with inline `# lint: allow-positional` or `--allow-positional GLOB` |
| W003 | warn | `eventually:` ceiling below the budget floor (default 75s) |
| W004 | warn | POST/create-shaped test with no `{{RUN_ID}}`/`{{timestamp}}` (4xx tests skipped) |
| W005 | warn | Cleanup-flagged group references a variable captured in another group |
| W006 | warn | Suite directory declares no `preflight:` block in any file (missing dependency-health probes) |

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

**Exit codes:** 0 = all passed, 1 = any failure/error/preflight abort, 2 = run-lock contention (another run for this product is in progress).

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
