"""Schema rule: E006 (the file parses as YAML but does not validate against the
test-file schema).

The linter reads raw dictionaries so it can report on source text the models
never keep. That makes it structurally possible for the linter to be BLINDER
than the engine: a file carrying a key no model declares, or missing a required
one, parses as YAML, satisfies every text-level rule, and reports ``0 errors``
while ``regrun run`` refuses to load it. E006 closes that gap by validating each
parsed file against ``TestFile`` and reporting every pydantic error as an
E-class finding, with the failing location and the pydantic message.
"""

from pydantic import ValidationError

from regrun.engine.lint_rules.context import ERROR, FileContext, LintFinding
from regrun.models import TestFile

__all__ = ["check_file"]


def _test_id_for(ctx: FileContext, loc: tuple[object, ...]) -> str:
    """Attribute a ``groups.N.tests.M...`` location to its test id, when it has one."""
    if len(loc) >= 4 and loc[0] == "groups" and loc[2] == "tests":
        group_index, test_index = loc[1], loc[3]
        if isinstance(group_index, int) and isinstance(test_index, int):
            groups = ctx.groups
            if group_index < len(groups):
                tests = groups[group_index].get("tests") or []
                if test_index < len(tests):
                    test = tests[test_index]
                    if isinstance(test, dict) and isinstance(test.get("id"), str):
                        return test["id"]
    return "-"


def check_file(ctx: FileContext) -> list[LintFinding]:
    """E006 — the file does not validate against the test-file schema."""
    try:
        TestFile.model_validate(ctx.raw)
    except ValidationError as exc:
        return [
            ctx.finding(
                _test_id_for(ctx, err["loc"]),
                "E006",
                ERROR,
                "schema violation at {loc}: {msg}".format(
                    loc=".".join(str(part) for part in err["loc"]) or "<root>",
                    msg=err["msg"],
                ),
            )
            for err in exc.errors()
        ]
    return []
