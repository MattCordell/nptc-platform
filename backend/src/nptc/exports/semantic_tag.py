"""FR-83's sanctioned semantic-tag strip, and the label trims that sit beside it.

The export renderer and the list read model (`nptc.api.routers.catalogue_shared`) call
`render_display_term`. `backend/tests/test_catalogue_bindings.py` asserts that it and the
shared `semantic_tag`/`strip_semantic_tag` are referenced from no other module across
`backend/src`, `transform/src` and `shared/src`, bar the shared package's re-export, two
FR-97 seeding-reconciliation sites (ADR-0006) and the transform's dataset builder, which
only reads whether a served FSN has a tag.

**Why not call `nptc_shared.terminology.strip_semantic_tag` alone.** It returns
its input unchanged when there is no trailing parenthesised group, which suits a
seeding comparison that counts that case separately (ADR-0006). An export that
runs unattended on every release must fail loudly instead, because every stored
`fsn` came from the server (FR-82) and a served FSN always carries a tag. This module adds that assertion and leaves the
strip rule defined once, in `nptc_shared.terminology.snomed`.
"""

from __future__ import annotations

import re
from typing import ClassVar

from nptc_shared.terminology import semantic_tag, strip_semantic_tag

__all__ = [
    "EmptyDisplayTermError",
    "NotAServedFSNError",
    "render_display_term",
    "trim_specimen_suffix",
]

_SPECIMEN_SUFFIX = re.compile(r"\s+specimen$", re.IGNORECASE)


class NotAServedFSNError(ValueError):
    """Raised by `render_display_term` when its input has no trailing
    parenthesised group (FR-83's first assertion). FR-82 guarantees a stored
    `fsn` is a served FSN, which always has one, so the export must fail rather
    than publish this value. A stored-data fault, not a caller mistake, so a 500."""

    http_status: ClassVar[int] = 500


class EmptyDisplayTermError(ValueError):
    """Raised by `render_display_term` when stripping the semantic tag would
    leave nothing (FR-83's second assertion). An `AssertionError` would read as
    a bug in this module; `http_status` matches `NotAServedFSNError` so a caller
    handles both alike."""

    http_status: ClassVar[int] = 500


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


def trim_specimen_suffix(term: str) -> str:
    """`term` without a trailing "specimen" word ("Serum specimen" shows as "Serum").

    A bare "Specimen", the root concept's term, is returned as it is, never empty.
    """
    trimmed = _SPECIMEN_SUFFIX.sub("", term)
    return trimmed or term
