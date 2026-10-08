"""Designation storage tests (issue #47, FR-04, FR-24, FR-37, FR-63, FR-85).

Uses the shared `app_session` fixture from `conftest.py`, an ORM `Session`
bound to `app_db`.

FR-05 collision detection is out of scope here - it is issue #49's own test
module, layered on top of the rows created here.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue import queries
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.collisions import DesignationCollisionError
from nptc.catalogue.designations import (
    DesignationAlreadyRetiredError,
    DesignationNotFoundError,
    DesignationNotRetiredError,
    DuplicateActiveTermError,
    add_designation,
    add_synonyms,
    amend_designation,
    find_retired_designation,
    load_active_designation,
    load_retired_designation,
    reinstate_designation,
    retire_designation,
)
from nptc.catalogue.entries import create_entry
from nptc.catalogue.term_hygiene import TermCleaningError, preferred_term_length
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.designation import Designation, DesignationStatus
from nptc_transform.cell_defects import split_synonyms

_NBSP = chr(0x00A0)
_ZERO_WIDTH_SPACE = chr(0x200B)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


latest_audit_event = _load("audit_support").latest_audit_event


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _new_entry(session: Session, preferred_term: str = "Full blood count") -> CatalogueEntry:
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

    # Order-independent: `add_synonyms` inserts in comparison-key order,
    # not caller order (issue #49's deadlock-avoidance fix), so this
    # asserts the same *set* of rows exists, not positional equality.
    assert {d.term for d in created} == set(expected)
    assert len(created) == len(expected)
    assert all(term for term in (d.term for d in created))


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_blank_term_is_refused(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(TermCleaningError):
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
    """`ix_designation_no_duplicate_active_term` is a *within-entry*
    invariant, so `assert_no_error_collisions` (a cross-entry check, issue
    #49) never sees this case at all - the database constraint is the only
    thing enforcing it, and issue #224 is what gives its `IntegrityError` a
    typed translation rather than reaching the caller raw."""
    entry = _new_entry(app_session)
    add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="First synonym add"
    )
    app_session.flush()

    with pytest.raises(DuplicateActiveTermError):
        add_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            term="FBC",
            reason="Duplicate synonym add",
        )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_add_synonyms_deduplicates_terms_that_collapse_after_cleaning(
    app_session: Session,
) -> None:
    """`["FBC", "FBC "]` collapse to the same cleaned term - inserting both
    would violate `ix_designation_no_duplicate_active_term` at flush, from
    a batch the caller reasonably thinks is well-formed (the whole premise
    of FR-04 is cleaning up exactly this kind of whitespace-variant
    duplicate)."""
    entry = _new_entry(app_session)

    created = add_synonyms(
        app_session,
        AuditContext.system(),
        entry=entry,
        terms=["FBC", "FBC" + _NBSP, "  FBC  ", "CBC"],
        reason="Adding a batch with duplicate-after-cleaning terms",
    )
    app_session.flush()

    # Order-independent - see the sibling test above's own comment.
    assert {d.term for d in created} == {"FBC", "CBC"}


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
    """Checks the actual import graph via `inspect.getsource`, not merely
    the module's own `__file__` name - a bare `"code_binding" not in
    module.__file__` string check is vacuously true for any module that
    simply isn't named `code_binding` and asserts nothing about what it
    imports."""
    import nptc.catalogue.designations as module

    source = inspect.getsource(module)
    assert "code_binding" not in source


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
def test_catalogue_entry_length_is_the_fr85_published_figure(app_session: Session) -> None:
    """FR-85's `Length` is the *catalogue's* preferred term (PRD §6.5) -
    `CatalogueEntry.length`, not `Designation.length`, is the field FR-85
    actually publishes. No setter, no backing column."""
    entry = _new_entry(app_session, preferred_term="Aciclovir level" + _NBSP)

    assert entry.length == 15
    with pytest.raises(AttributeError):
        entry.length = 99  # type: ignore[misc]

    assert "length" not in CatalogueEntry.__table__.c


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_catalogue_entry_preferred_term_is_cleaned_at_entry(app_session: Session) -> None:
    entry = _new_entry(app_session, preferred_term="Aciclovir level" + _NBSP)

    assert entry.preferred_term == "Aciclovir level"


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

    with pytest.raises(TermCleaningError) as exc_info:
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


@pytest.mark.integration
def test_retiring_an_already_retired_designation_is_refused(app_session: Session) -> None:
    """Retiring twice would otherwise silently write a second
    `designation.retired` audit event with no actual state change."""
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=designation, reason="Retiring FBC synonym"
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(DesignationAlreadyRetiredError):
        retire_designation(
            app_session,
            AuditContext.system(),
            designation=designation,
            reason="Retiring FBC synonym again",
        )

    assert _audit_event_count(app_session) == before


@pytest.mark.integration
def test_load_designations_any_status_orders_repeated_retired_terms_deterministically(
    app_session: Session,
) -> None:
    """`term` is unique only among *active* designations
    (`ix_designation_no_duplicate_active_term`), so a term added, retired,
    re-added and retired again leaves rows sharing it - the exact case that
    left `load_designations_any_status`'s `ORDER BY` with no total order, and
    the reason it adds `status` (active before retired) and `id` as
    tiebreakers.

    Two things a weaker version of this test got wrong (issue #239 review,
    round 2 - verified by reverting the `ORDER BY` fix and running this test
    against it, which then passed 4 of 6 runs):

    - **The active row's term must not already sort first alphabetically.**
      `"Active synonym"` sorts before `"FBC"` on `term` alone, so a query
      with no `status` in its `ORDER BY` at all would still put the active
      row first - the assertion could not fail pre-fix. `"Zebra panel"`
      sorts *after* `"FBC"`, so the active-first assertion only passes
      because `status` actually leads the sort.
    - **Two retired rows sharing a comparison key is a coin flip, not a
      guard.** `Designation.id` is `gen_random_uuid()`-backed, so an
      unordered pair lands in ascending `id` order about half the time by
      chance. Four retired rows cuts that to 1 in 24 (`4!`), a much smaller
      chance of a false pass.

    Asserted against a Python-side sort by `id`, not a fixed expected order:
    `Designation.id` is a UUID (issue #224/FR-06's precedent - never an
    integer a test could predict), so the guarantee under test is that the
    query's own order is deterministic and matches ascending `id` among
    otherwise-identical rows, not that it matches insertion order. Postgres
    orders `uuid` by byte comparison, which agrees with Python's
    `UUID.__lt__`, so the two sorts genuinely match."""
    entry = _new_entry(app_session)
    active = add_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        term="Zebra panel",
        reason="Adding an active synonym that sorts after FBC",
    )
    app_session.flush()

    retired_ids: list[uuid.UUID] = []
    for cycle in range(4):
        retired = add_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            term="FBC",
            reason=f"Adding FBC synonym, cycle {cycle}",
        )
        app_session.flush()
        retire_designation(
            app_session,
            AuditContext.system(),
            designation=retired,
            reason=f"Retiring FBC synonym, cycle {cycle}",
        )
        app_session.flush()
        retired_ids.append(retired.id)

    rows = queries.load_designations_any_status(app_session, (entry.id,))

    assert [row.status for row in rows] == ["active"] + ["retired"] * 4
    assert rows[0].id == active.id

    returned_retired_ids = [row.id for row in rows[1:]]
    assert returned_retired_ids == sorted(returned_retired_ids), (
        "retired rows must be in ascending id order"
    )
    assert set(returned_retired_ids) == set(retired_ids)


# --- issue #224: the load/amend surface #149's edit screen needs ----------


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_load_active_designation_resolves_by_term(app_session: Session) -> None:
    entry = _new_entry(app_session)
    created = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()

    found = load_active_designation(app_session, entry_id=entry.id, term="FBC")

    assert found.id == created.id


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_load_active_designation_resolves_a_case_and_punctuation_variant(
    app_session: Session,
) -> None:
    """`load_active_designation` looks up by comparison key, not the raw
    term - a caller naming a variant that folds to the same key as the
    stored term (issue #49's own comparison fold) still resolves it."""
    entry = _new_entry(app_session)
    created = add_designation(
        app_session, AuditContext.system(), entry=entry, term="17-OHP", reason="Adding synonym"
    )
    app_session.flush()

    found = load_active_designation(app_session, entry_id=entry.id, term="17 ohp")

    assert found.id == created.id


@pytest.mark.integration
def test_load_active_designation_raises_when_no_active_match(app_session: Session) -> None:
    entry = _new_entry(app_session)

    with pytest.raises(DesignationNotFoundError):
        load_active_designation(app_session, entry_id=entry.id, term="No such term")


@pytest.mark.integration
def test_load_active_designation_does_not_resolve_a_retired_term(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=designation, reason="Retiring FBC synonym"
    )
    app_session.flush()

    with pytest.raises(DesignationNotFoundError):
        load_active_designation(app_session, entry_id=entry.id, term="FBC")


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_amend_designation_edits_the_term_in_place(app_session: Session) -> None:
    """Edits the row rather than retiring and re-adding: the `id` and the
    audit trail show one edit, not an unrelated-looking retire+create
    pair (issue #224)."""
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    amended = amend_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        designation=designation,
        new_term="Full Blood Count",
        reason="Correcting the synonym",
    )
    app_session.flush()

    assert amended.id == designation.id
    assert amended.term == "Full Blood Count"
    assert _audit_event_count(app_session) == before + 1


@pytest.mark.integration
def test_amend_designation_to_the_same_term_is_a_no_op(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    amend_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        designation=designation,
        new_term="FBC",
        reason="Re-saving the same synonym",
    )

    assert _audit_event_count(app_session) == before


@pytest.mark.integration
def test_amend_a_retired_designation_is_refused(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=designation, reason="Retiring FBC synonym"
    )
    app_session.flush()

    with pytest.raises(DesignationAlreadyRetiredError):
        amend_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            designation=designation,
            new_term="Full Blood Count",
            reason="Correcting a retired synonym",
        )


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_amend_designation_still_runs_the_error_collision_check(app_session: Session) -> None:
    """Amending is not exempt from FR-05: renaming a synonym to match
    another live entry's preferred term is exactly the same ordering
    hazard as adding it fresh."""
    other_entry = _new_entry(app_session, preferred_term="Adrenal Ab")
    entry = _new_entry(app_session, preferred_term="21-Hydroxylase Ab")
    designation = add_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        term="Some other synonym",
        reason="Adding an unrelated synonym",
    )
    app_session.flush()

    with pytest.raises(DesignationCollisionError):
        amend_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            designation=designation,
            new_term="Adrenal Ab",
            reason="Renaming to the colliding term",
        )

    assert other_entry.preferred_term == "Adrenal Ab"


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_amend_designation_onto_another_active_term_on_the_same_entry_is_refused(
    app_session: Session,
) -> None:
    """The within-entry duplicate case (`ix_designation_no_duplicate_
    active_term`), same as `test_duplicate_active_synonym_is_refused_by_
    the_partial_unique` above but reached via an amendment rather than a
    second add."""
    entry = _new_entry(app_session)
    add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC"
    )
    app_session.flush()
    other = add_designation(
        app_session, AuditContext.system(), entry=entry, term="CBC", reason="Adding CBC"
    )
    app_session.flush()

    with pytest.raises(DuplicateActiveTermError):
        amend_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            designation=other,
            new_term="FBC",
            reason="Renaming CBC onto the existing FBC synonym",
        )


# --- issue #313: reinstating a retired designation ---------------------


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_find_retired_designation_picks_the_most_recently_retired_row(
    app_session: Session,
) -> None:
    """Two retired rows can share `(entry_id, term_key)` - a term
    added, retired, and re-added twice over
    (`ix_designation_no_duplicate_active_term` is active-only) - so this
    pins `retired_at DESC` as the tie-break, not insertion or `id` order.

    `retired_at` is set explicitly on the older row after retiring it,
    rather than trusting `retire_designation`'s own `func.now()` for both
    rows: Postgres's `now()` is the *transaction* start time, not the
    statement time, so two retirements in one transaction would otherwise
    tie and fall straight through to the `id` tiebreaker, proving nothing
    about `retired_at` itself - the identical hazard `public_catalogue_
    support.py`'s own `code_binding` seed fixture works around for FR-17's
    other table."""
    entry = _new_entry(app_session)
    older = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC, cycle 1"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=older, reason="Retiring FBC, cycle 1"
    )
    older.retired_at = datetime.now(UTC) - timedelta(hours=1)
    app_session.flush()

    newer = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC, cycle 2"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=newer, reason="Retiring FBC, cycle 2"
    )
    app_session.flush()

    found = find_retired_designation(app_session, entry_id=entry.id, term="FBC")

    assert found is not None
    assert found.id == newer.id


@pytest.mark.integration
def test_load_retired_designation_raises_when_no_retired_match(app_session: Session) -> None:
    entry = _new_entry(app_session)
    add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()

    with pytest.raises(DesignationNotFoundError):
        load_retired_designation(app_session, entry_id=entry.id, term="FBC")


@pytest.mark.integration
def test_reinstate_designation_restores_the_same_row(app_session: Session) -> None:
    """The whole point of #313: the reinstated row keeps its `id`, so
    `nptc.catalogue.history.load_history` reads one continuous record
    across create, retire and reinstate - not a retirement paired with an
    unrelated-looking new row, the way re-adding the term today does."""
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=designation, reason="Retiring by mistake"
    )
    app_session.flush()

    reinstated = reinstate_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        designation=designation,
        reason="Reinstating the FBC synonym",
    )
    app_session.flush()

    assert reinstated.id == designation.id
    assert reinstated.status == str(DesignationStatus.ACTIVE)
    assert reinstated.retired_at is None


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_reinstate_designation_records_one_audit_event(app_session: Session) -> None:
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=designation, reason="Retiring by mistake"
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    reinstate_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        designation=designation,
        reason="Reinstating the FBC synonym",
    )
    app_session.flush()

    event = latest_audit_event(app_session, entity_type="designation", entity_id=designation.id)
    assert _audit_event_count(app_session) == before + 1
    assert event.action == "designation.reinstated"
    assert event.before == {"status": "retired"}
    assert event.after == {"status": "active"}


@pytest.mark.integration
def test_reinstating_an_active_designation_is_refused(app_session: Session) -> None:
    """`reinstate_designation`'s own guard, mirroring `retire_designation`'s
    already-retired guard - reinstating a row that is already active would
    otherwise reach `record_change` with an empty diff."""
    entry = _new_entry(app_session)
    designation = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC synonym"
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    with pytest.raises(DesignationNotRetiredError):
        reinstate_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            designation=designation,
            reason="Reinstating an already-active synonym",
        )

    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_reinstate_designation_still_runs_the_error_collision_check(app_session: Session) -> None:
    """Reinstating is not exempt from FR-05: a synonym can be retired, and
    only *afterwards* does another live entry claim the same term as its
    own preferred term (issue #313's own "collided while retired"
    scenario) - the designation above could not have been added with this
    term already colliding, since `add_designation`'s own FR-05 check
    would have refused it outright."""
    entry = _new_entry(app_session, preferred_term="21-Hydroxylase Ab")
    designation = add_designation(
        app_session,
        AuditContext.system(),
        entry=entry,
        term="Adrenal Ab",
        reason="Adding a synonym that does not collide yet",
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=designation, reason="Retiring it"
    )
    app_session.flush()
    _new_entry(app_session, preferred_term="Adrenal Ab")

    with pytest.raises(DesignationCollisionError):
        reinstate_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            designation=designation,
            reason="Reinstating the now-colliding synonym",
        )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_reinstating_onto_an_active_duplicate_on_the_same_entry_is_refused(
    app_session: Session,
) -> None:
    """The within-entry duplicate case (`ix_designation_no_duplicate_
    active_term`) - a term retired and then re-added creates a **new**
    active row sharing the retired row's `term_key` (issue #313's own
    motivating scenario), so reinstating the *original* row would produce
    two active rows for the same term. `assert_no_error_collisions` never
    sees this (it excludes this entry from its own comparison), so this is
    the partial unique index's own `IntegrityError`, translated exactly as
    `add_designation`'s own duplicate-add case is."""
    entry = _new_entry(app_session)
    original = add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Adding FBC"
    )
    app_session.flush()
    retire_designation(
        app_session, AuditContext.system(), designation=original, reason="Retiring FBC by mistake"
    )
    app_session.flush()
    add_designation(
        app_session, AuditContext.system(), entry=entry, term="FBC", reason="Re-adding FBC"
    )
    app_session.flush()

    with pytest.raises(DuplicateActiveTermError):
        reinstate_designation(
            app_session,
            AuditContext.system(),
            entry=entry,
            designation=original,
            reason="Reinstating the original FBC synonym",
        )
