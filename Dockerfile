# =============================================================================
# regression-runner image — published to ghcr.io/daviunx/regrun on v* tags.
# =============================================================================
# Bakes the tooling a CI regression job needs:
#   - postgresql-client (pg_restore / psql / pg_isready) — prod-dump restore
#   - curl — pgdump download + sidecar health polling
#   - regrun + its deps (incl. fastmcp) — the test runner; the MCP runner is
#     in-process since 0.2.0, so uv/uvx is NOT needed.
#
# regrun is installed FROM THE BUILD CONTEXT (the tagged source), so the image
# always matches the commit it was built from — no PyPI publish-timing race.
#
# Built by .github/workflows/docker-publish.yml on `v*.*.*` tags.
# =============================================================================
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

COPY . /src
RUN pip install --no-cache-dir /src \
    && regrun --version

CMD ["bash"]
