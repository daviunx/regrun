# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0] - 2026-09-20

File isolation: a suite file declares what it consumes, and the engine can then act on it. One file can be run and trusted, a red run costs one file's re-run instead of the whole suite, a broken producer yields one failure instead of a cascade, and a suite can be split across disjoint stacks.

### Added

- **`meta.requires:`** (list of file stems) declares the files a file consumes captured values from. The setup layer is the bootstrap contract every file already depends on and is never listed. One declaration drives all of the features below.
- **BLOCKED**, a discriminated sub-kind of `skipped`. When a file fails, every file requiring it (directly or transitively) is not run, **and a failed `layer: setup` file blocks every later file whether it declared a dependency or not** (setup files never declare `requires:` on each other, so no graph edge can carry it). The first failed setup file stays the blocker, so the report names the root cause rather than the latest symptom and its tests report `BLOCKED` naming the blocker, in the text report, in `report.json` (`blocked_by`, plus a `blocked` count) and in JUnit (`<skipped message="blocked by ...">`). One broken producer now yields one actionable failure instead of a wall of red with a single cause. The exit code still tracks real failures and errors.
- **`--file <stem-or-glob>`** (repeatable) runs only the matching files, automatically pulling the setup layer and the `requires` closure of each match, in the canonical order. A pattern matching nothing is an error, never a silent zero-file run. Composes with `--group` and `--skip-setup`; a file present only as a dependency keeps all of its groups, because narrowing groups must not drop the variables the selected file needs.
- **`--rerun-failed`** re-runs only the files that failed, errored or were blocked in the latest report for this product and target, echoes the report it read, and exits 0 saying so when there is nothing to re-run. Resolution is target-scoped, so two isolates of one product never re-run each other's failures.
- **`--shard k/n`** splits a suite into deterministic subsets: a file and its closure stay together, the setup layer runs in every shard, a `serial: true` file gets the last shard to itself, and the rest are packed greedily by weight (test count when no timings are supplied). `--dry-run --shard k/n` prints the plan so a pipeline author can diff subsets before wiring a matrix. **Each shard requires a disjoint environment (its own database and index prefix); regrun cannot verify this and the help text says so.**
- **`meta.serial: true`** marks a file that asserts process-global behaviour (a rate limit, a global counter, a singleton lock) and therefore must never share a shard with unrelated traffic.
- **Time budgets, off unless declared.** `meta.budget_seconds` per file and `--budget-seconds` per run. An overrun reds the run and the report names the file and the overrun, without reclassifying any test: the test passed, the budget did not.
- **A per-file timing table** in the text report and `report.json` (`file_timings`: tests, duration, share of the run), so suite rot is visible where it starts instead of being inferred from a total.
- **Lint rule W012** (warn) flags a file that uses a variable another suite file captures without declaring `meta.requires:` for it: coupling that holds only while the full suite runs in order, and breaks on a filtered run, a single-file re-run or a shard boundary. Cleared by declaring the dependency, by producing the value locally, or by the producer being a setup file. A warning on purpose, so an existing suite can adopt it without a red gate.
- **Lint rule E005** (error) rejects a `meta.requires:` entry no run can satisfy: an unknown stem, the file itself, a cycle, or a file that runs after its dependent. W012 reads every template form of a reference (`{{ VAR }}`, `{{ VAR | filter }}`, `{{ VAR.field }}`), so a filtered or dotted use of a borrowed value is not a blind spot.
- **`run` itself rejects an unsatisfiable `meta.requires:`** before executing anything: an unknown stem, a self-reference or a cycle aborts with a non-zero exit naming the file, the entry and the known files. Silently dropping it left the run green with blocked-skip disabled for that dependency, which is the coupling the declaration exists to remove. A producer left out by the operator's own narrowing (`--file`, `--layer`, `--shard`) stays tolerated: a filtered run cannot judge a producer it never loaded.

### Changed

- **Unknown `meta` keys are now rejected at load** (`extra="forbid"`, matching `Assertion` and `Test` since 0.9.0). A typo'd `require:` was previously accepted and silently ignored, leaving the file with no declared dependency, selection blind to the coupling, and nothing anywhere saying so. The keys only external orchestration reads (`health_path`, `mcp_health_path`) are declared fields, so a suite carrying them still parses. *No opt-out*: a rejected key is a defect, fix the key.
- **`--skip-setup` now drops the setup layer whatever else was selected.** It previously took effect only when `--layer` was also given, so on its own it silently did nothing.

