"""FR-37 changelog note validation (issue #47).

Pure-function tests, no database - `nptc.catalogue.changelog` has no
dependency on a session or model.
"""

from __future__ import annotations

import pytest

from nptc.catalogue.changelog import (
    MINIMUM_NOTE_LENGTH,
    SEED_IMPORT_NOTE,
    ChangelogNoteMissingLetterError,
    ChangelogNoteTooShortError,
    EmptyChangelogNoteError,
    LowInformationChangelogNoteError,
    validate_changelog_note,
)

#: Built via `chr()`, not a literal non-ASCII space, per this repo's own
#: convention (see `shared/tests/test_sctid.py`) - sidesteps ruff's RUF001
#: ambiguous-character check on a source literal.
_NBSP = chr(0x00A0)


@pytest.mark.req("FR-37")
@pytest.mark.parametrize("note", [None, "", "   ", _NBSP * 3])
def test_empty_or_invisible_only_note_is_rejected(note: str | None) -> None:
    with pytest.raises(EmptyChangelogNoteError):
        validate_changelog_note(note)


@pytest.mark.req("FR-37")
@pytest.mark.parametrize(
    "note",
    [
        "update",
        "Update",
        "UPDATE.",
        "fix",
        "Fixed",
        "minor update",
        "as discussed",
    ],
)
def test_low_information_note_is_rejected(note: str) -> None:
    """These all match `LOW_INFORMATION_NOTES` and are also short enough
    to fail the length floor - `LowInformationChangelogNoteError` is
    raised because that check runs first (see the module docstring), not
    `ChangelogNoteTooShortError`."""
    with pytest.raises(LowInformationChangelogNoteError):
        validate_changelog_note(note)


@pytest.mark.req("FR-37")
@pytest.mark.parametrize("note", ["2026", "12345", "00:00"])
def test_short_letterless_note_is_rejected_for_length_not_missing_letter(note: str) -> None:
    """Too short *and* letterless, but not on `LOW_INFORMATION_NOTES` (that
    list's `"."`/`"n/a"`/`"---"` all fold to an empty or short punctuation-
    stripped string already in the set, so they raise
    `LowInformationChangelogNoteError` instead - see
    `test_low_information_note_is_rejected`). The length check runs before
    the letter check, so this is `ChangelogNoteTooShortError`, not
    `ChangelogNoteMissingLetterError`."""
    with pytest.raises(ChangelogNoteTooShortError):
        validate_changelog_note(note)


@pytest.mark.req("FR-37")
@pytest.mark.parametrize("note", ["1234567890123", "2026-08-20 00:00"])
def test_letterless_note_past_the_length_floor_is_rejected_for_missing_letter(note: str) -> None:
    """A note with no letter at all (PRD FR-37's own "meaningful" bar) must
    be rejected even once it clears the length floor - regression test for
    a bug where this fell through to `ChangelogNoteTooShortError` with a
    misleading "must be at least 10 characters" message for a note that
    was already 13+ characters long."""
    assert len(note) >= MINIMUM_NOTE_LENGTH
    with pytest.raises(ChangelogNoteMissingLetterError):
        validate_changelog_note(note)


@pytest.mark.req("FR-37")
def test_note_padded_with_non_breaking_spaces_still_fails_the_length_floor() -> None:
    """A naive `len()` check would count the non-breaking-space padding as
    real characters and let a low-information note squeak past the minimum -
    exactly the PRD Appendix A.1 defect class recurring on a new field.
    `normalise_for_comparison` collapses and strips them first, so the note
    is judged on its real content."""
    padded = "fix" + _NBSP * 10
    with pytest.raises(LowInformationChangelogNoteError):
        validate_changelog_note(padded)


@pytest.mark.req("FR-37")
def test_short_but_not_low_information_note_is_rejected_for_length() -> None:
    with pytest.raises(ChangelogNoteTooShortError):
        validate_changelog_note("ok done")


@pytest.mark.req("FR-37")
def test_meaningful_note_is_accepted_and_normalised() -> None:
    result = validate_changelog_note("Corrected the specimen for the RBC assay" + _NBSP)
    assert result == "Corrected the specimen for the RBC assay"
    assert len(result) >= MINIMUM_NOTE_LENGTH


@pytest.mark.req("FR-37")
def test_seed_import_note_passes_validation_on_its_own_merits() -> None:
    """ADR-0010's seeded-import note is a real sentence, not an exemption -
    FR-37 has no bypass anywhere, including for system-initiated writes.
    (`validate_changelog_note` would already have raised above if
    `SEED_IMPORT_NOTE` matched the low-information list, so the only thing
    left worth asserting independently is that it round-trips unchanged.)"""
    assert validate_changelog_note(SEED_IMPORT_NOTE) == SEED_IMPORT_NOTE
