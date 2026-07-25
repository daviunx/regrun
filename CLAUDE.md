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

## Security — this repo is PUBLIC and ships to public PyPI

Anyone can read this code, fork it, and open a PR against it; anyone who runs `pip install regrun` executes what the release job publishes. Treat the release path as a supply chain, not a deploy.

| Rule | Why |
|------|-----|
| **Every third-party `uses:` is pinned to a full 40-char commit SHA**, with the version in a trailing comment | A tag or branch ref is MUTABLE — it can be repointed at new code without the ref changing. There is NO lock file for GitHub Actions: the SHA in the YAML is the only pin that exists |
| **NEVER `@vN` / `@main` / `@release/vN`** on an action | `pypa/gh-action-pypi-publish@release/v1` is a BRANCH. It ran in the job holding `id-token: write`, so a force-push upstream would have executed attacker code holding the credential that publishes as regrun |
| **`id-token: write` lives on the `publish` job ONLY** — never workflow-level | Workflow-level permissions are inherited by every job, including reusable workflows it calls. The mint must not be in scope for jobs that run tests |
| **The publish job runs NO project code** — no checkout, no build. It downloads the `dist` artifact and uploads it | `python -m build` executes the build backend (poetry-core, unpinned) from PyPI. That must never share a process with the OIDC credential. Build is a separate `needs:`-linked job with no `id-token` |
| **A job-level `permissions:` block REPLACES, never merges** | Any scope you omit becomes `none`. `contents: read` must be listed explicitly alongside `id-token: write` or `checkout`/`download-artifact` break |
| **`on: pull_request`, NEVER `pull_request_target`** | Forks can open PRs here. The test job runs fork-authored code and `pip install .` runs the fork's build backend. `pull_request_target` gives that base-repo context, secrets, and a write token — the classic "pwn request" |
| **`persist-credentials: false` on every checkout** | Default `true` writes `GITHUB_TOKEN` into `.git/config`, readable by anything running afterwards — including fork test code |
| **No customer names, internal hostnames, tokens, or fleet-internal URLs** in code, tests, docs, or this file | It is all world-readable, forever, including in git history |

**Known open gaps** — do not describe this repo as hardened until they close:

| Gap | Task |
|-----|------|
| No committed `poetry.lock` (gitignored) → CI resolves deps fresh and unpinned, and that job gates the publish | `beb1608a` |
| No dependency scanning at all — `osv-scanner` reads `poetry.lock`, so it is blocked on the row above | `beb1608a` |
| `pypi` environment has no tag-protection rule, so `needs:` is a SOFT gate — a tag can be pushed from a branch with the test job deleted | `ba65649f` (operator, repo settings) |

---

## Gotchas

- **A release is the TAG, not the commit.** `.github/workflows/publish.yml` fires ONLY on `v*.*.*` tags — pushing `main` publishes NOTHING. A version bump sitting on main is not released
- **The tag push is irreversible.** It lands on public pypi.org/project/regrun — a burned version can be yanked but NEVER reused. This is the one push in the fleet that stays operator-gated; the `main` push is free
- **A green pipeline does not prove the upload landed.** Verify BOTH `gh run watch` green AND the live version from the PyPI JSON API
- **A red suite burns the tag.** `publish.yml` calls `test.yml` and depends on it, so a tag whose suite fails publishes NOTHING — and that version number can never be reused on public PyPI. Run `poetry run pytest && poetry run mypy src/regrun` locally BEFORE tagging, not after
- **mypy runs at default strictness, not `strict = true`.** A documented deviation from `python/03-tooling.md`, recorded in `[tool.mypy]` — ratchet task `5500a359`. Do not describe regrun's tooling as fully standards-compliant until that lands
- **ruff's select is EXPLICIT and narrow — `["E4", "E7", "E9", "F"]`.** Those four are ruff's pre-0.16 default; the block exists because the default is not a stable contract. 0.16.0 widened it (I, BLE, UP, RUF, SIM, PIE, DTZ, PYI) and reddened CI on unchanged code, since the Lint job installed ruff unpinned. `ruff check` passing does NOT mean the fleet's mandated 14-group ruleset passes — that is 156 raw findings, task `0e89a609`
- **ruff's version is pinned in `[dependency-groups] dev`, and CI installs it from there** via `pip install --group dev .` — never `pip install ruff`. A gate that resolves its own tooling at run time fails on someone else's release date, not on your commit
- Consumers pin regrun in the monorepo — after publishing, bump the pin where it is consumed and `poetry lock` there
- This repo is PUBLIC. Never put customer names, internal hostnames, tokens, or fleet-internal URLs in code, tests, docs, or this file
