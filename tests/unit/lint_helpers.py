"""Shared fixture builders for the lint-rule test files.

Every lint-rule test writes real YAML into a tmp directory and lints it through
``lint_directory`` — the rules are only meaningful against a real suite on disk.
These four helpers are that boilerplate, shared so each rule-family file holds
only its own fixtures.
"""

from pathlib import Path

import yaml


def write(directory: Path, name: str, doc: dict) -> None:
    """Write ``doc`` as a suite file named ``name`` inside ``directory``."""
    (directory / name).write_text(yaml.safe_dump(doc, sort_keys=False))


def rules(findings) -> set[str]:
    """The set of rule ids present in ``findings``."""
    return {f.rule for f in findings}


def api_doc(groups: list[dict]) -> dict:
    """A minimal ``layer: api`` / ``runner: httpx`` suite file."""
    return {"meta": {"product": "demo", "layer": "api", "runner": "httpx"}, "groups": groups}


def mcp_doc(groups: list[dict]) -> dict:
    """A minimal ``layer: mcp`` / ``runner: fastmcp`` suite file."""
    return {"meta": {"product": "demo", "layer": "mcp", "runner": "fastmcp"}, "groups": groups}


def create_test(body: dict) -> dict:
    """A create-shaped POST whose ``body`` decides whether a rule fires."""
    return {
        "id": "A.1",
        "name": "create",
        "method": "POST",
        "path": "/companies",
        "body": body,
        "assert": {"status": 201},
    }
