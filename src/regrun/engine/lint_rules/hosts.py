"""Stack-targeting rule: W008 — a hardcoded host or database in a bash command.

A literal host makes the step read the WRONG stack silently instead of failing
loud, which is why it applies to sweep steps as well as tests.
"""

import re

from regrun.engine.lint_rules.context import WARN, FileContext, LintFinding

# A URL/db literal is parameterized (and exempt) when it sits inside a Jinja
# ``{{ ... }}`` span (e.g. the DEFAULT of
# ``{{ env.get('REGRUN_API_ENDPOINT', 'http://…') }}``) or a shell
# ``${VAR:-default}`` span, or when the host itself is a variable
# (``http://${API_HOST}/…`` / ``http://{{HOST}}/…``).
_URL_LITERAL_RE = re.compile(r"https?://")
_JINJA_SPAN_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_SHELL_SPAN_RE = re.compile(r"\$\{[^}]*\}")
_PSQL_DB_RE = re.compile(r"\bpsql\b[^;|&\n]*?\s-d\s+(\S+)")

_REMEDY = "(use {{ env.get(...) }} or ${VAR:-default})"

__all__ = ["check_sweep", "check_test", "hardcoded_literals"]


def _param_spans(s: str) -> list[tuple[int, int]]:
    """Character spans of ``{{ … }}`` / ``${ … }`` parameterizations in a string."""
    return [m.span() for m in _JINJA_SPAN_RE.finditer(s)] + [
        m.span() for m in _SHELL_SPAN_RE.finditer(s)
    ]


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def hardcoded_literals(cmd: str) -> list[str]:
    """W008 messages for one bash command: hardcoded URL hosts / psql db targets.

    A literal is exempt when it sits inside a ``{{ … }}`` or ``${ … }`` span
    (parameterized with a default), or when the host/name itself is a variable.
    """
    messages: list[str] = []
    spans = _param_spans(cmd)

    for m in _URL_LITERAL_RE.finditer(cmd):
        if _in_spans(m.start(), spans):
            continue
        after = cmd[m.end() : m.end() + 2]
        if after.startswith("$") or after.startswith("{{"):
            continue  # literal scheme, parameterized host
        token = re.match(r"\S+", cmd[m.start() :])
        literal = token.group(0).rstrip("'\"),;") if token else cmd[m.start() :]
        messages.append(f"hardcoded URL '{literal}'")

    for m in _PSQL_DB_RE.finditer(cmd):
        name = m.group(1).strip("'\"")
        if name.startswith("$") or name.startswith("{{") or _in_spans(m.start(1), spans):
            continue
        messages.append(f"hardcoded psql database '-d {name}'")

    return messages


def _commands_of(item: dict) -> list[str]:
    commands: list[str] = []
    for bash_cmd in item.get("commands") or []:
        cmd = bash_cmd.get("cmd") if isinstance(bash_cmd, dict) else None
        if isinstance(cmd, str):
            commands.append(cmd)
    return commands


def check_test(ctx: FileContext, _group_index: int, _group: dict, test: dict) -> list[LintFinding]:
    """W008 over one test's bash commands."""
    tid = test.get("id", "-")
    return [
        ctx.finding(tid, "W008", WARN, f"{message} {_REMEDY}")
        for cmd in _commands_of(test)
        for message in hardcoded_literals(cmd)
    ]


def check_sweep(ctx: FileContext) -> list[LintFinding]:
    """W008 over the file's sweep steps.

    A sweep with a hardcoded host sweeps the wrong stack, which is exactly the
    failure class the block exists to prevent.
    """
    findings: list[LintFinding] = []
    for step in ctx.raw.get("sweep") or []:
        if not isinstance(step, dict):
            continue
        test_id = f"sweep:{step.get('name', '-')}"
        for cmd in _commands_of(step):
            for message in hardcoded_literals(cmd):
                findings.append(ctx.finding(test_id, "W008", WARN, f"{message} {_REMEDY}"))
    return findings
