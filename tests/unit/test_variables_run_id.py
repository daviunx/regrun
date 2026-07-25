"""Unit tests for the engine-owned ``RUN_ID`` builtin (0.9.0).

``RUN_ID`` is generated once per :class:`VariableStore` (== once per run) in
the same ``{int(time)}{hex4}`` format ``{{timestamp}}`` produces, so suites no
longer need to hand-declare ``RUN_ID: "{{timestamp}}"``. BACKCOMPAT: a suite
that declares or captures ``RUN_ID`` shadows the builtin — the suite's value
wins and nothing changes.
"""

import re

from regrun.engine.variables import VariableStore

_RUN_ID_FORMAT = re.compile(r"^\d{10,}[0-9a-f]{4}$")


def test_run_id_builtin_resolves_without_declaration() -> None:
    store = VariableStore()
    rendered = store.render_string("regr-co-{{RUN_ID}}")
    assert "{{" not in rendered
    assert rendered.startswith("regr-co-")


def test_run_id_builtin_matches_timestamp_format() -> None:
    store = VariableStore()
    assert _RUN_ID_FORMAT.match(store.effective_run_id)


def test_run_id_stable_across_renders() -> None:
    # Unlike {{timestamp}} (recomputed per render), RUN_ID is one value per run.
    store = VariableStore()
    first = store.render_string("{{RUN_ID}}")
    second = store.render_string("{{RUN_ID}}")
    assert first == second == store.effective_run_id


def test_suite_declared_run_id_wins() -> None:
    # A suite that sets RUN_ID (declared in variables: or captured) shadows the
    # engine builtin everywhere, including effective_run_id.
    store = VariableStore()
    store.set("RUN_ID", "suite-owned-123")
    assert store.render_string("regr-x-{{RUN_ID}}") == "regr-x-suite-owned-123"
    assert store.effective_run_id == "suite-owned-123"


def test_stores_generate_distinct_run_ids() -> None:
    assert VariableStore().effective_run_id != VariableStore().effective_run_id
