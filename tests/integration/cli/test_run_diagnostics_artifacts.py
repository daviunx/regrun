"""CLI integration tests (TDD-RED) for persistent run artifacts + diagnostics.

Exercises the real ``regrun run`` command via ``click.testing.CliRunner`` against
real temp YAML fixtures (bash runner -- no network) and the real filesystem
(``tmp_path`` + ``REGRUN_RUNS_DIR`` override). No mocks.

Contract (analysis.md §4.3 + §4.1):

* EVERY run writes ``report.txt`` + ``report.json`` under
  ``{REGRUN_RUNS_DIR}/{product}/{YYYYMMDD-HHMMSS}/`` -- including when the run
  FAILS.
* stdout ends with a parseable pointer line::

      Full report: <abs path>/report.txt (json: report.json)

* An ``eventually``-exhausted failing test records
  ``diagnostics.attempts == max_attempts`` in the persisted ``report.json``.
"""

import json
import re
from pathlib import Path

import yaml
from click.testing import CliRunner

from regrun.cli import cli

PRODUCT = "diag"
POINTER_RE = re.compile(r"Full report: (?P<txt>.+/report\.txt) \(json: report\.json\)")
TS_DIR_RE = re.compile(r"^\d{8}-\d{6}")


def _write(directory: Path, filename: str, doc: dict) -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def _failing_suite(directory: Path) -> None:
    """A one-test bash suite whose assertion always fails (echo != expected)."""
    doc = {
        "meta": {"product": PRODUCT, "layer": "setup", "runner": "bash"},
        "groups": [
            {
                "id": 1,
                "name": "Diagnostics",
                "priority": "high",
                "tests": [
                    {
                        "id": "D.1",
                        "name": "always fails",
                        "commands": [{"cmd": "echo nope"}],
                        "assert": {"contains": "yes"},
                    }
                ],
            }
        ],
    }
    _write(directory, "00_setup.yaml", doc)


def _eventually_suite(directory: Path) -> None:
    """A bash suite whose assertion never passes, wrapped in an eventually block
    with max_attempts=3 and zero delay (fast, no network)."""
    doc = {
        "meta": {"product": PRODUCT, "layer": "setup", "runner": "bash"},
        "groups": [
            {
                "id": 1,
                "name": "Eventually Exhaust",
                "priority": "high",
                "tests": [
                    {
                        "id": "E.1",
                        "name": "never consistent",
                        "commands": [{"cmd": "echo nope"}],
                        "assert": {"contains": "yes"},
                        "eventually": {
                            "max_attempts": 3,
                            "interval": 0,
                            "backoff": 1,
                            "initial_delay": 0,
                        },
                    }
                ],
            }
        ],
    }
    _write(directory, "00_setup.yaml", doc)


def _artifact_dir(runs_dir: Path) -> Path:
    """Return the single timestamped run dir under {runs_dir}/{product}/."""
    product_dir = runs_dir / PRODUCT
    candidates = [p for p in product_dir.iterdir() if p.is_dir() and TS_DIR_RE.match(p.name)]
    assert len(candidates) == 1, f"expected one run dir, found {candidates}"
    return candidates[0]


def test_run_writes_report_artifacts_even_on_failure(tmp_path, monkeypatch) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    _failing_suite(suite)

    runs_dir = tmp_path / "runs"
    monkeypatch.setenv("REGRUN_RUNS_DIR", str(runs_dir))

    result = CliRunner().invoke(cli, ["run", str(suite)])

    # The run failed (assertion did not hold) ...
    assert result.exit_code == 1, result.output

    # ... yet both artifacts exist under {runs}/{product}/{timestamp}/.
    run_dir = _artifact_dir(runs_dir)
    assert (run_dir / "report.txt").is_file()
    assert (run_dir / "report.json").is_file()


def test_stdout_ends_with_pointer_line(tmp_path, monkeypatch) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    _failing_suite(suite)

    runs_dir = tmp_path / "runs"
    monkeypatch.setenv("REGRUN_RUNS_DIR", str(runs_dir))

    result = CliRunner().invoke(cli, ["run", str(suite)])

    last_line = result.output.strip().splitlines()[-1]
    m = POINTER_RE.search(last_line)
    assert m is not None, f"no pointer line at stdout tail: {last_line!r}"
    # The path it advertises really exists.
    assert Path(m.group("txt")).is_file()


def test_artifact_write_failure_does_not_suppress_results(tmp_path, monkeypatch) -> None:
    """An unwritable REGRUN_RUNS_DIR must NOT swallow the run: full results +
    Failures section still print, the exit code is the run's own, and the write
    failure is surfaced as a stderr warning (not a traceback)."""
    suite = tmp_path / "suite"
    suite.mkdir()
    _failing_suite(suite)

    # Point REGRUN_RUNS_DIR at a FILE -- mkdir(parents=True) under it raises
    # OSError (the parent is not a directory).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("REGRUN_RUNS_DIR", str(blocker))

    result = CliRunner().invoke(cli, ["run", str(suite)])

    # The run's own outcome is preserved (failing suite -> exit 1), NOT masked
    # by the artifact-write failure.
    assert result.exit_code == 1, result.output

    # Full results + the Failures section still reached stdout.
    assert "D.1" in result.output
    assert "Failures" in result.output
    assert "Result: FAIL" in result.output

    # The write failure is a stderr warning, and no pointer line was printed.
    assert "could not persist run artifacts" in result.stderr
    assert "Full report:" not in result.output


def test_eventually_exhaustion_records_max_attempts(tmp_path, monkeypatch) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    _eventually_suite(suite)

    runs_dir = tmp_path / "runs"
    monkeypatch.setenv("REGRUN_RUNS_DIR", str(runs_dir))

    result = CliRunner().invoke(cli, ["run", str(suite)])
    assert result.exit_code == 1, result.output

    run_dir = _artifact_dir(runs_dir)
    report = json.loads((run_dir / "report.json").read_text())

    by_id = {tr["test_id"]: tr for tr in report["test_results"]}
    assert "E.1" in by_id
    diag = by_id["E.1"]["diagnostics"]
    assert diag is not None
    assert diag["attempts"] == 3
