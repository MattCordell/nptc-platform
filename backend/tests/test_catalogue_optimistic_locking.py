"""FR-38 optimistic locking tests (issue #46, NFR-38 test 8): a stale
`row_version` is rejected, the caller is shown the conflicting changes,
and a rejected save never leaves an audit event behind.

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_business_key.py`'s own module docstring for why.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import EntryChanges, create_entry, save_entries, save_entry
from nptc.catalogue.errors import EntryVersionConflictError
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    return Session(bind=app_db, join_transaction_mode="create_savepoint")


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_with_current_row_version_succeeds_and_bumps_it(app_session: Session) -> None:
    entry = create_entry(app_session, AuditContext.system(), preferred_term="Original term")
    assert entry.row_version == 1

    updated = save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=1,
        changes=EntryChanges(preferred_term="Updated term"),
        reason="renamed",
    )

    assert updated.row_version == 2
    assert updated.preferred_term == "Updated term"


@pytest.mark.req("FR-38")
@pytest.mark.req("NFR-38")
@pytest.mark.integration
def test_stale_row_version_is_rejected_and_names_the_conflict(app_session: Session) -> None:
    """NFR-38 mandated test 8: "A concurrent edit with a stale row_version
    is rejected". The caller must also be shown *what* conflicts, not just
    a bare refusal - FR-38's stated rationale."""
    entry = create_entry(app_session, AuditContext.system(), preferred_term="Original term")

    save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=1,
        changes=EntryChanges(preferred_term="First editor's change"),
        reason="first save",
    )

    with pytest.raises(EntryVersionConflictError) as exc_info:
        save_entry(
            app_session,
            AuditContext.system(),
            business_key=entry.business_key,
            expected_row_version=1,  # stale - the row is now at version 2
            changes=EntryChanges(preferred_term="Second editor's change"),
            reason="second save",
        )

    report = exc_info.value.report
    assert report.business_key == entry.business_key
    assert report.expected_row_version == 1
    assert report.current_row_version == 2
    assert len(report.conflicts) == 1
    assert report.conflicts[0].field == "preferred_term"
    assert report.conflicts[0].submitted == "Second editor's change"
    assert report.conflicts[0].current == "First editor's change"


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_a_stale_save_that_would_have_matched_still_reports_zero_conflicts(
    app_session: Session,
) -> None:
    """The version is the contract even if the submitted values happen to
    coincide with the current ones - but the report's `conflicts` must
    stay empty in that case rather than showing a spurious diff."""
    entry = create_entry(app_session, AuditContext.system(), preferred_term="Same term")

    save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=1,
        changes=EntryChanges(specimen_unconstrained=True),
        reason="unrelated change",
    )

    with pytest.raises(EntryVersionConflictError) as exc_info:
        save_entry(
            app_session,
            AuditContext.system(),
            business_key=entry.business_key,
            expected_row_version=1,
            changes=EntryChanges(preferred_term="Same term"),
            reason="no-op-looking change",
        )

    assert exc_info.value.report.conflicts == ()


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_a_rejected_save_writes_no_audit_event_and_leaves_the_entry_untouched(
    app_session: Session,
) -> None:
    entry = create_entry(app_session, AuditContext.system(), preferred_term="Original term")
    save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=1,
        changes=EntryChanges(preferred_term="First editor's change"),
        reason="first save",
    )
    events_before = _audit_event_count(app_session)

    with pytest.raises(EntryVersionConflictError):
        save_entry(
            app_session,
            AuditContext.system(),
            business_key=entry.business_key,
            expected_row_version=1,
            changes=EntryChanges(preferred_term="Second editor's change"),
            reason="second save",
        )

    assert _audit_event_count(app_session) == events_before
    current = app_session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == entry.business_key)
    ).scalar_one()
    assert current.preferred_term == "First editor's change"
    assert current.row_version == 2


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_session_remains_usable_after_a_caught_conflict(app_session: Session) -> None:
    """A failed flush inside an unmanaged nested transaction can leave a
    `Session` unable to issue further statements - the whole point of
    wrapping the version-checked flush in its own savepoint is that a
    caught conflict does not poison the rest of the caller's transaction."""
    entry = create_entry(app_session, AuditContext.system(), preferred_term="Original term")
    save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=1,
        changes=EntryChanges(preferred_term="First editor's change"),
        reason="first save",
    )

    with pytest.raises(EntryVersionConflictError):
        save_entry(
            app_session,
            AuditContext.system(),
            business_key=entry.business_key,
            expected_row_version=1,
            changes=EntryChanges(preferred_term="Second editor's change"),
            reason="second save",
        )

    # The session must still accept a valid, non-conflicting save.
    recovered = save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=2,
        changes=EntryChanges(preferred_term="Recovered change"),
        reason="third save",
    )
    assert recovered.row_version == 3


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_entries_bumps_every_entrys_row_version(app_session: Session) -> None:
    """#63's bulk reclassify is meant to call this seam, not a Core bulk
    `update()` - see `save_entries`'s own docstring."""
    entries = [
        create_entry(app_session, AuditContext.system(), preferred_term=f"Entry {i}")
        for i in range(3)
    ]
    events_before = _audit_event_count(app_session)

    updates = [
        (entry.business_key, entry.row_version, EntryChanges(preferred_term=f"Renamed {i}"))
        for i, entry in enumerate(entries)
    ]
    saved = save_entries(app_session, AuditContext.system(), updates=updates, reason="bulk")

    assert [entry.row_version for entry in saved] == [2, 2, 2]
    assert _audit_event_count(app_session) == events_before + 3


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_entries_stale_middle_entry_does_not_bump_the_first(app_session: Session) -> None:
    entries = [
        create_entry(app_session, AuditContext.system(), preferred_term=f"Entry {i}")
        for i in range(3)
    ]
    # Stale the middle entry out from under the batch before it runs.
    save_entry(
        app_session,
        AuditContext.system(),
        business_key=entries[1].business_key,
        expected_row_version=1,
        changes=EntryChanges(preferred_term="Already changed"),
        reason="pre-empted",
    )

    updates = [
        (entry.business_key, 1, EntryChanges(preferred_term=f"Renamed {i}"))
        for i, entry in enumerate(entries)
    ]

    with pytest.raises(EntryVersionConflictError):
        save_entries(app_session, AuditContext.system(), updates=updates, reason="bulk")

    first = app_session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == entries[0].business_key)
    ).scalar_one()
    assert first.row_version == 2
    assert first.preferred_term == "Renamed 0"
