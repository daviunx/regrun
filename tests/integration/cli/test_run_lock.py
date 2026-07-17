"""CLI integration tests for the per-product run lock (Phase 3) — RED gate.

Holds an exclusive ``fcntl.flock`` on ``{REGRUN_RUNS_DIR}/{product}/.lock`` in
the test process (a separate open file description conflicts with the runner's
non-blocking acquire even in-process), then drives ``regrun run`` via
``CliRunner``:

  * lock held -> ``regrun run`` exits code 2, naming the product + lock path.
  * lock held + ``--no-lock`` -> the run proceeds (bypass proven).

All red against current regrun 0.7.0 (no lock; no ``--no-lock`` flag).
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
                    {"id": "API.1", "name": "ok", "commands": [{"cmd": "true"}], "assert": {"last_exit_code": 0}}
                ],
            }
        ],
    }


def _suite_dir(tmp_path: Path) -> Path:
    test_dir = tmp_path / "suite"
    test_dir.mkdir()
    _write_yaml(test_dir, "01_api.yaml", _passing_suite())
    return test_dir


def _hold_lock(runs_dir: Path) -> tuple[int, Path]:
    """Create + exclusively flock the product lock file; return (fd, path)."""
    lock_dir = runs_dir / "demo"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd, lock_path


def _release(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def test_lock_contention_exits_two(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    test_dir = _suite_dir(tmp_path)
    fd, lock_path = _hold_lock(runs_dir)
    try:
        result = CliRunner().invoke(
            cli, ["run", str(test_dir)], env={"REGRUN_RUNS_DIR": str(runs_dir)}
        )
        assert result.exit_code == 2, result.output
        assert "demo" in result.output, result.output
        assert ".lock" in result.output, result.output
    finally:
        _release(fd)


def test_no_lock_bypasses_contention(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    test_dir = _suite_dir(tmp_path)
    fd, _lock_path = _hold_lock(runs_dir)
    try:
        result = CliRunner().invoke(
            cli, ["run", str(test_dir), "--no-lock"], env={"REGRUN_RUNS_DIR": str(runs_dir)}
        )
        assert result.exit_code == 0, result.output
    finally:
        _release(fd)
