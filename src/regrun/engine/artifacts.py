"""Persistent run artifacts: every run's full report written to disk.

An AI agent (or a human with a tail-clipped terminal) must never have to re-run
a suite to see why it failed. Every run -- pass, fail, or fail-fast abort --
persists the complete text + JSON report to a timestamped directory, and the CLI
prints a parseable pointer line so the file can be read instead of re-run.

Location: ``{REGRUN_RUNS_DIR or ~/.regrun/runs}/{product}/{target}/{YYYYMMDD-HHMMSS}/``
with ``report.txt`` + ``report.json``. ``target`` is the run's lock-target slug
(see ``run_lock.derive_lock_target``) so two isolates of the same product never
interleave reports in one folder. Timestamped dirs, no auto-pruning (plain
text, negligible size).
"""

import os
from datetime import datetime
from pathlib import Path

from regrun.engine.reporter import RunResult

REPORT_TXT = "report.txt"
REPORT_JSON = "report.json"
REPORT_JUNIT = "junit.xml"


def _runs_base_dir() -> Path:
    """Resolve the artifacts base dir, honouring ``REGRUN_RUNS_DIR`` at call time."""
    env = os.getenv("REGRUN_RUNS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".regrun" / "runs"


def write_run_artifacts(
    run_result: RunResult,
    text_report: str,
    json_report: str,
    junit_report: str = "",
) -> Path:
    """Write ``report.txt`` + ``report.json`` + ``junit.xml`` for a run; return the run directory.

    The directory is ``{base}/{product}/{target}/{timestamp}`` and is created
    if needed.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = _runs_base_dir() / run_result.product / run_result.target / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / REPORT_TXT).write_text(text_report)
    (run_dir / REPORT_JSON).write_text(json_report)
    if junit_report:
        (run_dir / REPORT_JUNIT).write_text(junit_report)

    return run_dir


def latest_report(product: str, target: str) -> Path | None:
    """The newest ``report.json`` for this product AND target, or None if there is none.

    Scoped to the target on purpose: two isolates of one product must never read
    each other's failures. Run directories are timestamped ``YYYYMMDD-HHMMSS``,
    which sorts chronologically as a string, so the last name is the latest run.
    """
    target_dir = _runs_base_dir() / product / target
    if not target_dir.is_dir():
        return None
    for run_dir in sorted((d for d in target_dir.iterdir() if d.is_dir()), reverse=True):
        report = run_dir / REPORT_JSON
        if report.is_file():
            return report
    return None


def pointer_line(run_dir: Path) -> str:
    """The stdout tail line an agent parses to locate the full report."""
    return f"Full report: {run_dir / REPORT_TXT} (json: {REPORT_JSON}, junit: {REPORT_JUNIT})"
