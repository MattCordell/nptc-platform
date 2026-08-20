"""Designation storage tests (issue #47, FR-04, FR-24, FR-37, FR-63, FR-85).

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_business_key.py`'s own module docstring for why.

FR-05 collision detection is out of scope here - it is issue #49's own test
module, layered on top of the rows created here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.designations import (
    DesignationTermError,
    add_designation,
    add_synonyms,
    preferred_term_length,
    retire_designation,
)
from nptc.catalogue.entries import create_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.designation import Designation, DesignationStatus, DesignationUse
from nptc_transform.cell_defects import split_synonyms

_NBSP = chr(0x00A0)
_ZERO_WIDTH_SPACE = chr(0x200B)


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _new_entry(session: Session, preferred_term: str = "Full blood count") -> object:
    return create_entry(
        session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for FR-47 designation test",
    )


# --- FR-04: synonyms are rows, never a delimited string ---------------------


@pytest.mark.req("FR-04")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("ADA RBC, ADA red cells", ("ADA RBC", "ADA red cells")),
        ("Zovirax;;Cyclir", ("Zovirax", "Cyclir")),
        ("Aciclovir ; Acyclovir ;  ", ("Aciclovir", "Acyclovir")),
    ],
)
def test_sample_defect_strings_become_individual_rows_with_no_empty_row(
    app_session: Session, cell: str, expected: tuple[str, ...]
) -> None:
    entry = _new_entry(app_session)

    parts = split_synonyms(cell)
    assert parts == expected

    created = add_synonyms(
        app_session,
        AuditContext.system(),
        entry=entry,
        terms=parts,
        reason="Split synonyms from the sample cell",
    )

    assert [d.term for d in created] == list(expected)
    assert all(term for term in (d.term for d in created))


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_blank_term_is_refused(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(DesignationTermError):
        add_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            term="   ",
            reason="Attempting to add a blank synonym",
        )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_duplicate_active_synonym_is_refused_by_the_partial_unique(app_session: Session) -> None:
    entry = _new_entry(app_session)
    add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="First synonym add"
    )
    app_session.flush()

    with pytest.raises(IntegrityError):
        add_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            term="FBC",
            reason="Duplicate synonym add",
        )
        app_session.flush()


# --- The three-strings boundary ---------------------------------------------


def test_designation_table_has_no_served_label_columns() -> None:
    """`designation` is catalogue-side only - it never mirrors a SNOMED
    CT-served label (`code_binding.au_preferred_term`/`code_binding.fsn`,
    #48). A future change that starts copying a served label into this
    table should fail this test, not pass review unnoticed."""
    columns = set(Designation.__table__.c.keys())
    assert "au_preferred_term" not in columns
    assert "fsn" not in columns


def test_designations_module_does_not_import_the_code_binding_side() -> None:
    import nptc.catalogue.designations as module

    assert "code_binding" not in module.__file__
    assert not any("code_binding" in name for name in dir(module))


# --- FR-85 / FR-24: computed, never stored, never settable ------------------


@pytest.mark.req("FR-85")
@pytest.mark.req("FR-24")
def test_preferred_term_length_matches_the_cleaned_character_count() -> None:
    """PRD §6.5's migration note: a trailing non-breaking space shifts the
    published length once it collapses to nothing after cleaning - the
    case that must not be missed."""
    assert preferred_term_length("Aciclovir level" + _NBSP) == len("Aciclovir level")
    assert preferred_term_length("Aciclovir level" + _NBSP) == 15


@pytest.mark.req("FR-85")
@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_designation_length_has_no_setter_and_no_column(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )

    assert designation.length == len("FBC")
    with pytest.raises(AttributeError):
        designation.length = 99  # type: ignore[misc]

    assert "length" not in Designation.__table__.c


# --- FR-63: normalisation on ingestion and prohibition at entry -------------


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_term_with_trailing_non_breaking_space_is_stored_cleaned(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        term="Aciclovir level" + _NBSP,
        reason="Adding a synonym with a trailing NBSP",
    )

    assert designation.term == "Aciclovir level"


@pytest.mark.integration
def test_term_with_a_zero_width_space_is_refused(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(DesignationTermError) as exc_info:
        add_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            term="FB" + _ZERO_WIDTH_SPACE + "C",
            reason="Attempting to add a zero-width-space synonym",
        )

    assert _ZERO_WIDTH_SPACE not in str(exc_info.value)
    assert "<U+200B>" in str(exc_info.value)


# --- FR-37 negative path: a rejected note leaves no audit event -------------


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_low_information_note_is_refused_and_leaves_no_audit_event(app_session: Session) -> None:
    entry = _new_entry(app_session)
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(ChangelogNoteError):
        add_designation(app_session, AuditContext.system(), entry=entry, term="FBC", reason="fix")

    assert _audit_event_count(app_session) == before


# --- A retired designation is retained, not deleted -------------------------


@pytest.mark.integration
def test_retired_designation_is_still_selectable(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()

    retire_designation(
        app_session,
        AuditContext.system(),
        designation=designation,
        reason="Retiring the FBC synonym",
    )
    app_session.flush()

    reloaded = app_session.execute(
        select(Designation).where(Designation.id == designation.id)
    ).scalar_one()
    assert reloaded.status == str(DesignationStatus.RETIRED)


# --- Decision 1: the catalogue's en-AU preferred term lives in one place ----


@pytest.mark.integration
def test_en_au_preferred_designation_is_refused(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(IntegrityError):
        add_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            term="Full blood count",
            use=str(DesignationUse.PREFERRED),
            language="en-AU",
            reason="Attempting to duplicate the catalogue preferred term",
        )
        app_session.flush()


@pytest.mark.integration
def test_a_non_en_au_preferred_designation_is_accepted(app_session: Session) -> None:
    entry = _new_entry(app_session)

    designation = add_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        term="Panui toto katoa",
        use=str(DesignationUse.PREFERRED),
        language="mi-NZ",
        reason="Adding a non-en-AU preferred term",
    )
    app_session.flush()

    assert designation.use == "preferred"
    assert designation.language == "mi-NZ"
