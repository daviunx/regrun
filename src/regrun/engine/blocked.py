"""Result emission for tests the run decided NOT to execute.

Extracted from ``executor.py`` (size limits): every skipped ``TestResult`` the
run loop mints comes from here, so the reasons a test did not run live in one
module instead of being inlined in the loop.
"""

from regrun.engine.reporter import TestResult
from regrun.models import Test

__all__ = ["skipped_result"]


def skipped_result(test: Test, group_name: str, file_stem: str = "") -> TestResult:
    """A plain skip: the run aborted (fail-fast) before reaching this test."""
    return TestResult(
        test_id=test.id,
        test_name=test.name,
        group_name=group_name,
        passed=False,
        skipped=True,
        file_stem=file_stem,
    )
