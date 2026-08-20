"""FR-37 changelog note validation (issue #47).

Pure-function tests, no database - `nptc.catalogue.changelog` has no
dependency on a session or model.
"""

from __future__ import annotations

import pytest

from nptc.catalogue.changelog import (
    LOW_INFORMATION_NOTES,
    MINIMUM_NOTE_LENGTH,
    SEED_IMPORT_NOTE,
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
        ".",
        "update",
        "Update",
        "UPDATE.",
        "fix",
        "Fixed",
        "minor update",
        "as discussed",
        "n/a",
        "---",
        "2026",
    ],
)
def test_low_information_note_is_rejected(note: str) -> None:
    """The list itself, plus the "no letter at all" catch (`"---"`,
    `"2026"`) that would otherwise slip past a naive length check."""
    with pytest.raises((LowInformationChangelogNoteError, ChangelogNoteTooShortError)):
        validate_changelog_note(note)


@pytest.mark.req("FR-37")
def test_note_padded_with_non_breaking_spaces_still_fails_the_length_floor() -> None:
    """A naive `len()` check would count the non-breaking-space padding as
    real characters and let a low-information note squeak past the minimum -
    exactly the PRD Appendix A.1 defect class recurring on a new field.
    `normalise_for_comparison` collapses and strips them first, so the note
    is judged on its real content."""
    padded = "fix" + _NBSP * 10
    with pytest.raises((LowInformationChangelogNoteError, ChangelogNoteTooShortError)):
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
    FR-37 has no bypass anywhere, including for system-initiated writes."""
    assert validate_changelog_note(SEED_IMPORT_NOTE) == SEED_IMPORT_NOTE
    assert validate_changelog_note(SEED_IMPORT_NOTE).casefold() not in LOW_INFORMATION_NOTES
