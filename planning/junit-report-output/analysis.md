# Analysis: JUnit XML Report Output

## Requirements

Add JUnit XML output to regrun so CI regression failures render per-test in GitLab's MR Tests tab. Pure addition — existing report.txt/report.json must stay byte-identical.

## Current State

- `engine/reporter.py` — `RunResult` model + `format_text()` / `format_json()` formatters
- `engine/artifacts.py` — persists `report.txt` + `report.json` to `{REGRUN_RUNS_DIR}/{product}/{timestamp}/`
- `cli.py` — orchestrates: run tests -> format -> echo -> persist artifacts
- `engine/diagnostics.py` — `FailureDiagnostics` with redaction/truncation (reuse for JUnit failure bodies)
- `TestResult` has: `test_id`, `test_name`, `group_name`, `passed`, `skipped`, `error`, `duration_ms`, `diagnostics`

## Technical Approach

### New file: `engine/junit.py`

Pure function `format_junit(run_result: RunResult) -> str` that produces JUnit XML.

**Mapping (per GitLab JUnit spec):**
- `<testsuites>` root element
- `<testsuite>` per source YAML file — but `RunResult` currently doesn't track source file. Since `TestResult` groups by `group_name`, and the task spec says "per suite FILE (file_stem)", we need to thread the source file stem through. However, `RunResult.test_results` is a flat list with `group_name` only.

  **Decision:** The task spec says `<testsuite>` per suite FILE. But `RunResult` loses file provenance — all results are flattened. We have two options:
  1. Thread file stem through `TestResult` (adds a field)
  2. Use `group_name` as the testsuite name (one `<testsuite>` per unique group)

  Option 1 is cleaner and matches the spec. Add `file_stem: str = ""` to `TestResult`, set it in `executor.py` from `Path.stem`.

- `<testcase>` per test: `classname="{product}.{file_stem}.{group_name}"`, `name="{test_id} {test_name}"`, `time` in seconds
- FAIL: `<failure message="...">` with diagnostics body (reuse `_one_failure()` logic, XML-escaped, capped at 16KB)
- ERROR: `<error message="...">` with error string
- SKIP: `<skipped/>`
- XML-escape all text content via `xml.sax.saxutils.escape()`

### Changes to existing files

1. **`engine/reporter.py`** — add `file_stem: str = ""` field to `TestResult`
2. **`engine/executor.py`** — pass `path.stem` when constructing `TestResult` (set in `run_tests` loop)
3. **`engine/artifacts.py`** — add `REPORT_JUNIT = "junit.xml"`, write it alongside txt/json, update `pointer_line`
4. **`cli.py`** — generate junit report string, pass to `write_run_artifacts`
5. **`pyproject.toml`** — version bump `0.6.0` -> `0.7.0`
6. **`CHANGELOG.md`** — add 0.7.0 entry
7. **`README.md`** — add section with GitLab wiring snippet

### Size cap

Cap each `<failure>`/`<error>` body to 16KB (16384 chars) after XML escaping. GitLab truncates huge bodies poorly.

## Impact

- `TestResult` gets one new optional field (`file_stem`) — backward compatible (default `""`)
- `write_run_artifacts` signature adds `junit_report: str` parameter — breaking for any direct callers (only `cli.py`)
- Existing txt/json outputs unchanged (additive junit.xml alongside)
- No new dependencies (stdlib `xml.sax.saxutils`)

## Implementation Phases

1. **Phase 1: Model + emitter** — Add `file_stem` to `TestResult`, create `engine/junit.py` with `format_junit()`
2. **Phase 2: Thread file_stem** — Set `file_stem` in `executor.py` run loop
3. **Phase 3: Persistence** — Update `artifacts.py` + `cli.py` to write junit.xml
4. **Phase 4: Tests** — Unit tests for junit emitter (happy path, failure detail, error, skipped, XML escaping, size cap)
5. **Phase 5: Docs** — README section, CHANGELOG, version bump

## Tests

New test file: `tests/unit/test_junit.py`
- `test_junit_happy_path_all_pass` — valid XML, correct structure
- `test_junit_failure_with_diagnostics` — `<failure>` element with diagnostics body
- `test_junit_error_test` — `<error>` element
- `test_junit_skipped_test` — `<skipped/>` element
- `test_junit_xml_escaping` — special chars in names/bodies
- `test_junit_body_size_cap` — bodies over 16KB truncated
- `test_junit_file_stem_classname` — classname includes file_stem

## Applicable Standards

- `development/overview.md` — core principles
- `development/python.md` — Python patterns
- `development/git-conventions.md` — commit format
- `testing/overview.md` — test level decisions
