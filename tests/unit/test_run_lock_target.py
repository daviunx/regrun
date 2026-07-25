"""Unit tests for lock-target derivation and the fixed lock dir (0.9.0)."""

from pathlib import Path

from regrun.engine.run_lock import _locks_base_dir, derive_lock_target


def test_target_defaults_without_endpoint_or_env(monkeypatch) -> None:
    monkeypatch.delenv("REGRUN_LOCK_TARGET", raising=False)
    assert derive_lock_target(None) == "default"


def test_target_from_endpoint_host(monkeypatch) -> None:
    monkeypatch.delenv("REGRUN_LOCK_TARGET", raising=False)
    assert derive_lock_target("http://demo.localhost") == "demo.localhost"


def test_target_from_endpoint_host_with_port(monkeypatch) -> None:
    monkeypatch.delenv("REGRUN_LOCK_TARGET", raising=False)
    assert derive_lock_target("http://demo.localhost:8080") == "demo.localhost-8080"


def test_explicit_lock_target_wins_over_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("REGRUN_LOCK_TARGET", "my-isolate")
    assert derive_lock_target("http://demo.localhost") == "my-isolate"


def test_explicit_lock_target_is_sanitized(monkeypatch) -> None:
    monkeypatch.setenv("REGRUN_LOCK_TARGET", "iso late/slug")
    assert derive_lock_target(None) == "iso-late-slug"


def test_lock_dir_ignores_runs_dir(monkeypatch, tmp_path: Path) -> None:
    # RGRN-07: the lock location must NOT move with REGRUN_RUNS_DIR.
    monkeypatch.delenv("REGRUN_LOCK_DIR", raising=False)
    monkeypatch.setenv("REGRUN_RUNS_DIR", str(tmp_path / "per-job-runs"))
    assert _locks_base_dir() == Path.home() / ".regrun" / "locks"


def test_lock_dir_explicit_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REGRUN_LOCK_DIR", str(tmp_path / "locks"))
    assert _locks_base_dir() == tmp_path / "locks"
