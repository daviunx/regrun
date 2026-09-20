"""The one definition of the order suite files run in.

Every part of the engine that asks "which of these two files comes first"
resolves it HERE. A second answer anywhere else is not a style problem: the
runner, the shard planner, the file selectors and the linter each decide
something on the strength of that order, and two of them disagreeing produces a
verdict nobody can reproduce. That is exactly what a literal ``path.name`` sort
next to a bare stem comparison did: the two agree on ordinary names and diverge
the moment a name carries punctuation, because the constant ``.yaml`` suffix is
then compared against a real character.

THE KEY: layer rank first (``setup``, ``api``, ``mcp``, ``chat``; an unknown
layer sorts last), then the file's STEM in byte order. The extension is excluded
deliberately, because it is a constant that carries no ordering intent, and
dropping it is what removes the punctuation artifact. Byte order means digits
before uppercase before underscore before lowercase, and it is never folded: a
suite's order must not depend on a locale or a case table.

What that means for the operator, given two files in one layer:

* ``00_setup`` runs BEFORE ``00_setup-extra``. A name that is a prefix of
  another runs first, whatever the longer one continues with. (A shell's ``ls``
  disagrees, because it compares ``.yaml`` against ``-extra``.)
* ``00_setup-extra`` runs before ``00a_x``: ``_`` precedes ``a``.
* ``00.b`` runs before all of them: ``.`` precedes ``_`` and every letter.
* ``00A_x`` runs before ``00a_x``: uppercase precedes lowercase.

Numeric prefixes made of digits, letters and underscores are unaffected by all
of this, which is what the convention is for.
"""

__all__ = [
    "LAYER_ORDER",
    "layer_rank",
    "run_order_key",
    "within_layer_key",
]

# Layer rank for the canonical run order. Defined HERE, once: the runner, the
# linter and the shard planner must all consume the same order, and a second
# copy is a silent desync waiting for a fifth layer.
LAYER_ORDER = {"setup": 0, "api": 1, "mcp": 2, "chat": 3}


def layer_rank(layer: str) -> int:
    """Run position of a layer. An unrecognised layer sorts after every known one."""
    return LAYER_ORDER.get(layer, 99)


def within_layer_key(stem: str) -> str:
    """How two files in the SAME layer compare: by stem, in byte order.

    Trivial by design, and named anyway. It is the single place the decision to
    compare stems rather than filenames is made, so a caller reaching for
    ``path.name`` is visibly reaching past the contract.
    """
    return stem


def run_order_key(stem: str, layer: str) -> tuple[int, str]:
    """Sort key for the canonical run order: layer rank, then stem.

    Pass a file's stem and its ``meta.layer``. Sorting any collection of files by
    this key yields the order the runner executes them in, which is the order
    every ordering-dependent judgement in the engine is entitled to assume.
    """
    return (layer_rank(layer), within_layer_key(stem))
