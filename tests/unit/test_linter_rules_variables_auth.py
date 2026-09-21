"""Unit tests for W012 over a file's ``auth:`` profiles.

A profile field is a use like any other: ``token: "{{ USER_JWT }}"`` consumes a
value, and when a sibling non-setup file captures it the file is coupled to that
sibling. Scanning only the groups left every profile field a blind spot, so such
a file linted clean, passed a full ordered run, and failed on an unresolved
variable as soon as it ran alone or in another shard.

The clearing conditions are the group scan's: produced locally, producer in the
setup layer, or declared in ``meta.requires``. An UNUSED profile is flagged too:
profiles resolve lazily, only for the test that names one, so an unused profile
never fails at run time while still pointing at a fixture the file does not own.
"""

from pathlib import Path

import yaml

from regrun.engine.linter import lint_directory


def _write(directory: Path, name: str, doc: dict) -> None:
    (directory / name).write_text(yaml.safe_dump(doc, sort_keys=False))


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


def _of_rule(findings, rule: str) -> list:
    return [f for f in findings if f.rule == rule]


def _login_group() -> dict:
    """A group whose test CAPTURES ``USER_JWT``."""
    return {
        "id": 1,
        "name": "Login",
        "tests": [
            {
                "id": "P.1",
                "name": "log in",
                "method": "POST",
                "path": "/auth/login",
                "org_header": False,
                "body": {"email": "someone@example.com", "password": "secret"},
                "assert": {"status": 200},
                "capture": {"USER_JWT": "$.access_token"},
            }
        ],
    }


def _producer(tmp_path: Path, layer: str = "api") -> None:
    _write(
        tmp_path,
        "01_login.yaml",
        {
            "meta": {"product": "myapp", "layer": layer, "runner": "httpx"},
            "groups": [_login_group()],
        },
    )


def _reader_group() -> dict:
    """A group whose test selects the ``user`` profile and captures nothing."""
    return {
        "id": 5,
        "name": "Reader",
        "tests": [
            {
                "id": "C.1",
                "name": "list things",
                "method": "GET",
                "path": "/things",
                "auth": "user",
                "assert": {"status": 200},
            }
        ],
    }


def _consumer(
    tmp_path: Path,
    token: str = "{{ USER_JWT }}",
    groups: list[dict] | None = None,
    **meta: object,
) -> None:
    """A file whose ``user`` profile reads ``token``, declaring what ``meta`` says."""
    doc: dict = {
        "meta": {"product": "myapp", "layer": "api", "runner": "httpx"},
        "auth": {"user": {"type": "bearer", "token": token}},
        "groups": groups if groups is not None else [_reader_group()],
    }
    doc["meta"].update(meta)
    _write(tmp_path, "02_things.yaml", doc)


# ------------------------------------------------------------------------------ flagged


def test_w012_flags_an_auth_token_captured_by_a_sibling(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path)
    assert "W012" in _rules(lint_directory(tmp_path))


def test_w012_message_names_the_profile_and_the_producing_file(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path)
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.file == "02_things.yaml"
    assert finding.severity == "warn"
    assert "user" in finding.message
    assert "USER_JWT" in finding.message
    assert "01_login.yaml" in finding.message


def test_w012_flags_a_filtered_reference_inside_a_profile(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, token="{{ USER_JWT | trim }}")
    assert "W012" in _rules(lint_directory(tmp_path))


def test_w012_flags_a_dotted_reference_inside_a_profile(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, token="{{ USER_JWT.value }}")
    assert "W012" in _rules(lint_directory(tmp_path))


def test_w012_flags_an_unspaced_reference_inside_a_profile(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, token="{{USER_JWT}}")
    assert "W012" in _rules(lint_directory(tmp_path))


def test_w012_flags_a_profile_no_test_selects(tmp_path: Path) -> None:
    """Dead residue: lazily resolved, never run, still coupled to a sibling."""
    _producer(tmp_path)
    _consumer(
        tmp_path,
        groups=[
            {
                "id": 5,
                "name": "Reader",
                "tests": [
                    {
                        "id": "C.1",
                        "name": "list things unauthenticated",
                        "method": "GET",
                        "path": "/things",
                        "auth": "none",
                        "assert": {"status": 401},
                    }
                ],
            }
        ],
    )
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert len(findings) == 1
    assert "user" in findings[0].message