### Internal

- `executor.py`, `linter.py` and the `run` command were decomposed into focused modules (`engine/depgraph.py`, `engine/shardplan.py`, `engine/budgets.py`, `engine/rerun.py`, `engine/blocked.py`, `engine/selection.py`, `engine/lint_rules/`, `cli_output.py`) before any of the above was written. Behaviour-preserving, one commit at a time, full suite green between each.

## [0.9.1] - 2026-07-26

### Fixed — false-green door: dangling auth-profile references

- **A test referencing an auth profile not defined in ITS OWN file now FAILS** with `unknown auth profile in test <id>` — before any request is built. Auth profiles are per-file (only captured variables propagate cross-file via the VariableStore); previously a dangling `auth:` or `meta.default_auth` reference logged a WARN and sent the request with **no credentials**, surfacing as a 401-instead-of-403 that reads like a product bug. Same closed-world doctrine as strict-vars. Scoped to auth-consuming runners (`httpx`/`fastmcp`/`websocket`) — bash/sql tests never read auth config. *No opt-out*: a dangling reference is a defect, define the profile or use `none`.

### Added

- **Lint rule E004** (error) — a test on an auth-consuming runner references an auth profile absent from that file's `auth:` block (via `auth:` or `meta.default_auth`). The static twin of the runtime guard; fleet-scanned clean on all four product suites before enabling.

## [0.9.0] - 2026-07-25

Runner-owned stability guarantees: the authoring disciplines that kept suites stable (sweep-first, per-run fixture naming, parameterized bash hosts, one-run-per-stack) move from documentation into the engine. Three false-green doors of the `max_attempts: 0` family (closed in 0.8.3) are closed for good.

### Fixed — false-green doors

- **A test whose evaluated assertion list is EMPTY now FAILS** with an explicit `zero assertions evaluated` error. `assert: {}` or `assert: {json_path: {}}` evaluated zero assertions and passed via `all([]) == True` — a test could report PASSED having checked nothing.
- **A typo'd assertion or test key now fails the file at load.** `Assertion` and `Test` are `extra="forbid"`: `statuss: 201` / `jsonpath:` used to be silently ignored (pydantic default `extra="ignore"`), silencing the whole block. Verified against all 71 fleet suite files before enabling — zero extra keys in use. *No opt-out*: a rejected key is a defect, fix the key.
- **An unresolved `{{VAR}}` now FAILS the test** naming the variable and the test id (**strict-vars, DEFAULT ON**). It used to render as the raw literal with a WARN log — a capture that never landed or an undeclared `RUN_ID` named fixtures `regr-x-{{RUN_ID}}` byte-identical on every run, a guaranteed silent cross-run collision. A broken file-level `variables:` declaration aborts the run before any group. **Backcompat opt-outs:** `meta.strict_vars: false` per file, `--no-strict-vars` per run (for suites that deliberately template literal braces).

### Added

