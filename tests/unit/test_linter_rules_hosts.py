"""Unit tests for the stack-targeting lint rule W008 (hardcoded host or database)."""

from pathlib import Path

from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory


# --------------------------------------------------------------------------- W008


def _bash_doc(cmd: str) -> dict:
    return {
        "meta": {"product": "demo", "layer": "setup", "runner": "bash"},
        "groups": [
            {
                "id": 1,
                "name": "B",
                "tests": [
                    {
                        "id": "B.1",
                        "name": "t",
                        "commands": [{"cmd": cmd}],
                        "assert": {"last_exit_code": 0},
                    }
                ],
            }
        ],
    }


def test_w008_hardcoded_url_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "00_setup.yaml", _bash_doc("curl -s http://demo.localhost/api/v1/health"))
    assert "W008" in _rules(lint_directory(tmp_path))


def test_w008_clean_env_get_wrapped_url(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "00_setup.yaml",
        _bash_doc(
            "curl -s \"{{ env.get('REGRUN_API_ENDPOINT', 'http://demo.localhost') }}/health\""
        ),
    )
    assert "W008" not in _rules(lint_directory(tmp_path))


def test_w008_clean_shell_var_host(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "00_setup.yaml",
        _bash_doc('curl -s "http://${API_HOST:-demo.localhost}/health"'),
    )
    assert "W008" not in _rules(lint_directory(tmp_path))


def test_w008_hardcoded_psql_db_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "00_setup.yaml",
        _bash_doc("docker exec -i pg psql -U user -d demo_prod -t -A -c 'SELECT 1;'"),
    )
    assert "W008" in _rules(lint_directory(tmp_path))


def test_w008_clean_parameterized_psql_db(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "00_setup.yaml",
        _bash_doc("docker exec -i pg psql -U user -d \"${DEMO_DB:-demo_db}\" -c 'SELECT 1;'"),
    )
    assert "W008" not in _rules(lint_directory(tmp_path))


def test_w008_fires_inside_sweep_steps(tmp_path: Path) -> None:
    doc = _bash_doc("true")
    doc["sweep"] = [
        {
            "name": "sweep-http",
            "runner": "bash",
            "commands": [{"cmd": "curl -s http://demo.localhost/sweep"}],
            "assert": {"last_exit_code": 0},
        }
    ]
    _write(tmp_path, "00_setup.yaml", doc)
    assert "W008" in _rules(lint_directory(tmp_path))
