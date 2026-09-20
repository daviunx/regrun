"""Test-shape classification shared by the fixture and sweep-coverage rules.

"Is this test a create?" and "is this a deliberate negative?" decide whether
W004 / W007 / W011 apply at all, and both the per-file and the directory-level
rules must answer them identically.
"""

import re

# W011: a created-name string ``<prefix>{{RUN_ID}}`` names a fixture family by
# its prefix (``regr-vis-``); the prefix must appear in some sweep/cleanup
# delete pattern or the family is unsweepable.
FAMILY_PREFIX_RE = re.compile(r"([A-Za-z][A-Za-z0-9._-]*[-_])\{\{\s*RUN_ID\s*\}\}")

_CREATE_KEY_HINTS = ("name", "slug", "title", "email")
# HTTP POST path-SEGMENT exemptions for W004 — a POST is create-shaped by
# default, but these segment classes are NOT creates and carry no per-run row:
#   * search / query — a read (semantic/keyword/hybrid search, ad-hoc query)
#   * login / auth / token — an auth exchange with FIXED credentials
# Matched as whole '/'-split segments (case-folded), never as substrings, so
# ``/searchable-widgets`` or ``/oauthorize-foo`` do NOT trip. Delete endpoints
# use a segment-CONTAINS check (``bulk-delete`` / ``.../delete``) because the
# verb rides a compound segment; bodyless POSTs are handled separately.
_SEARCH_SEGMENTS = frozenset({"search", "_search", "query"})
_AUTH_SEGMENTS = frozenset({"login", "auth", "token"})
# Multiplex WRITE actions that CREATE a new persistent row — the only case where
# per-run uniqueness (W004) applies. When a test's ``args.action`` names one of
# these it is create-shaped; EVERY other explicit action verb targets an existing
# entity or returns data — reads (get/list/list_missing/search/stats/…), in-place
# mutations (update/delete/restore), diagnostics — so W004 does NOT apply, even if
# the args carry a ``name``/``slug`` key. Discrete create tools (``tool_create``
# etc.) carry no ``action`` arg and fall through to the ``name``/``slug`` hint check.
_CREATE_ACTIONS = frozenset({"create", "add", "insert", "register", "new", "upsert"})


def http_post_is_noncreate(test: dict) -> bool:
    """An HTTP POST that is NOT a create → exempt from W004.

    Conservative, path-segment based (not substring soup):
      1. search / query endpoint (segment ``search``/``_search``/``query``) —
         a read, not a create.
      2. auth endpoint (segment ``login``/``auth``/``token``) — a fixed-
         credential exchange, no per-run row.
      3. delete endpoint (any segment CONTAINS ``delete``: ``bulk-delete`` /
         ``.../delete``) — delete semantics, not create.
      4. bodyless POST (no ``body`` and no ``json``) — an action toggle with
         nothing to carry a ``{{RUN_ID}}``.
    """
    segments = [s.lower() for s in str(test.get("path") or "").split("/") if s]
    if any(s in _SEARCH_SEGMENTS for s in segments):
        return True
    if any(s in _AUTH_SEGMENTS for s in segments):
        return True
    if any("delete" in s for s in segments):
        return True
    return test.get("body") is None and test.get("json") is None


def is_create_shaped(test: dict) -> bool:
    """True when the test creates a new persistent row."""
    if (test.get("method") or "").upper() == "POST":
        return not http_post_is_noncreate(test)
    args = test.get("args")
    if isinstance(args, dict):
        # An explicit ``action`` verb governs: only a genuine create verb is
        # create-shaped. Reads (get/list/…), in-place mutations (update/delete),
        # and diagnostics never create a per-run row, so W004 does not apply even
        # when they carry a name/slug filter/target arg.
        action = args.get("action")
        if isinstance(action, str) and action.strip():
            return action.strip().lower() in _CREATE_ACTIONS
        return any(any(h in str(k).lower() for h in _CREATE_KEY_HINTS) for k in args)
    return False


def is_negative_test(test: dict) -> bool:
    """A negative/expected-failure test — skipped by W004.

    Covers BOTH an HTTP 4xx status assertion (api layer) AND an MCP
    ``is_error: true`` assertion (mcp layer). A negative test intentionally
    rejects its input, so it exercises no create path and needs no per-run
    uniqueness.
    """
    assertion = test.get("assert") or {}
    status = assertion.get("status")
    values = status if isinstance(status, list) else [status]
    if any(isinstance(s, int) and 400 <= s < 500 for s in values):
        return True
    return assertion.get("is_error") is True
