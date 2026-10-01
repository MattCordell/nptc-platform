"""FR-37 changelog note validation: the server-side authority every save must
pass through. The client-side gate stops the round trip before it happens;
this module enforces the rule, since NFR-20 requires that the server never
trust a client-only gate.

The note becomes the permanently published `History` text (PRD SS9.1), so a
lazy note is a permanently unhelpful public record. That is why this
validates *meaningfulness*, not merely presence.

**Normalise before measuring length.** `normalise_for_comparison`
(`nptc_shared.text`) collapses every non-ASCII `Zs` character (a
non-breaking space, PRD Appendix A.1's defect) to an ordinary space and
strips the edges. Measuring a raw note would let a caller pad a
low-information note past the minimum with invisible characters. The
*normalised* note is also what gets persisted, since a published History
entry padded with invisible characters is as undesirable as a padded
preferred term.

**The low-information check runs before the length check.** `"fix"` is both
too short and low-information. Reporting `LowInformationChangelogNoteError`
gives the caller, and the client gate, the more actionable message ("this
phrase is never a useful note"), where "type more characters" could be
satisfied by padding the same useless phrase.
"""

from __future__ import annotations

import re
from typing import ClassVar, Final

from nptc_shared.text import normalise_for_comparison

#: PRD FR-37: "minimum length, rejected if it matches a list of
#: low-information strings such as 'update', 'fix', '.'". Ten characters
#: forces a short phrase rather than a single word; "typo fixed" (10) passes
#: only because it is not on the list below.
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
#: Matches a letter. A note such as `"."`, `"---"` or `"2026"` would otherwise
#: slip past the length check with no informative content. Mirrored in
#: `frontend/src/catalogue/changelog-note.ts` as `HAS_LETTER_RE` (ADR-0030).
#: `test_has_letter_re_matches_exactly_letter_and_numeric_categories` in
#: `backend/tests/test_changelog_note.py` pins it to exactly the general
#: categories Lu, Ll, Lt, Lm, Lo, Nl and No.
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


class ChangelogNoteError(ValueError):
    """Base class for every way a changelog note can fail FR-37 validation.
    Carries the `http_status` `ClassVar` of `nptc.catalogue.errors`, so
    `nptc.api.errors` registers one handler for the base class."""

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


class ChangelogNoteMissingLetterError(ChangelogNoteError):
    """Raised when the normalised note contains no letter at all (e.g.
    `"---"`, `"2026"`). Distinct from `ChangelogNoteTooShortError`: a
    13-character note of digits and punctuation is not short, it has no
    informative content, and deserves different guidance."""


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

    if len(normalised) < MINIMUM_NOTE_LENGTH:
        raise ChangelogNoteTooShortError(
            f"a changelog note must be at least {MINIMUM_NOTE_LENGTH} "
            f"characters and describe the change (FR-37); got {normalised!r}"
        )

    if not _HAS_LETTER_RE.search(normalised):
        raise ChangelogNoteMissingLetterError(
            f"a changelog note must contain a letter and describe the change "
            f"(FR-37); got {normalised!r}"
        )

    return normalised
