"""Persistent run artifacts: every run's full report written to disk.

An AI agent (or a human with a tail-clipped terminal) must never have to re-run
a suite to see why it failed. Every run -- pass, fail, or fail-fast abort --
persists the complete text + JSON report to a timestamped directory, and the CLI
prints a parseable pointer line so the file can be read instead of re-run.

Location: ``{REGRUN_RUNS_DIR or ~/.regrun/runs}/{product}/{YYYYMMDD-HHMMSS}/``
with ``report.txt`` + ``report.json``. Timestamped dirs, no auto-pruning
(plain text, negligible size).
"""

import os
from datetime import datetime
from pathlib import Path

from regrun.engine.reporter import RunResult

REPORT_TXT = "report.txt"
REPORT_JSON = "report.json"


def _runs_base_dir() -> Path:
    """Resolve the artifacts base dir, honouring ``REGRUN_RUNS_DIR`` at call time."""
    env = os.getenv("REGRUN_RUNS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".regrun" / "runs"


def write_run_artifacts(run_result: RunResult, text_report: str, json_report: str) -> Path:
    """Write ``report.txt`` + ``report.json`` for a run; return the run directory.

    The directory is ``{base}/{product}/{timestamp}`` and is created if needed.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = _runs_base_dir() / run_result.product / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / REPORT_TXT).write_text(text_report)
    (run_dir / REPORT_JSON).write_text(json_report)

    return run_dir


def pointer_line(run_dir: Path) -> str:
    """The stdout tail line an agent parses to locate the full report."""
    return f"Full report: {run_dir / REPORT_TXT} (json: {REPORT_JSON})"