- **Engine-owned `RUN_ID` builtin.** Generated once per run, in the exact `{int(time)}{hex4}` format `{{timestamp}}` produces, so suites no longer need to hand-declare `RUN_ID: "{{timestamp}}"` in `00_setup.yaml`. **Backcompat: a suite that declares or captures `RUN_ID` shadows the builtin — the suite's value wins and nothing changes.** The run's effective RUN_ID is recorded in `RunResult.run_id`, printed as a `run_id:` report header line, and exported into every bash child env.
- **First-class `sweep:` block** — the structural sweep-first guarantee. A top-level block (peer of `preflight:`, typically in the setup file) of bash/sql/httpx-shaped steps, runner-executed ONCE, after `preflight:` and before any group. A step failure ABORTS the run with exit 1 and zero groups executed (`SWEEP FAILED: <name>` + diagnostics): a suite must not create fixtures into an environment it could not sweep. `capture:` — test-level or per-command — is rejected at validation (sweeps must be capture-independent), as is `eventually:` (a sweep is a delete, not a poll). `--skip-sweep` suppresses; `--dry-run` lists steps; a passing run's header prints `sweep: N steps completed`. `cleanup: true` group semantics are untouched and remain the tail-end backstop.
- **Bash child env injection.** `BashRunner` now passes `os.environ` + the resolved `REGRUN_API_ENDPOINT` / `REGRUN_MCP_ENDPOINT` + the run's `RUN_ID` to every command — bash steps always know the stack under test, so `${REGRUN_API_ENDPOINT}` is always correct and hardcoding a host becomes unnecessary rather than merely discouraged.
- **Report provenance.** The report header (and `report.json`) now carries the regrun version that adjudicated the run, the resolved api/mcp endpoints, the run's `RUN_ID` and the lock target — "why did this go red between tasks" is one line of reading, not archaeology.
- **Lint rules W007–W011:**
  - **W007** — suite has neither a `sweep:` block nor a `cleanup: true` group sorting before its first create-shaped test (sweep-first unenforced). Directory-level.
  - **W008** — bash `cmd` carrying a hardcoded `http(s)://` host or `psql … -d <name>` database literal not wrapped in `{{ env.get(...) }}` / `${VAR:-default}`; also fires inside `sweep:` steps.
  - **W009** — `json_path` condition whose ONLY operator is `exists: true` (satisfied by null — use `not_empty` or a value). Inline suppress: `# lint: allow-exists`.
  - **W010** — `$.data.*` in `json_path` or `capture` within an mcp-layer file (asserts/captures run on the POST-normalize body; `data` is hoisted). Segment-exact: `$.database` is not a hit.
  - **W011** (best-effort) — created fixture-name prefixes (`<prefix>{{RUN_ID}}`) that appear in NO sweep step or `cleanup: true` group: orphan families listed by name. Directory-level.

### Changed

- **Target-aware run lock in a FIXED lock dir.** Lock key is now `product + target` (was: product alone), where target = `REGRUN_LOCK_TARGET` when set, else a sanitized host slug of the resolved API endpoint, else `default`. Two runs of the same product against DIFFERENT stacks (worktree isolates) now run concurrently; the same stack still serializes with exit 2. The lock file moved from `{REGRUN_RUNS_DIR}/{product}/.lock` to `~/.regrun/locks/{product}--{target}.lock` — deliberately independent of `REGRUN_RUNS_DIR`, which CI sets per job and which therefore gave every CI job its own lock file, silently voiding the no-concurrency guarantee. `REGRUN_LOCK_DIR` exists as an explicit override.
- **Target-namespaced artifacts.** Run reports now land in `{REGRUN_RUNS_DIR|~/.regrun/runs}/{product}/{target}/{timestamp}/` (was: no target segment) using the same target slug as the lock, so two isolates of one product never interleave reports in one folder. Tooling that globs run dirs must add one path level (fleet CI globs `regrun-runs/**/junit.xml` — unaffected).
- **File-level `variables:` now merge sequentially**, so a later declaration can reference an earlier one (`TAG: "regr-vis-{{RUN_ID}}"` after `RUN_ID: "{{timestamp}}"`). Previously the whole dict was rendered before any value was stored, leaving such references as literals.
- **W004 is no longer satisfied by an inline `{{timestamp}}`** — the builtin is recomputed on every render, so an inline use yields a value stored nowhere: the fixture was uncapturable AND unsweepable by any pattern the suite knows. Per-run uniqueness now requires `{{RUN_ID}}` or a run-scoped DECLARED variable (fixpoint over declarations deriving from `timestamp`/`uuid`/RUN_ID, cross-file).
- `render_test` no longer renders bash `commands` (the bash runner re-renders each command at execution time, after per-command captures) — required for strict-vars to coexist with mid-test capture chains; behavior-neutral otherwise.

### Compatibility

- `sweep:` is **silently ignored by a pre-0.9.0 binary** (unknown key), mitigated by lint W007 + the `sweep:` report header line. `meta.strict_vars` is likewise ignored by older binaries (which never fail on unresolved variables anyway).
- The two deliberate behavior-change opt-outs are **strict-vars** (`meta.strict_vars: false` / `--no-strict-vars`) and **sweep execution** (`--skip-sweep`). Everything else is either fail-loud-on-defect (no opt-out by design) or backcompat-by-precedence (suite-declared `RUN_ID` wins).

