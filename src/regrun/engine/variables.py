"""Jinja2 variable store, template rendering, and capture extraction."""

import os
import time
import uuid
from typing import Any

import structlog
from jinja2 import BaseLoader, Environment, StrictUndefined, UndefinedError
from jsonpath_ng import parse as jsonpath_parse

from regrun.models import Test

logger = structlog.get_logger()


class UnresolvedVariableError(Exception):
    """Strict-vars failure: a rendered template references an undefined variable.

    Raised instead of silently returning the raw template — an unresolved
    ``{{RUN_ID}}`` otherwise names a fixture ``regr-x-{{RUN_ID}}`` byte-identical
    on every run, a guaranteed cross-run collision that surfaces as flakes.
    """

    def __init__(self, template: str, detail: str) -> None:
        self.template = template
        self.detail = detail
        super().__init__(f"{detail} (template: {template!r})")


class VariableStore:
    """Dict-like store for template variables with built-in and env resolution.

    ``strict`` (default on) makes an unresolved ``{{VAR}}`` raise
    :class:`UnresolvedVariableError` instead of warn-and-return-literal. The
    executor toggles it per file from ``meta.strict_vars`` / ``--no-strict-vars``.
    """

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict
        self._vars: dict[str, str] = {}
        self._dotenv_vars: dict[str, str] = {}
        # Engine-owned per-run RUN_ID: generated ONCE per store (== once per
        # run), same format {{timestamp}} produces, so suites no longer need to
        # hand-declare RUN_ID: "{{timestamp}}" in 00_setup. A suite that still
        # declares or captures RUN_ID shadows it — the suite's value wins.
        self._run_id = f"{int(time.time())}{uuid.uuid4().hex[:4]}"
        self._jinja_env = Environment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            keep_trailing_newline=False,
        )

    def load_env_file(self, path: str) -> None:
        """Load a dotenv file into the env rendering context (not os.environ)."""
        from dotenv import dotenv_values

        loaded = dotenv_values(path)
        self._dotenv_vars.update({k: v for k, v in loaded.items() if v is not None})
        logger.info("env_file_loaded", path=path, keys=len(self._dotenv_vars))

    def get(self, key: str) -> str | None:
        """Get a variable value by name."""
        return self._vars.get(key)

    def set(self, key: str, value: str) -> None:
        """Set a variable value."""
        self._vars[key] = value

    def merge(self, variables: dict[str, str]) -> None:
        """Merge a dict of variables into the store (new values overwrite)."""
        self._vars.update(variables)

    def all(self) -> dict[str, str]:
        """Return a copy of all stored variables."""
        return dict(self._vars)

    @property
    def effective_run_id(self) -> str:
        """The RUN_ID this run renders: suite-declared/captured when present
        (backcompat — the suite's value wins), else the engine builtin."""
        return self._vars.get("RUN_ID", self._run_id)

    def _build_context(self) -> dict[str, Any]:
        """Build the full Jinja2 rendering context with built-ins and env access."""
        ctx: dict[str, Any] = dict(self._vars)
        # Builtin fallback only: a declared/captured RUN_ID (already in ctx via
        # self._vars) shadows the engine value.
        ctx.setdefault("RUN_ID", self._run_id)
        ctx["timestamp"] = f"{int(time.time())}{uuid.uuid4().hex[:4]}"
        ctx["date"] = time.strftime("%Y-%m-%d")
        ctx["uuid"] = str(uuid.uuid4())
        env = dict(os.environ)
        env.update(self._dotenv_vars)
        ctx["env"] = env
        return ctx

    def render_string(self, template_str: str) -> str:
        """Render a single string template, substituting variables."""
        if "{{" not in template_str and "{%" not in template_str:
            return template_str
        try:
            template = self._jinja_env.from_string(template_str)
            return template.render(self._build_context())
        except UndefinedError as e:
            if self.strict:
                raise UnresolvedVariableError(template_str, str(e)) from e
            logger.warning("undefined_variable", template=template_str, error=str(e))
            return template_str

    def render_value(self, value: Any) -> Any:
        """Recursively render template variables in a value."""
        if isinstance(value, str):
            return self.render_string(value)
        if isinstance(value, dict):
            return {k: self.render_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render_value(item) for item in value]
        return value


def render_test(test: Test, store: VariableStore) -> Test:
    """Deep-render all string fields in a test definition using the variable store.

    Returns a new Test instance with all template strings resolved. ``commands``
    are deliberately NOT rendered here: the bash runner re-renders each command
    at execution time so per-command ``capture:`` variables resolve mid-test —
    rendering them now would (in strict mode) fail on captures that have not
    happened yet.
    """
    test_data = test.model_dump(by_alias=True)
    commands = test_data.pop("commands", None)
    rendered_data = store.render_value(test_data)
    rendered_data["commands"] = commands
    return Test.model_validate(rendered_data)


def capture_from_response(
    captures: dict[str, str],
    response_body: dict | list | str | None,
) -> dict[str, str]:
    """Extract capture values from a response body using JSONPath expressions.

    Args:
        captures: Mapping of variable_name -> JSONPath expression (or 'stdout').
        response_body: Parsed response body (dict/list for JSON, str for raw text).

    Returns:
        Dict of variable_name -> extracted string value.
    """
    results: dict[str, str] = {}

    for var_name, expression in captures.items():
        if expression == "stdout":
            results[var_name] = str(response_body) if response_body is not None else ""
            continue

        if not isinstance(response_body, (dict, list)):
            logger.warning(
                "capture_skip_non_dict",
                variable=var_name,
                expression=expression,
                body_type=type(response_body).__name__,
            )
            continue

        try:
            parsed_expr = jsonpath_parse(expression)
            matches = parsed_expr.find(response_body)
            if matches:
                results[var_name] = str(matches[0].value)
                logger.debug("captured_variable", variable=var_name, value=results[var_name])
            else:
                logger.warning(
                    "capture_no_match",
                    variable=var_name,
                    expression=expression,
                )
        except Exception as e:
            logger.error(
                "capture_error",
                variable=var_name,
                expression=expression,
                error=str(e),
            )

    return results