def test_w012_names_every_profile_that_borrows_the_value(tmp_path: Path) -> None:
    """Which profile to drop is the author's call, so all of them are named."""
    _producer(tmp_path)
    _write(
        tmp_path,
        "02_things.yaml",
        {
            "meta": {"product": "myapp", "layer": "api", "runner": "httpx"},
            "auth": {
                "user": {"type": "bearer", "token": "{{ USER_JWT }}"},
                "user_key": {"type": "api_key", "token": "{{ USER_JWT }}"},
            },
            "groups": [_reader_group()],
        },
    )
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert len(findings) == 1
    message = findings[0].message
    assert "auth profiles 'user', 'user_key' use" in message
    assert "01_login.yaml" in message


def test_w012_singular_phrasing_for_one_profile(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path)
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert "auth profile 'user' uses" in findings[0].message


def test_w012_flags_a_reference_in_a_profile_org_header(tmp_path: Path) -> None:
    """Every string field of a profile is scanned, not the token alone."""
    _write(
        tmp_path,
        "01_login.yaml",
        {
            "meta": {"product": "myapp", "layer": "api", "runner": "httpx"},
            "groups": [
                {
                    "id": 1,
                    "name": "Bootstrap",
                    "tests": [
                        {
                            "id": "P.1",
                            "name": "create a workspace",
                            "method": "POST",
                            "path": "/workspaces",
                            "body": {"name": "fixture-{{RUN_ID}}"},
                            "assert": {"status": 201},
                            "capture": {"WORKSPACE_SLUG": "$.slug"},
                        }
                    ],
                }
            ],
        },
    )
    _write(
        tmp_path,
        "02_things.yaml",
        {
            "meta": {"product": "myapp", "layer": "api", "runner": "httpx"},
            "auth": {
                "user": {
                    "type": "bearer",
                    "token": "static-token",
                    "org_header": "{{ WORKSPACE_SLUG }}",
                }
            },
            "groups": [_reader_group()],
        },
    )
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert len(findings) == 1
    assert "WORKSPACE_SLUG" in findings[0].message


def test_w012_reports_one_finding_when_groups_and_a_profile_share_a_value(
    tmp_path: Path,
) -> None:
    """One coupling, one fix: the group message is the one that names the request."""
    _producer(tmp_path)
    _consumer(
        tmp_path,
        groups=[
            {
                "id": 5,
                "name": "Reader",
                "tests": [
                    {
                        "id": "C.1",
                        "name": "echo the token back",
                        "method": "POST",
                        "path": "/introspect",
                        "auth": "user",
                        "body": {"token": "{{ USER_JWT }}"},
                        "assert": {"status": 200},
                    }
                ],
            }
        ],
    )
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert len(findings) == 1
    assert "USER_JWT" in findings[0].message


# ------------------------------------------------------------------------------ cleared


def test_w012_cleared_when_the_producer_is_a_setup_file(tmp_path: Path) -> None:
    _producer(tmp_path, layer="setup")
    _consumer(tmp_path)
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_by_declaring_requires(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, requires=["01_login"])
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_when_the_file_captures_the_token_itself(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, groups=[_login_group(), _reader_group()])
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_by_a_file_level_variables_declaration(tmp_path: Path) -> None:
    _producer(tmp_path)
    _write(
        tmp_path,
        "02_things.yaml",
        {
            "meta": {"product": "myapp", "layer": "api", "runner": "httpx"},
            "variables": {"USER_JWT": "static-token"},
            "auth": {"user": {"type": "bearer", "token": "{{ USER_JWT }}"}},
            "groups": [_reader_group()],
        },
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_silent_when_a_profile_carries_no_template_reference(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, token="static-token")
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_ignores_an_env_lookup_inside_a_profile(tmp_path: Path) -> None:
    """``env`` is a template global; no suite file produces it."""
    _producer(tmp_path)
    _consumer(tmp_path, token="{{ env.get('MYAPP_STATIC_TOKEN', 'dev-token') }}")
    assert "W012" not in _rules(lint_directory(tmp_path))