## [0.8.3] - 2026-07-25

### Fixed

- **`eventually: {max_attempts: 0}` reported a test as PASSED having evaluated zero assertions.** `EventuallyConfig.max_attempts` carried no lower bound, so at `0` the retry loop body never ran, `run_with_retry` returned `(None, [])`, and the executor's `all_passed = all(assertion_results)` — `all([])` being `True` — produced a green verdict with no crash, because the `None` response is only dereferenced on the failure branch. Any test could therefore be silenced into a false pass, and since regrun adjudicates every consumer's regression suite that verdict propagated downstream. `max_attempts` is now constrained `ge=1`, and `interval` / `backoff` / `initial_delay` are constrained `ge=0` (negative timings previously reached `asyncio.sleep` unchallenged). `run_with_retry` additionally asserts the invariant rather than assuming it, so relaxing the constraint fails loudly instead of silently returning to the false-green path.

### Changed

- **CI now runs the test suite, and the release is gated on it.** `test.yml` gained `Tests` (pytest + coverage, matrix 3.11/3.12/3.13) and `Typecheck` (mypy) jobs; previously it ran only `lint` and `import-test`, so a populated `tests/` tree never executed in CI and a green pipeline proved only that the package imported. `publish.yml` now calls `test.yml` as a reusable workflow and depends on it — a tag push with a red suite publishes nothing. OIDC `id-token: write` is scoped to the publish job alone instead of the whole workflow.
- **Type checking and coverage enforcement.** mypy is now a blocking CI job at default strictness, with `plugins = ["pydantic.mypy"]` and `python_version = "3.11"` (tracking the package's minimum `requires-python`, not the newest interpreter). The coverage floor moved from `fail_under = 38` — 34 points below actual — to `70`, matching measured branch coverage rounded down.
- Lint scope widened from `src/` to `src/ tests/`.
- **The lint gate no longer resolves its own tooling at run time.** The `Lint` job ran `pip install ruff` with no bound, so it consumed whatever ruff had shipped by the time the job started; ruff 0.16.0 widened its *default* rule set (adding I, BLE, UP, RUF, SIM, PIE, DTZ, PYI) and turned 31 findings red on code that had not changed. ruff is now declared in `[dependency-groups] dev` and installed from there via the same `pip install --group dev .` the test and typecheck jobs use, and `[tool.ruff.lint] select` is declared explicitly as `["E4", "E7", "E9", "F"]` — ruff's own pre-0.16 default, i.e. the bar already in force, pinned so an upstream release can no longer change it. Same defect class as the unpinned action refs below: a release gate must not depend on someone else's release date. Adopting the wider fleet ruleset is tracked separately.

### Security

- **Every third-party GitHub Action is now pinned to a full commit SHA.** `pypa/gh-action-pypi-publish@release/v1` was a *branch* ref executing inside the only job that holds `id-token: write` — the OIDC credential authorised to publish to pypi.org as regrun. A force-push or upstream compromise on that branch would have run attacker-controlled code holding that credential, publishing a backdoored package to everyone who installs it. `actions/checkout`, `actions/setup-python` and the three `docker/*` actions are pinned for the same reason. There is no lock file for GitHub Actions — the SHA in the workflow is the only pin that exists.
- **Build is now isolated from publish.** `python -m build` downloads and executes the build backend (`poetry-core`, unpinned and unhashed) from PyPI, and previously did so in the same job as the OIDC mint. It now runs in a separate job carrying no `id-token`, handing over a `dist` artifact; the publish job checks out nothing and runs only the download + upload steps.
- `persist-credentials: false` on every checkout — the default writes `GITHUB_TOKEN` into `.git/config`, where later steps (including fork-authored test code on a public repo) can read it.

### Docs

- `README.md` said "Requires Python 3.12 or later" while `requires-python` declared `>=3.11` and CI tested 3.11. The packaging metadata is the contract pip and PyPI enforce, so the README was corrected to 3.11+ rather than narrowing support and breaking consumers already resolving on 3.11.

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
