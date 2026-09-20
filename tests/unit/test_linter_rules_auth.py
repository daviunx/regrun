"""Unit tests for the auth-profile lint rules E003 and E004.

E003 is the ``auth:``-parses-as-null trap; E004 is a reference to a profile
this file never declared, which used to send the request with no credentials.
"""

from pathlib import Path

from lint_helpers import api_doc as _api_doc
from lint_helpers import rules as _rules
from lint_helpers import write as _write
from regrun.engine.linter import lint_directory, lint_exit_code


# --------------------------------------------------------------------------- E003


def test_e003_null_auth_flagged(tmp_path: Path) -> None:
    # auth: with an explicit null value (the `auth: none` trap).
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1\n        name: t\n        auth:\n        assert:\n          status: 200\n"
    )
    findings = lint_directory(tmp_path)
    assert "E003" in _rules(findings)


def test_e003_clean_string_none(tmp_path: Path) -> None:
    (tmp_path / "01_api.yaml").write_text(
        "meta:\n  product: demo\n  layer: api\n  runner: httpx\n"
        "groups:\n  - id: 5\n    name: A\n    tests:\n"
        "      - id: A.1\n        name: t\n        auth: none\n        assert:\n          status: 200\n"
    )
    assert "E003" not in _rules(lint_directory(tmp_path))


# --------------------------------------------------------------------------- E004


def test_e004_undefined_test_auth_flagged(tmp_path: Path) -> None:
    doc = _api_doc(
        [
            {
                "id": 5,
                "name": "A",
                "tests": [
                    {
                        "id": "A.1",
                        "name": "t",
                        "auth": "ghost",
                        "method": "GET",
                        "path": "/x",
                        "assert": {"status": 200},
                    }
                ],
            }
        ]
    )
    _write(tmp_path, "01_api.yaml", doc)
    findings = lint_directory(tmp_path)
    e004 = [f for f in findings if f.rule == "E004"]
    assert len(e004) == 1
    assert "'ghost'" in e004[0].message
    assert lint_exit_code(findings) == 1


def test_e004_undefined_default_auth_flagged(tmp_path: Path) -> None:
    doc = _api_doc(
        [
            {
                "id": 5,
                "name": "A",
                "tests": [
                    {
                        "id": "A.1",
                        "name": "t",
                        "method": "GET",
                        "path": "/x",
                        "assert": {"status": 200},
                    }
                ],
            }
        ]
    )
    doc["meta"]["default_auth"] = "ghost"
    _write(tmp_path, "01_api.yaml", doc)
    findings = lint_directory(tmp_path)
    assert any(f.rule == "E004" and "meta.default_auth" in f.message for f in findings)


def test_e004_clean_when_profile_defined(tmp_path: Path) -> None:
    doc = _api_doc(
        [
            {
                "id": 5,
                "name": "A",
                "tests": [
                    {
                        "id": "A.1",
                        "name": "t",
                        "auth": "prod",
                        "method": "GET",
                        "path": "/x",
                        "assert": {"status": 200},
                    }
                ],
            }
        ]
    )
    doc["auth"] = {"prod": {"type": "bearer", "token": "{{PROD_JWT}}"}}
    _write(tmp_path, "01_api.yaml", doc)
    assert "E004" not in _rules(lint_directory(tmp_path))


def test_e004_exempt_none_and_bash_runner(tmp_path: Path) -> None:
    # 'none' literal and non-auth-consuming runners never fire E004 — a bash
    # test under a dangling default_auth ignores auth config entirely.
    doc = _api_doc(
        [
            {
                "id": 5,
                "name": "A",
                "tests": [
                    {
                        "id": "A.1",
                        "name": "t",
                        "auth": "none",
                        "method": "GET",
                        "path": "/x",
                        "assert": {"status": 200},
                    },
                    {
                        "id": "A.2",
                        "name": "t2",
                        "runner": "bash",
                        "commands": [{"cmd": "echo ok"}],
                        "assert": {"last_exit_code": 0},
                    },
                ],
            }
        ]
    )
    doc["meta"]["default_auth"] = "ghost-but-bash-ignores"
    # A.1 overrides with 'none'; A.2 is bash. Only an httpx test WITHOUT an
    # override would fire — there is none in this file.
    _write(tmp_path, "01_api.yaml", doc)
    assert "E004" not in _rules(lint_directory(tmp_path))
