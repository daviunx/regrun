"""Unit tests for the variable-isolation lint rules W012 + E005 (0.10.0).

W012 (foreign capture): a file uses a value another suite file produced without
declaring the dependency. Cleared by declaring ``requires:``, by producing the
value locally (test ``capture:`` / bash command ``capture:`` / file-level
``variables:``), or by the producer being a ``layer: setup`` file (the bootstrap
contract every file already depends on).

E005 (bad ``requires:``): an unknown stem, the file itself, a cycle, or a required
file that sorts AFTER its dependent.

Fixtures are real YAML files linted through ``lint_directory`` — same shape as
``test_linter_rules.py``, in a separate file because that one is already at the
test-file size limit.
"""

from pathlib import Path

import yaml

from regrun.engine.linter import lint_directory, lint_exit_code


def _write(directory: Path, name: str, doc: dict) -> None:
    (directory / name).write_text(yaml.safe_dump(doc, sort_keys=False))


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


def _of_rule(findings, rule: str) -> list:
    return [f for f in findings if f.rule == rule]


def _api_doc(groups: list[dict], **meta: object) -> dict:
    doc: dict = {
        "meta": {"product": "demo", "layer": "api", "runner": "httpx"},
        "groups": groups,
    }
    doc["meta"].update(meta)
    return doc


def _setup_doc(groups: list[dict], **meta: object) -> dict:
    doc: dict = {
        "meta": {"product": "demo", "layer": "setup", "runner": "httpx"},
        "groups": groups,
    }
    doc["meta"].update(meta)
    return doc


def _producer_group() -> dict:
    """A group whose test CAPTURES ``THING_ID``."""
    return {
        "id": 1,
        "name": "Producer",
        "tests": [
            {
                "id": "P.1",
                "name": "read a thing",
                "method": "GET",
                "path": "/things",
                "assert": {"status": 200},
                "capture": {"THING_ID": "$.items[0].id"},
            }
        ],
    }


def _consumer_group() -> dict:
    """A group whose test USES ``THING_ID`` and captures nothing."""
    return {
        "id": 5,
        "name": "Consumer",
        "tests": [
            {
                "id": "C.1",
                "name": "read the captured thing",
                "method": "GET",
                "path": "/things/{{THING_ID}}",
                "assert": {"status": 200},
            }
        ],
    }


def _write_pair(tmp_path: Path, **consumer_meta: object) -> None:
    _write(tmp_path, "01_producer.yaml", _api_doc([_producer_group()]))
    _write(tmp_path, "02_consumer.yaml", _api_doc([_consumer_group()], **consumer_meta))


# ------------------------------------------------------------------------ W012 positive


def test_w012_flags_an_undeclared_foreign_capture(tmp_path: Path) -> None:
    _write_pair(tmp_path)
    assert "W012" in _rules(lint_directory(tmp_path))


def test_w012_message_names_file_variable_and_producer(tmp_path: Path) -> None:
    _write_pair(tmp_path)
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.file == "02_consumer.yaml"
    assert "THING_ID" in finding.message
    assert "01_producer.yaml" in finding.message


def test_w012_is_a_warning_not_an_error(tmp_path: Path) -> None:
    """Non-breaking rollout: W012 must not red a non-strict lint run."""
    _write_pair(tmp_path)
    findings = _of_rule(lint_directory(tmp_path), "W012")
    assert findings[0].severity == "warn"


# ------------------------------------------------------------------------- W012 cleared


