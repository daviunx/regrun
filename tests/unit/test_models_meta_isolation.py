"""Unit tests for the file-isolation keys on ``TestMeta`` (0.10.0).

Contract:

  * ``requires: list[str]`` — the file stems this file depends on; default ``[]``.
  * ``serial: bool`` — the file asserts process-global behaviour; default ``False``.
  * ``budget_seconds: float | None`` — per-file time budget; must be > 0.
  * ``health_path`` / ``mcp_health_path`` — read by external orchestration, never
    consumed by the engine, but DECLARED so ``extra="forbid"`` does not reject a
    suite that carries them.
  * every other meta key in fleet use parses.
  * an UNKNOWN meta key is REJECTED: a typo'd ``require:`` must not silently
    disable blocked-skip (the whole point of the tightening).
"""

import pytest
from pydantic import ValidationError

from regrun import models


def _meta(**overrides: object) -> dict:
    doc: dict = {"product": "demo", "layer": "api", "runner": "httpx"}
    doc.update(overrides)
    return doc


# --------------------------------------------------------------------------- requires


def test_requires_defaults_to_empty_list() -> None:
    meta = models.TestMeta.model_validate(_meta())
    assert meta.requires == []


def test_requires_accepts_list_of_stems() -> None:
    meta = models.TestMeta.model_validate(_meta(requires=["00_setup", "01_provider"]))
    assert meta.requires == ["00_setup", "01_provider"]


def test_requires_rejects_a_bare_string() -> None:
    """strict=True: a single stem must still be written as a list."""
    with pytest.raises(ValidationError):
        models.TestMeta.model_validate(_meta(requires="01_provider"))


# --------------------------------------------------------------------------- serial


def test_serial_defaults_to_false() -> None:
    meta = models.TestMeta.model_validate(_meta())
    assert meta.serial is False


def test_serial_accepts_true() -> None:
    meta = models.TestMeta.model_validate(_meta(serial=True))
    assert meta.serial is True


def test_serial_rejects_a_string() -> None:
    with pytest.raises(ValidationError):
        models.TestMeta.model_validate(_meta(serial="true"))


# --------------------------------------------------------------------------- budget


def test_budget_seconds_defaults_to_none() -> None:
    meta = models.TestMeta.model_validate(_meta())
    assert meta.budget_seconds is None


def test_budget_seconds_accepts_a_float() -> None:
    meta = models.TestMeta.model_validate(_meta(budget_seconds=12.5))
    assert meta.budget_seconds == 12.5


def test_budget_seconds_accepts_an_int_as_float() -> None:
    """strict=True still admits int -> float (pydantic's strict float rule)."""
    meta = models.TestMeta.model_validate(_meta(budget_seconds=30))
    assert meta.budget_seconds == 30.0


def test_budget_seconds_rejects_zero() -> None:
    """A zero budget would red every run it is declared on — a suite defect."""
    with pytest.raises(ValidationError):
        models.TestMeta.model_validate(_meta(budget_seconds=0))


def test_budget_seconds_rejects_a_negative_value() -> None:
    with pytest.raises(ValidationError):
        models.TestMeta.model_validate(_meta(budget_seconds=-1.0))


# --------------------------------------------------- externally-consumed health keys


def test_health_path_keys_default_to_none() -> None:
    meta = models.TestMeta.model_validate(_meta())
    assert meta.health_path is None
    assert meta.mcp_health_path is None


def test_health_path_keys_are_accepted_and_carried() -> None:
    """Read by external orchestration from the first suite yaml; engine ignores them."""
    meta = models.TestMeta.model_validate(
        _meta(health_path="/health", mcp_health_path="/mcp/health")
    )
    assert meta.health_path == "/health"
    assert meta.mcp_health_path == "/mcp/health"


# --------------------------------------------------------------------- extra="forbid"


def test_unknown_meta_key_is_rejected() -> None:
    """A typo'd ``require:`` must fail at load, not silently disable blocked-skip."""
    with pytest.raises(ValidationError, match="require"):
        models.TestMeta.model_validate(_meta(require=["01_provider"]))


def test_unrelated_unknown_meta_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        models.TestMeta.model_validate(_meta(budgets_seconds=10.0))


def test_every_meta_key_in_fleet_use_parses() -> None:
    """The meta-key census: each key a fleet suite carries must be a declared field."""
    meta = models.TestMeta.model_validate(
        {
            "product": "demo",
            "layer": "setup",
            "runner": "bash",
            "endpoint": "http://demo.localhost",
            "mcp_endpoint": "http://demo-mcp.localhost",
            "default_auth": "admin",
            "env_file": ".env",
            "health_path": "/health",
            "mcp_health_path": "/mcp/health",
            "strict_vars": True,
            "requires": [],
            "serial": False,
            "budget_seconds": 60.0,
            "sql_connection": {
                "docker_container": "demo-db-1",
                "docker_user": "demo",
                "database": "demo_test",
                "fallback_dsn": "postgresql://demo@localhost/demo_test",
            },
        }
    )
    assert meta.layer == "setup"
    assert meta.sql_connection is not None
