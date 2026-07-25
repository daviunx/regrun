"""Unit tests for strict variable resolution (0.9.0).

Strict mode (default) raises :class:`UnresolvedVariableError` on an undefined
``{{VAR}}`` instead of the old warn-and-return-literal behaviour — a literal
``regr-x-{{RUN_ID}}`` fixture name is byte-identical every run and collides.
"""

import pytest

from regrun.engine.variables import UnresolvedVariableError, VariableStore, render_test
from regrun.models import Test


def test_strict_default_on() -> None:
    assert VariableStore().strict is True


def test_strict_raises_naming_the_variable() -> None:
    store = VariableStore()
    with pytest.raises(UnresolvedVariableError) as exc:
        store.render_string("regr-x-{{NEVER_SET}}")
    assert "NEVER_SET" in str(exc.value)
    assert exc.value.template == "regr-x-{{NEVER_SET}}"


def test_non_strict_returns_raw_template() -> None:
    store = VariableStore(strict=False)
    assert store.render_string("regr-x-{{NEVER_SET}}") == "regr-x-{{NEVER_SET}}"


def test_strict_resolves_known_variables() -> None:
    store = VariableStore()
    store.set("CO_ID", "42")
    assert store.render_string("id={{CO_ID}}") == "id=42"


def test_strict_toggle_is_mutable() -> None:
    # The executor flips strictness per file (meta.strict_vars).
    store = VariableStore()
    store.strict = False
    assert store.render_string("{{NOPE}}") == "{{NOPE}}"
    store.strict = True
    with pytest.raises(UnresolvedVariableError):
        store.render_string("{{NOPE}}")


def test_render_test_strict_fails_on_unresolved_body_var() -> None:
    store = VariableStore()
    test = Test.model_validate(
        {
            "id": "A.1",
            "name": "t",
            "method": "POST",
            "path": "/companies",
            "body": {"slug": "regr-co-{{MISSING}}"},
            "assert": {"status": 201},
        }
    )
    with pytest.raises(UnresolvedVariableError, match="MISSING"):
        render_test(test, store)


def test_render_test_leaves_commands_unrendered() -> None:
    # Bash commands are rendered by the bash runner at execution time so
    # per-command captures resolve mid-test; render_test must not touch them
    # (strict mode would otherwise fail on a capture that hasn't happened yet).
    store = VariableStore()
    test = Test.model_validate(
        {
            "id": "B.1",
            "name": "t",
            "commands": [
                {"cmd": "echo 42", "capture": {"MID_TEST": "stdout"}},
                {"cmd": "echo {{MID_TEST}}"},
            ],
            "assert": {"last_exit_code": 0},
        }
    )
    rendered = render_test(test, store)
    assert rendered.commands is not None
    assert rendered.commands[1].cmd == "echo {{MID_TEST}}"
