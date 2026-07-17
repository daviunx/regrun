"""Per-product run lock: mechanically enforce the sweep-first no-concurrency rule.

A regression suite's sweep-first cleanup discipline assumes only one run per
product touches the shared environment at a time. This module holds an exclusive
non-blocking ``fcntl.flock`` on ``{REGRUN_RUNS_DIR|~/.regrun/runs}/{product}/.lock``
for the duration of a run. A concurrent run for the same product raises
``RunLockError`` (mapped to exit code 2 by the CLI).

flock self-releases on process death (incl. SIGKILL), so there is no stale-lock
protocol to maintain. ``fcntl`` is POSIX-only; regrun is already POSIX-only
(bash runner), but the import is guarded so locking degrades to a no-op rather
than crashing on a non-POSIX platform.
"""

import os
from pathlib import Path

import structlog

from regrun.engine import artifacts

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

logger = structlog.get_logger()


class RunLockError(Exception):
    """Raised when another regression run for the same product holds the lock."""

    def __init__(self, product: str, lock_path: Path) -> None:
        self.product = product
        self.lock_path = lock_path
        super().__init__(
            f"Another regression run for '{product}' is in progress (lock: {lock_path})"
        )


def acquire_run_lock(product: str) -> int | None:
    """Acquire the per-product run lock (non-blocking exclusive flock).

    Returns the held file descriptor (release it with :func:`release_run_lock`),
    or ``None`` when locking is unavailable — non-POSIX, or an unusable runs dir.

    Raises ``RunLockError`` on genuine contention (another holder).
    """
    if fcntl is None:  # pragma: no cover - non-POSIX platforms
        return None
    lock_dir = artifacts._runs_base_dir() / product
    # Creating the lock file is best-effort: an unusable runs dir (unwritable,
    # points at a file) must degrade to running unlocked, not crash the run —
    # the artifact write will surface the same environment problem as a warning.
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / ".lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        logger.warning("run_lock_unavailable", error=str(exc))
        return None
    # Only genuine flock contention (another holder) aborts the run.
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise RunLockError(product, lock_path) from exc
    return fd


def release_run_lock(fd: int | None) -> None:
    """Release a lock fd acquired by :func:`acquire_run_lock` (no-op for ``None``)."""
    if fd is None or fcntl is None:  # pragma: no cover - non-POSIX platforms
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
