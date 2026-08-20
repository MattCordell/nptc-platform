"""FR-83's one legitimate semantic-tag strip - the export renderer.

Landed with issue #48 rather than a later export-renderer issue, so the
"exactly one call site" guarantee (FR-83, the double-strip hazard) exists
from the moment a served FSN first has anywhere to live at all
(`nptc.db.models.code_binding`). `backend/tests/test_catalogue_bindings.py`
asserts structurally that `render_display_term`, and the shared
`semantic_tag`/`strip_semantic_tag` functions it wraps, are referenced from
no module outside this package.

**Why this can't just call `nptc_shared.terminology.strip_semantic_tag`
directly.** That function already exists for a second, narrowly scoped
purpose (FR-97's seeding-time reconciliation, ADR-0006) and deliberately
*returns its input unchanged, never raising*, when there is no trailing
parenthesised group - the right behaviour for a seeding comparison that
already counts that case separately, but the wrong one for an export that
runs unattended on every release. FR-83 requires the export to fail loudly
instead, because a value with no tag at all is not a served FSN (FR-82
guarantees every stored `fsn` came from the server, and a served FSN always
carries exactly one tag) - so this module adds that assertion on top rather
than duplicating the strip rule itself, which stays defined exactly once, in
`nptc_shared.terminology._SEMANTIC_TAG`, shared by `semantic_tag`,
`strip_semantic_tag` and this function.
"""

from __future__ import annotations

from nptc_shared.terminology import semantic_tag, strip_semantic_tag

__all__ = ["render_display_term"]


class NotAServedFSNError(ValueError):
    """Raised by `render_display_term` when its input carries no trailing
    parenthesised group - PRD FR-83's first defensive assertion: the value
    is then not a served FSN (FR-82 guarantees every stored `fsn` is), and
    the export MUST fail loudly rather than publish it."""


def render_display_term(fsn: str) -> str:
    """FR-83's one legitimate strip: `fsn`'s final parenthesised group
    (its semantic tag) removed, exactly once. `fsn` MUST be read directly
    from `code_binding.fsn` - by FR-82, that column always holds a served
    FSN, so this is the only place this transformation is permitted at all.

    Raises `NotAServedFSNError` if `fsn` carries no trailing parenthesised
    group (PRD FR-83's first defensive assertion) - a stripped or otherwise
    non-served value must never reach this function silently. Raises
    `AssertionError` if the result is empty (PRD FR-83's second defensive
    assertion) - both are FR-83's guardrails against a bad input publishing
    something worse than a loud failure.

    `391483001`'s FSN, `"Microscopy (acid fast bacilli) (procedure)"`,
    renders as `"Microscopy (acid fast bacilli)"` - the regression fixture
    PRD SS6.4/NFR-38 test 11 names explicitly, and the reason the rule is
    "remove the *final* parenthesised group", not "remove every
    parenthesised group".
    """
    if semantic_tag(fsn) is None:
        raise NotAServedFSNError(
            f"{fsn!r} has no trailing parenthesised group and is therefore not a "
            "served FSN (FR-82) - refusing to strip a value that may already have "
            "been stripped (FR-83)"
        )
    result = strip_semantic_tag(fsn)
    if not result:
        raise AssertionError(f"stripping the semantic tag from {fsn!r} produced an empty string")
    return result
