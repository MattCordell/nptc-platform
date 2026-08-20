"""FR-37 changelog note validation: the server-side authority every save must
pass through (issue #47). Issue #62's client-side gate is the affordance that
stops the round trip before it happens - this module is what actually enforces
it, since NFR-20 requires the server never trust a client-only gate.

The note becomes the permanently published ``History`` text (PRD §9.1's
FR-37 rationale), so a lazy note today is a permanently unhelpful public
record - which is why this validates *meaningfulness*, not merely presence.

**Why normalise before measuring length.** ``nptc_shared.text.
normalise_for_comparison`` collapses every non-ASCII ``Zs`` character (a
non-breaking space, PRD Appendix A.1's own defect) to an ordinary space and
strips the edges. Measuring a raw, un-normalised note's length would let a
caller pad a low-information note past the minimum with invisible
characters - exactly the defect class this platform exists to eliminate,
now on a new field. The *normalised* note is also what should be persisted:
a published History entry padded with invisible characters is exactly as
undesirable as a preferred term padded with them.

**Why the low-information check runs before the length check.** ``"fix"``
is both too short *and* low-information; reporting
``LowInformationChangelogNoteError`` rather than ``ChangelogNoteTooShortError``
gives the caller (and #62's client gate) the more specific, more actionable
message - "this phrase is never a useful changelog note" beats "type more
characters", which a caller could satisfy by padding the same useless phrase.
"""

from __future__ import annotations

import re
from typing import ClassVar, Final

from nptc_shared.text import normalise_for_comparison

#: PRD FR-37: "minimum length, rejected if it matches a list of
#: low-information strings such as 'update', 'fix', '.'". Ten characters is
#: long enough to force a short phrase rather than a single word - "typo
#: fixed" (10) still passes only because it isn't on the list below, but
#: "corrected specimen" and similar short, real descriptions comfortably clear
#: it.
MINIMUM_NOTE_LENGTH: Final[int] = 10

#: Casefolded, punctuation-stripped low-information phrases (PRD FR-37's own
#: examples plus the obvious neighbours) - checked against the note with the
#: same casefolding and punctuation-stripping applied, so "Fix.", "FIX", and
#: "fix" all match the one entry "fix".
LOW_INFORMATION_NOTES: Final[frozenset[str]] = frozenset(
    {
        "update",
        "updated",
        "fix",
        "fixed",
        "change",
        "changed",
        "edit",
        "edited",
        "correction",
        "corrected",
        "typo",
        "minor",
        "minor update",
        "minor change",
        "as discussed",
        "as agreed",
        "n a",
        "na",
        "none",
        "test",
        "wip",
        "tidy up",
        "cleanup",
        "misc",
        "",
    }
)

#: A real, specific sentence for the ADR-0010 seeded-import path - passes
#: validation on its own merits rather than being an exemption from it, so
#: FR-37 has no bypass anywhere in the codebase, including for system-
#: initiated writes.
SEED_IMPORT_NOTE: Final[str] = "Seeded from the RCPA-QAP baseline catalogue import (ADR-0010)."

#: Strips everything but letters/digits/spaces before matching against
#: ``LOW_INFORMATION_NOTES``, so "Fix." and "fix" compare equal without the
#: list needing a punctuated variant of every phrase.
_STRIP_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
#: True if the note contains no letter at all - catches "." , "---", "2026",
#: which would otherwise slip past the length check with no informative
#: content whatsoever.
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


class ChangelogNoteError(ValueError):
    """Base class for every way a changelog note can fail FR-37 validation.
    Carries the same ``http_status: ClassVar[int]`` convention as
    `nptc.catalogue.errors` so `nptc.api.errors` can register one handler
    for the base class rather than one per subclass."""

    http_status: ClassVar[int] = 422


class EmptyChangelogNoteError(ChangelogNoteError):
    """Raised when the note is `None`, empty, or whitespace/invisible-
    character-only after normalisation."""


class ChangelogNoteTooShortError(ChangelogNoteError):
    """Raised when the normalised note is shorter than
    `MINIMUM_NOTE_LENGTH`."""


class LowInformationChangelogNoteError(ChangelogNoteError):
    """Raised when the note matches `LOW_INFORMATION_NOTES` - checked before
    the length rule so the caller gets the more specific message (see the
    module docstring)."""


def _fold(note: str) -> str:
    """Casefolded, punctuation-stripped, whitespace-collapsed form used only
    for matching against `LOW_INFORMATION_NOTES` - never what gets
    persisted."""
    stripped = _STRIP_PUNCTUATION_RE.sub("", note)
    return " ".join(stripped.casefold().split())


def validate_changelog_note(note: str | None) -> str:
    """Validates `note` against FR-37 and returns the normalised text that
    should actually be persisted as `audit_event.reason`.

    Raises a `ChangelogNoteError` subclass - never returns on a rejected
    note - so a caller can call this before any mutation is attempted and
    be certain no partial write or audit event follows a bad note (the same
    posture `nptc.catalogue.entries.save_entry` already takes for a stale
    `row_version`).
    """
    if note is None:
        raise EmptyChangelogNoteError("a changelog note is required (FR-37)")

    normalised = normalise_for_comparison(note)
    if not normalised:
        raise EmptyChangelogNoteError("a changelog note is required (FR-37)")

    if _fold(normalised) in LOW_INFORMATION_NOTES:
        raise LowInformationChangelogNoteError(
            f"{normalised!r} is not a meaningful changelog note (FR-37) - it "
            "becomes the published History text, so it must describe what "
            "actually changed"
        )

    if len(normalised) < MINIMUM_NOTE_LENGTH or not _HAS_LETTER_RE.search(normalised):
        raise ChangelogNoteTooShortError(
            f"a changelog note must be at least {MINIMUM_NOTE_LENGTH} "
            f"characters and describe the change (FR-37); got {normalised!r}"
        )

    return normalised
