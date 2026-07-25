"""CLI integration tests for the per-product, per-target run lock (0.9.0).

Holds an exclusive ``fcntl.flock`` on ``{REGRUN_LOCK_DIR}/{product}--{target}.lock``
in the test process (a separate open file description conflicts with the
runner's non-blocking acquire even in-process), then drives ``regrun run`` via
``CliRunner``:

  * lock held (same target) -> ``regrun run`` exits code 2, naming product +
    target + lock path.
  * lock held + ``--no-lock`` -> the run proceeds (bypass proven).
  * lock held for a DIFFERENT target -> the run proceeds (concurrent isolates).
  * ``REGRUN_RUNS_DIR`` does NOT move the lock (the 0.8.x CI bypass is closed).

The suite fixture is bash-only with no endpoint, so the derived target is
``default``; ``REGRUN_LOCK_TARGET`` overrides it explicitly.
"""

import fcntl
import os
from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli


def _write_yaml(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _passing_suite() -> dict:
    return {
        "meta": {"product": "demo", "layer": "api", "runner": "bash"},
        "groups": [
            {
                "id": 5,
                "name": "API Surface",
                "priority": "high",
                "tests": [
                    {
                        "id": "API.1",
                        "name": "ok",
                        "commands": [{"cmd": "true"}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            }
        ],
    }


def _suite_dir(tmp_path: Path) -> Path:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _passing_suite())
    return test_dir


def _hold_lock(lock_dir: Path, target: str = "default") -> tuple[int, Path]:
    """Create + exclusively flock the product--target lock file; return (fd, path)."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"demo--{target}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd, lock_path


def _release(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _invoke(test_dir: Path, tmp_path: Path, *args: str, env: dict | None = None):
    full_env = {"REGRUN_RUNS_DIR": str(tmp_path / "runs")}
    full_env.update(env or {})
    return CliRunner().invoke(cli, ["run", str(test_dir), *args], env=full_env)


def test_lock_contention_exits_two(tmp_path: Path, monkeypatch) -> None:
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("REGRUN_LOCK_DIR", str(lock_dir))
    test_dir = _suite_dir(tmp_path)
    fd, _lock_path = _hold_lock(lock_dir)
    try:
        result = _invoke(test_dir, tmp_path)
        assert result.exit_code == 2, result.output
        assert "demo" in result.output, result.output
        assert "default" in result.output, result.output
        assert ".lock" in result.output, result.output
    finally:
        _release(fd)


def test_no_lock_bypasses_contention(tmp_path: Path, monkeypatch) -> None:
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("REGRUN_LOCK_DIR", str(lock_dir))
    test_dir = _suite_dir(tmp_path)
    fd, _lock_path = _hold_lock(lock_dir)
    try:
        result = _invoke(test_dir, tmp_path, "--no-lock")
        assert result.exit_code == 0, result.output
    finally:
        _release(fd)


def test_different_target_runs_concurrently(tmp_path: Path, monkeypatch) -> None:
    # A lock held for isolate A must NOT serialize a run against isolate B —
    # the mechanical unblock for parallel worktrees (RGRN-06).
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("REGRUN_LOCK_DIR", str(lock_dir))
    test_dir = _suite_dir(tmp_path)
    fd, _lock_path = _hold_lock(lock_dir, target="isolate-a")
    try:
        result = _invoke(test_dir, tmp_path, env={"REGRUN_LOCK_TARGET": "isolate-b"})
        assert result.exit_code == 0, result.output
    finally:
        _release(fd)


def test_same_explicit_target_still_serializes(tmp_path: Path, monkeypatch) -> None:
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("REGRUN_LOCK_DIR", str(lock_dir))
    test_dir = _suite_dir(tmp_path)
    fd, _lock_path = _hold_lock(lock_dir, target="isolate-a")
    try:
        result = _invoke(test_dir, tmp_path, env={"REGRUN_LOCK_TARGET": "isolate-a"})
        assert result.exit_code == 2, result.output
        assert "isolate-a" in result.output, result.output
    finally:
        _release(fd)


def test_runs_dir_no_longer_moves_the_lock(tmp_path: Path, monkeypatch) -> None:
    # RGRN-07: the lock used to live under REGRUN_RUNS_DIR, so a per-job runs
    # dir gave every CI job its own lock file and the guarantee evaporated.
    # Now a held lock contends regardless of where REGRUN_RUNS_DIR points.
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("REGRUN_LOCK_DIR", str(lock_dir))
    test_dir = _suite_dir(tmp_path)
    fd, _lock_path = _hold_lock(lock_dir)
    try:
        result = _invoke(test_dir, tmp_path, env={"REGRUN_RUNS_DIR": str(tmp_path / "other-runs")})
        assert result.exit_code == 2, result.output
    finally:
        _release(fd)
