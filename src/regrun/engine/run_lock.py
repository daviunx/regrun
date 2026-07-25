"""Per-product, per-target run lock: mechanically enforce sweep-first no-concurrency.

A regression suite's sweep-first cleanup discipline assumes only one run per
product touches a given TARGET STACK at a time. This module holds an exclusive
non-blocking ``fcntl.flock`` on ``{REGRUN_LOCK_DIR|~/.regrun/locks}/
{product}--{target}.lock`` for the duration of a run. A concurrent run for the
same product+target raises ``RunLockError`` (mapped to exit code 2 by the CLI);
runs against DIFFERENT targets (e.g. two isolate stacks) proceed concurrently.

The target key is, in order of precedence: the ``REGRUN_LOCK_TARGET`` env var
when set; a sanitized slug of the resolved API endpoint's host (which already
reflects ``REGRUN_API_ENDPOINT``); else ``"default"``.

The lock directory is FIXED — deliberately independent of ``REGRUN_RUNS_DIR``.
CI templates set a per-job runs dir, which used to move the lock file per job
and silently void the no-concurrency guarantee exactly where it was configured.
``REGRUN_LOCK_DIR`` exists as an explicit override (tests, exotic setups), but
nothing in the fleet sets it per job.

flock self-releases on process death (incl. SIGKILL), so there is no stale-lock
protocol to maintain. ``fcntl`` is POSIX-only; regrun is already POSIX-only
(bash runner), but the import is guarded so locking degrades to a no-op rather
than crashing on a non-POSIX platform.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import structlog

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

logger = structlog.get_logger()

_TARGET_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class RunLockError(Exception):
    """Raised when another regression run for the same product+target holds the lock."""

    def __init__(self, product: str, target: str, lock_path: Path) -> None:
        self.product = product
        self.target = target
        self.lock_path = lock_path
        super().__init__(
            f"Another regression run for '{product}' (target: {target}) is in progress "
            f"(lock: {lock_path})"
        )


def _locks_base_dir() -> Path:
    """Resolve the lock dir: fixed ``~/.regrun/locks`` unless explicitly overridden.

    Deliberately does NOT honour ``REGRUN_RUNS_DIR`` — the lock location must
    not move with the artifacts dir (see module docstring).
    """
    env = os.getenv("REGRUN_LOCK_DIR")
    if env:
        return Path(env)
    return Path.home() / ".regrun" / "locks"


def derive_lock_target(api_endpoint: str | None) -> str:
    """Derive the stable target slug for lock keying and artifact namespacing.

    Precedence: explicit ``REGRUN_LOCK_TARGET`` env var; else a sanitized host
    slug of the resolved API endpoint (``meta.endpoint`` after the
    ``REGRUN_API_ENDPOINT`` override was applied); else ``"default"``.
    """
    explicit = os.getenv("REGRUN_LOCK_TARGET")
    if explicit and explicit.strip():
        return _TARGET_SANITIZE_RE.sub("-", explicit.strip()) or "default"
    if api_endpoint:
        host = urlparse(api_endpoint).netloc or api_endpoint
        return _TARGET_SANITIZE_RE.sub("-", host) or "default"
    return "default"


def acquire_run_lock(product: str, target: str = "default") -> int | None:
    """Acquire the per-product, per-target run lock (non-blocking exclusive flock).

    Returns the held file descriptor (release it with :func:`release_run_lock`),
    or ``None`` when locking is unavailable — non-POSIX, or an unusable lock dir.

    Raises ``RunLockError`` on genuine contention (another holder).
    """
    if fcntl is None:  # pragma: no cover - non-POSIX platforms
        return None
    lock_dir = _locks_base_dir()
    # Creating the lock file is best-effort: an unusable lock dir (unwritable,
    # points at a file) must degrade to running unlocked, not crash the run.
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{product}--{target}.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        logger.warning("run_lock_unavailable", error=str(exc))
        return None
    # Only genuine flock contention (another holder) aborts the run.
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise RunLockError(product, target, lock_path) from exc
    return fd


def release_run_lock(fd: int | None) -> None:
    """Release a lock fd acquired by :func:`acquire_run_lock` (no-op for ``None``)."""
    if fd is None or fcntl is None:  # pragma: no cover - non-POSIX platforms
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
