"""Lint rule families, one module per family.

``linter.py`` is the coordinator: it parses the suite, builds a ``FileContext``
per file and dispatches to the registries here. A new rule lands in the module
its family owns (or a new module registered below), never in the coordinator.

============ =====================================================
Module       Rules
============ =====================================================
``structure`` E001, E002, W006
``auth``      E003, E004
``asserts``   W001, W002, W009, W010
``timing``    W003
``fixtures``  W004, W005, W007, W011
``hosts``     W008
``variables`` W012, E005
``schema``    E006
============ =====================================================
"""

from pathlib import Path
from typing import Callable

from regrun.engine.lint_rules import (
    asserts,
    auth,
    fixtures,
    hosts,
    schema,
    structure,
    timing,
    variables,
)
from regrun.engine.lint_rules.context import (
    ERROR,
    WARN,
    FileContext,
    LintFinding,
    build_file_context,
    derived_variables,
    is_mcp_file,
)

# A per-test rule reads one test in the context of its file and group.
TestRule = Callable[[FileContext, int, dict, dict], list[LintFinding]]
# A per-file rule reads the whole file.
FileRule = Callable[[FileContext], list[LintFinding]]
# A directory rule reads every parsed file at once.
DirectoryRule = Callable[[list[tuple[Path, dict, str]]], list[LintFinding]]

TEST_RULES: tuple[TestRule, ...] = (
    auth.check_test,
    asserts.check_test,
    hosts.check_test,
    timing.check_test,
    fixtures.check_test,
)

# Registry order is load-bearing: it fixes the order findings are emitted in.
# A new rule is APPENDED, never inserted.
FILE_RULES: tuple[FileRule, ...] = (
    hosts.check_sweep,
    structure.check_file,
    schema.check_file,
)

DIRECTORY_RULES: tuple[DirectoryRule, ...] = (
    structure.check_directory,
    fixtures.check_sweep_coverage,
    variables.check_directory,
)

__all__ = [
    "DIRECTORY_RULES",
    "ERROR",
    "FILE_RULES",
    "TEST_RULES",
    "WARN",
    "FileContext",
    "LintFinding",
    "build_file_context",
    "derived_variables",
    "is_mcp_file",
]