def test_w012_cleared_by_declaring_requires(tmp_path: Path) -> None:
    _write_pair(tmp_path, requires=["01_producer"])
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_by_a_transitive_requires_closure(tmp_path: Path) -> None:
    _write(tmp_path, "01_producer.yaml", _api_doc([_producer_group()]))
    _write(
        tmp_path,
        "02_middle.yaml",
        _api_doc(
            [
                {
                    "id": 6,
                    "name": "Middle",
                    "tests": [
                        {
                            "id": "M.1",
                            "name": "noop",
                            "method": "GET",
                            "path": "/health",
                            "assert": {"status": 200},
                        }
                    ],
                }
            ],
            requires=["01_producer"],
        ),
    )
    _write(tmp_path, "03_consumer.yaml", _api_doc([_consumer_group()], requires=["02_middle"]))
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_when_the_producer_is_a_setup_file(tmp_path: Path) -> None:
    """The setup layer is an implicit dependency of every file — never declared."""
    _write(tmp_path, "00_setup.yaml", _setup_doc([_producer_group()]))
    _write(tmp_path, "02_consumer.yaml", _api_doc([_consumer_group()]))
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_by_a_local_test_capture(tmp_path: Path) -> None:
    _write(tmp_path, "01_producer.yaml", _api_doc([_producer_group()]))
    _write(
        tmp_path,
        "02_consumer.yaml",
        _api_doc([_producer_group(), _consumer_group()]),
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_by_a_local_bash_command_capture(tmp_path: Path) -> None:
    _write(tmp_path, "01_producer.yaml", _api_doc([_producer_group()]))
    _write(
        tmp_path,
        "02_consumer.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "groups": [
                {
                    "id": 5,
                    "name": "Consumer",
                    "tests": [
                        {
                            "id": "C.0",
                            "name": "mint the id locally",
                            "commands": [{"cmd": "echo 7", "capture": {"THING_ID": "stdout"}}],
                            "assert": {"last_exit_code": 0},
                        },
                        {
                            "id": "C.1",
                            "name": "use it",
                            "commands": [{"cmd": "echo {{THING_ID}}"}],
                            "assert": {"last_exit_code": 0},
                        },
                    ],
                }
            ],
        },
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_cleared_by_a_file_level_variables_declaration(tmp_path: Path) -> None:
    _write(tmp_path, "01_producer.yaml", _api_doc([_producer_group()]))
    consumer = _api_doc([_consumer_group()])
    consumer["variables"] = {"THING_ID": "fixed-{{RUN_ID}}"}
    _write(tmp_path, "02_consumer.yaml", consumer)
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_ignores_engine_builtins(tmp_path: Path) -> None:
    """``RUN_ID`` / ``timestamp`` / ``uuid`` are engine builtins, produced by no file."""
    _write(
        tmp_path,
        "01_only.yaml",
        _api_doc(
            [
                {
                    "id": 5,
                    "name": "Builtins",
                    "tests": [
                        {
                            "id": "B.1",
                            "name": "create",
                            "method": "POST",
                            "path": "/things",
                            "body": {"name": "regr-thing-{{RUN_ID}}-{{timestamp}}-{{uuid}}"},
                            "assert": {"status": 201},
                        }
                    ],
                }
            ]
        ),
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_ignores_env_derived_values(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "01_only.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "groups": [
                {
                    "id": 5,
                    "name": "Env",
                    "tests": [
                        {
                            "id": "E.1",
                            "name": "probe the endpoint",
                            "commands": [
                                {
                                    "cmd": (
                                        "curl -sf "
                                        "{{ env.get('REGRUN_API_ENDPOINT', "
                                        "'http://demo.localhost') }}/health"
                                    )
                                }
                            ],
                            "assert": {"last_exit_code": 0},
                        }
                    ],
                }
            ],
        },
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_w012_silent_on_a_single_self_contained_file(tmp_path: Path) -> None:
    _write(tmp_path, "01_only.yaml", _api_doc([_producer_group(), _consumer_group()]))
    assert "W012" not in _rules(lint_directory(tmp_path))


# ------------------------------------------------------------------------------- E005


def test_e005_flags_an_unknown_required_stem(tmp_path: Path) -> None:
    _write(tmp_path, "01_api.yaml", _api_doc([_producer_group()], requires=["99_missing"]))
    findings = lint_directory(tmp_path)
    assert "E005" in _rules(findings)
    assert "99_missing" in _of_rule(findings, "E005")[0].message
    assert lint_exit_code(findings) == 1


def test_e005_flags_a_self_reference(tmp_path: Path) -> None:
    _write(tmp_path, "01_api.yaml", _api_doc([_producer_group()], requires=["01_api"]))
    findings = lint_directory(tmp_path)
    assert "E005" in _rules(findings)
    assert lint_exit_code(findings) == 1


def test_e005_flags_a_two_file_cycle(tmp_path: Path) -> None:
    _write(tmp_path, "01_a.yaml", _api_doc([_producer_group()], requires=["02_b"]))
    _write(tmp_path, "02_b.yaml", _api_doc([_consumer_group()], requires=["01_a"]))
    findings = _of_rule(lint_directory(tmp_path), "E005")
    assert findings
    assert any("cycle" in f.message.lower() for f in findings)


def test_e005_flags_a_required_file_that_sorts_later(tmp_path: Path) -> None:
    _write(tmp_path, "01_early.yaml", _api_doc([_consumer_group()], requires=["02_late"]))
    _write(tmp_path, "02_late.yaml", _api_doc([_producer_group()]))
    findings = _of_rule(lint_directory(tmp_path), "E005")
    assert findings
    assert any("02_late" in f.message for f in findings)


def test_e005_silent_on_a_valid_requires_chain(tmp_path: Path) -> None:
    _write(tmp_path, "01_producer.yaml", _api_doc([_producer_group()]))
    _write(tmp_path, "02_consumer.yaml", _api_doc([_consumer_group()], requires=["01_producer"]))
    assert "E005" not in _rules(lint_directory(tmp_path))


def test_e005_silent_when_no_file_declares_requires(tmp_path: Path) -> None:
    _write_pair(tmp_path)
    assert "E005" not in _rules(lint_directory(tmp_path))
