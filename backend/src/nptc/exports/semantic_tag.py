"""FR-83's one legitimate semantic-tag strip: the export renderer.

`backend/tests/test_catalogue_bindings.py` asserts that `render_display_term`
and the shared `semantic_tag`/`strip_semantic_tag` are referenced from no module
outside this package across `backend/src`, `transform/src` and `shared/src`. The
exceptions are the shared package's re-export and two FR-97
seeding-reconciliation call sites (ADR-0006). "Exactly one call site" is
therefore this package's claim about itself.

**Why not call `nptc_shared.terminology.strip_semantic_tag` alone.** It returns
its input unchanged when there is no trailing parenthesised group, which suits a
seeding comparison that counts that case separately (ADR-0006). An export that
runs unattended on every release must fail loudly instead, because a served FSN
always carries a tag (FR-82). This module adds that assertion and leaves the
strip rule defined once, in `nptc_shared.terminology.snomed`.
"""

from __future__ import annotations

from typing import ClassVar

from nptc_shared.terminology import semantic_tag, strip_semantic_tag

__all__ = ["EmptyDisplayTermError", "NotAServedFSNError", "render_display_term"]


class NotAServedFSNError(ValueError):
    """Raised by `render_display_term` when its input has no trailing
    parenthesised group (FR-83's first assertion). FR-82 guarantees a stored
    `fsn` has one, so the value is not a served FSN and the export must fail
    rather than publish it."""

    http_status: ClassVar[int] = 422


class EmptyDisplayTermError(ValueError):
    """Raised by `render_display_term` when stripping the semantic tag would
    leave nothing (FR-83's second assertion). An `AssertionError` would read as
    a bug in this module; `http_status` matches `NotAServedFSNError` so a caller
    handles both alike."""

    http_status: ClassVar[int] = 422


def render_display_term(fsn: str) -> str:
    """FR-83's one legitimate strip: `fsn` with its final parenthesised group
    (its semantic tag) removed, once. `fsn` must be read directly from
    `code_binding.fsn`, which holds a served FSN (FR-82).

    Raises `NotAServedFSNError` when `fsn` has no trailing group, and
    `EmptyDisplayTermError` when the result is empty. A stripped or non-served
    value must never pass silently.

    `391483001`'s FSN, `"Microscopy (acid fast bacilli) (procedure)"`, renders as
    `"Microscopy (acid fast bacilli)"` (PRD SS6.4, NFR-38 test 11). That is why
    the rule removes the *final* group, not every group.
    """
    if semantic_tag(fsn) is None:
        raise NotAServedFSNError(
            f"{fsn!r} has no trailing parenthesised group and is therefore not a "
            "served FSN (FR-82) - refusing to strip a value that may already have "
            "been stripped (FR-83)"
        )
    result = strip_semantic_tag(fsn)
    if not result:
        raise EmptyDisplayTermError(
            f"stripping the semantic tag from {fsn!r} produced an empty string"
        )
    return result
