"""Unit tests (TDD-RED) for response-body truncation in diagnostics.

Contract (analysis.md §4.1):

* ``regrun.engine.diagnostics.truncate_body(body, limit=None)`` returns the body
  as a string, cut to ``limit`` characters when it is longer, with the
  annotation ``…[truncated, N total chars]`` appended (N = the FULL length).
* Default limit is 2000 characters.
* ``REGRUN_DIAG_BODY_LIMIT`` overrides the default and MUST be read at call time
  (so an env var set after import is honoured).

New symbol imported inside each test so its absence is a per-test FAILURE.
"""


def test_short_body_is_returned_unchanged() -> None:
    from regrun.engine.diagnostics import truncate_body

    body = "small body"
    assert truncate_body(body) == "small body"


def test_default_limit_is_2000_chars_with_annotation() -> None:
    from regrun.engine.diagnostics import truncate_body

    total = 2500
    body = "y" * total
    out = truncate_body(body)

    assert out.startswith("y" * 2000)
    assert out[:2000] == "y" * 2000
    assert f"…[truncated, {total} total chars]" in out
    # The kept slice is exactly the limit, not the full body.
    assert out.count("y") == 2000


def test_env_override_respected_at_call_time(monkeypatch) -> None:
    from regrun.engine.diagnostics import truncate_body

    monkeypatch.setenv("REGRUN_DIAG_BODY_LIMIT", "10")

    total = 50
    body = "x" * total
    out = truncate_body(body)

    assert out.startswith("x" * 10)
    assert out.count("x") == 10
    assert f"…[truncated, {total} total chars]" in out


def test_explicit_limit_argument_wins() -> None:
    from regrun.engine.diagnostics import truncate_body

    body = "z" * 100
    out = truncate_body(body, limit=5)

    assert out.startswith("zzzzz")
    assert out.count("z") == 5
    assert "…[truncated, 100 total chars]" in out
