"""Unit tests for the template forms W012 must recognise as a variable reference.

A reference is a reference whatever follows the name: a Jinja filter, a dotted
attribute on a captured object, or no whitespace at all. Reading only the exact
``{{ VAR }}`` spelling made every other form a silent false negative, which is
worse than a false positive here: the whole point of W012 is to surface coupling
nobody wrote down.

What must NOT become a reference: engine builtins (no file produces them) and
``env.get(...)``, which is a template global, not a captured value.
"""

from pathlib import Path

import yaml

from regrun.engine.linter import lint_directory


def _write(directory: Path, name: str, doc: dict) -> None:
    (directory / name).write_text(yaml.safe_dump(doc, sort_keys=False))


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


def _producer(tmp_path: Path) -> None:
    """A file that captures ``THING_ID`` and declares no dependency."""
    _write(
        tmp_path,
        "01_producer.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "httpx"},
            "groups": [
                {
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
            ],
        },
    )


def _consumer(tmp_path: Path, reference: str) -> None:
    """A file that uses ``reference`` in a path and captures nothing."""
    _write(
        tmp_path,
        "02_consumer.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "httpx"},
            "groups": [
                {
                    "id": 5,
                    "name": "Consumer",
                    "tests": [
                        {
                            "id": "C.1",
                            "name": "use the value",
                            "method": "GET",
                            "path": f"/things/{reference}",
                            "assert": {"status": 200},
                        }
                    ],
                }
            ],
        },
    )


def _w012_for(tmp_path: Path, reference: str) -> set[str]:
    _producer(tmp_path)
    _consumer(tmp_path, reference)
    return _rules(lint_directory(tmp_path))


# ------------------------------------------------------------------- forms that count


def test_the_bare_form_is_flagged(tmp_path: Path) -> None:
    assert "W012" in _w012_for(tmp_path, "{{ THING_ID }}")


def test_the_unspaced_form_is_flagged(tmp_path: Path) -> None:
    assert "W012" in _w012_for(tmp_path, "{{THING_ID}}")


def test_a_filtered_reference_is_flagged(tmp_path: Path) -> None:
    assert "W012" in _w012_for(tmp_path, "{{ THING_ID | default('x') }}")


def test_a_filter_chain_is_flagged(tmp_path: Path) -> None:
    assert "W012" in _w012_for(tmp_path, "{{ THING_ID | trim | lower }}")


def test_a_dotted_attribute_reference_is_flagged(tmp_path: Path) -> None:
    assert "W012" in _w012_for(tmp_path, "{{ THING_ID.field }}")


def test_generous_whitespace_is_flagged(tmp_path: Path) -> None:
    assert "W012" in _w012_for(tmp_path, "{{    THING_ID    }}")


# --------------------------------------------------------------- forms that must not


def test_a_filtered_reference_is_cleared_by_declaring_requires(tmp_path: Path) -> None:
    """The wider matching must still be clearable the normal way."""
    _producer(tmp_path)
    _write(
        tmp_path,
        "02_consumer.yaml",
        {
            "meta": {
                "product": "demo",
                "layer": "api",
                "runner": "httpx",
                "requires": ["01_producer"],
            },
            "groups": [
                {
                    "id": 5,
                    "name": "Consumer",
                    "tests": [
                        {
                            "id": "C.1",
                            "name": "use the value",
                            "method": "GET",
                            "path": "/things/{{ THING_ID | trim }}",
                            "assert": {"status": 200},
                        }
                    ],
                }
            ],
        },
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_env_get_is_not_a_variable_reference(tmp_path: Path) -> None:
    """``env`` is a template global; no suite file produces it."""
    _write(
        tmp_path,
        "01_env.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "groups": [
                {
                    "id": 5,
                    "name": "Env",
                    "tests": [
                        {
                            "id": "E.1",
                            "name": "probe",
                            "commands": [
                                {
                                    "cmd": (
                                        "curl -sf {{ env.get('REGRUN_API_ENDPOINT', "
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
    _write(
        tmp_path,
        "02_env_too.yaml",
        {
            "meta": {"product": "demo", "layer": "api", "runner": "bash"},
            "groups": [
                {
                    "id": 6,
                    "name": "Env",
                    "tests": [
                        {
                            "id": "E.2",
                            "name": "probe again",
                            "commands": [{"cmd": "curl -sf {{ env.get('X', 'y') }}/health"}],
                            "assert": {"last_exit_code": 0},
                        }
                    ],
                }
            ],
        },
    )
    assert "W012" not in _rules(lint_directory(tmp_path))


def test_a_filtered_builtin_is_not_flagged(tmp_path: Path) -> None:
    _producer(tmp_path)
    _consumer(tmp_path, "{{ RUN_ID | trim }}")
    assert "W012" not in _rules(lint_directory(tmp_path))
