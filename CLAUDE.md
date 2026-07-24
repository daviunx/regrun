# regrun

Deterministic YAML-driven regression test runner for APIs, MCP servers, SQL, bash, and WebSocket streams. The fleet's regression engine — every product's `tests/regression/` suite runs on it.

**Stack:** Python 3.11+, Poetry, httpx / fastmcp / websocket runners. Published to **public PyPI**.

---

## Folder Map

| Folder | What |
|--------|------|
| `src/regrun/cli.py` | CLI entrypoints — `run`, `lint` |
| `src/regrun/engine/` | Execution core — executor, assertions, variables, retry, reporter, diagnostics, linter, run_lock, junit, artifacts |
| `src/regrun/runners/` | One module per runner — httpx, fastmcp, bash, sql, websocket (+ `mcp_response` normalization) |
| `src/regrun/models.py` | Pydantic models for the YAML schema |
| `tests/unit/` | Unit tests per engine/runner module |
| `tests/integration/cli/` | CLI-level integration tests |
| `planning/` | Design notes for in-flight regrun features |

---

## Tests

| What | Run |
|------|-----|
| Full suite | `poetry run pytest` |
| Unit only | `poetry run pytest tests/unit/` |
| Coverage (CI floor is `fail_under` in pyproject) | `poetry run pytest --cov` |
| Lint (what CI enforces) | `ruff check src/ tests/ && ruff format --check src/ tests/` |
| Typecheck (what CI enforces) | `poetry run mypy src/regrun` |

---

## Release

**This repo is GitHub + PUBLIC PyPI — `gh` is correct here, `glab` cannot work.** It is the one exception to the fleet's NEVER-`gh` rule.

| Step | Command |
|------|---------|
| 1. Bump | `version` in `pyproject.toml` + `CHANGELOG.md` entry |
| 2. Push main (free) | `git push origin main` |
| 3. Tag == HEAD (operator-gated) | `git push origin v<x.y.z>` |
| 4. Verify pipeline | `gh run watch` |
| 5. Verify artifact landed | `curl -s https://pypi.org/pypi/regrun/json \| jq -r .info.version` |

---

## Gotchas

- **A release is the TAG, not the commit.** `.github/workflows/publish.yml` fires ONLY on `v*.*.*` tags — pushing `main` publishes NOTHING. A version bump sitting on main is not released
- **The tag push is irreversible.** It lands on public pypi.org/project/regrun — a burned version can be yanked but NEVER reused. This is the one push in the fleet that stays operator-gated; the `main` push is free
- **A green pipeline does not prove the upload landed.** Verify BOTH `gh run watch` green AND the live version from the PyPI JSON API
- **A red suite burns the tag.** `publish.yml` calls `test.yml` and depends on it, so a tag whose suite fails publishes NOTHING — and that version number can never be reused on public PyPI. Run `poetry run pytest && poetry run mypy src/regrun` locally BEFORE tagging, not after
- **mypy runs at default strictness, not `strict = true`.** A documented deviation from `python/03-tooling.md`, recorded in `[tool.mypy]` — ratchet task `5500a359`. Do not describe regrun's tooling as fully standards-compliant until that lands
- **ruff runs its DEFAULT rules only.** No `[tool.ruff.lint] select` block — the mandated 14-group ruleset has ~103 outstanding violations (task `0e89a609`). `ruff check` passing does not mean the fleet ruleset passes
- Consumers pin regrun in the monorepo — after publishing, bump the pin where it is consumed and `poetry lock` there
- This repo is PUBLIC. Never put customer names, internal hostnames, tokens, or fleet-internal URLs in code, tests, docs, or this file
