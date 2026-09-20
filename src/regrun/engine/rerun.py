"""Reading a previous run's report to decide what is worth running again.

A red run should cost one file's re-run, not the whole suite. The persisted
``report.json`` already says which files went wrong, so nothing needs to be
remembered between invocations.
"""

import json
from pathlib import Path

__all__ = ["failed_stems"]


def failed_stems(report_path: Path) -> set[str]:
    """The file stems worth re-running, read from a persisted ``report.json``.

    A stem qualifies when it holds a test that FAILED, ERRORED, or was BLOCKED.
    Blocked stems are included because they never actually ran: leaving them out
    would hide every consumer of the file that broke.

    A report that cannot be parsed yields no stems rather than an exception: the
    remedy for a corrupt report is an ordinary full run, not a crash.
    """
    try:
        payload = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()

    results = payload.get("test_results")
    if not isinstance(results, list):
        return set()

    stems: set[str] = set()
    for row in results:
        if not isinstance(row, dict):
            continue
        stem = row.get("file_stem")
        if not stem:
            continue
        blocked = bool(row.get("blocked_by"))
        errored = bool(row.get("error"))
        failed = not row.get("passed", False) and not row.get("skipped", False)
        if blocked or errored or failed:
            stems.add(stem)
    return stems
