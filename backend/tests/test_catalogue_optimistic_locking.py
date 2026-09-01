"""FR-38 optimistic locking tests (issue #46, NFR-38 test 8): a stale
`row_version` is rejected, the caller is shown the conflicting changes,
and a rejected save never leaves an audit event behind.

Uses an ORM `Session` bound to `app_db` - see
`test_catalogue_business_key.py`'s own module docstring for why.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import (
    EntryChanges,
    create_entry,
    format_business_key,
    save_entries,
    save_entry,
)
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
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Original term",
        reason="Created for FR-38 test",
    )
    assert entry.row_version == 1

    updated = save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=1,
        changes=EntryChanges(preferred_term="Updated term"),
        reason="Renamed the entry",
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
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Original term",
        reason="Created for FR-38 test",
    )

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
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Same term",
        reason="Created for FR-38 test",
    )

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


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_no_op_save_writes_nothing_and_does_not_move_the_row_version(
    app_session: Session,
) -> None:
    """Resubmitting the values an entry already holds is not a change, and
    must not be recorded as one: `record_change` raises `AuditNoOpError` on
    an empty diff, so without the short-circuit this was an unmapped error
    rather than a quiet success (found reviewing issue #227's own new HTTP
    route, where re-saving an unchanged edit form reaches exactly here).

    `row_version` must not move either. Bumping it for a no-op would
    invalidate a *concurrent* editor's own still-current token over a change
    that never happened - the same reasoning
    `save_property_values` records for its own no-op check."""
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Full blood count",
        reason="Created for the no-op save test",
    )
    app_session.flush()
    before = _audit_event_count(app_session)

    saved = save_entry(
        app_session,
        AuditContext.system(),
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        # A trailing normalisable space (PRD Appendix A.1): `clean_term`
        # collapses it on assignment, so this is the stored value already -
        # comparing the raw submitted string would miss it.
        changes=EntryChanges(preferred_term="Full blood count "),
        reason="Re-saving the form without having changed anything",
    )

    assert saved.preferred_term == "Full blood count"
    assert saved.row_version == 1
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_a_rejected_save_writes_no_audit_event_and_leaves_the_entry_untouched(
    app_session: Session,
) -> None:
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Original term",
        reason="Created for FR-38 test",
    )
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
    entry = create_entry(
        app_session,
        AuditContext.system(),
        preferred_term="Original term",
        reason="Created for FR-38 test",
    )
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
        create_entry(
            app_session,
            AuditContext.system(),
            preferred_term=f"Entry {i}",
            reason="Created for FR-38 bulk test",
        )
        for i in range(3)
    ]
    events_before = _audit_event_count(app_session)

    updates = [
        (entry.business_key, entry.row_version, EntryChanges(preferred_term=f"Renamed {i}"))
        for i, entry in enumerate(entries)
    ]
    saved = save_entries(
        app_session, AuditContext.system(), updates=updates, reason="Bulk reclassify test"
    )

    assert [entry.row_version for entry in saved] == [2, 2, 2]
    assert _audit_event_count(app_session) == events_before + 3


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_entries_stale_middle_entry_does_not_bump_the_first(app_session: Session) -> None:
    entries = [
        create_entry(
            app_session,
            AuditContext.system(),
            preferred_term=f"Entry {i}",
            reason="Created for FR-38 bulk test",
        )
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
        save_entries(
            app_session, AuditContext.system(), updates=updates, reason="Bulk reclassify test"
        )

    first = app_session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == entries[0].business_key)
    ).scalar_one()
    assert first.row_version == 2
    assert first.preferred_term == "Renamed 0"


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_version_id_col_backstop_catches_a_genuine_concurrent_race(
    app_engine: Engine, owner_engine: Engine
) -> None:
    """Exercises the *second* conflict-detection layer directly - the one
    `save_entry`'s explicit precondition check never reaches, because both
    callers pass it.

    `app_session`'s per-test rolled-back transaction (used by every other
    test in this module) cannot produce a genuine two-transaction race: a
    write only another connection can see must actually be committed, and
    a real second connection would need `_load_for_update`'s own SELECT to
    run *before* that commit but the flush to run *after* it - an
    ordering plain sequential test code cannot express on its own. An
    `after_cursor_execute` hook on `app_engine` supplies that ordering
    deterministically: it fires the instant `_load_for_update`'s SELECT
    has executed (and, under READ COMMITTED, already fixed its result set)
    but before `save_entry` ever inspects the row it returned, then
    performs and commits the concurrent write from a genuinely separate
    connection. The precondition check therefore still sees the *pre-race*
    `row_version=1` and passes; the later flush's own versioned `UPDATE`
    is what collides with the now-committed `row_version=2`.

    Setup and teardown use plain SQL, never `create_entry`/`save_entry`,
    so this test produces zero `audit_event` rows of its own and cannot
    disturb the hash chain or any other test's exact-content assertions
    against the shared container. Only `catalogue_entry` needs cleanup
    afterwards, via `owner_engine` - the only role with the DELETE
    privilege `nptc_app` is deliberately refused (FR-03)."""
    business_key = format_business_key(999_000_001)
    with app_engine.connect() as setup_connection:
        entry_id = setup_connection.execute(
            text(
                "INSERT INTO catalogue_entry (business_key, preferred_term) "
                "VALUES (:key, 'Original term') RETURNING id"
            ),
            {"key": business_key},
        ).scalar_one()
        setup_connection.commit()

    fired = False

    def _inject_concurrent_write(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal fired
        if (
            fired
            or "catalogue_entry" not in statement
            or not statement.strip().upper().startswith("SELECT")
        ):
            return
        fired = True
        with app_engine.connect() as other_connection:
            other_connection.execute(
                text(
                    "UPDATE catalogue_entry SET row_version = row_version + 1, "
                    "preferred_term = 'Raced ahead' WHERE business_key = :key"
                ),
                {"key": business_key},
            )
            other_connection.commit()

    try:
        event.listen(app_engine, "after_cursor_execute", _inject_concurrent_write)
        race_session = Session(bind=app_engine)
        try:
            with pytest.raises(EntryVersionConflictError) as exc_info:
                save_entry(
                    race_session,
                    AuditContext.system(),
                    business_key=business_key,
                    expected_row_version=1,
                    changes=EntryChanges(preferred_term="Second editor's change"),
                    reason="raced save",
                )
        finally:
            race_session.close()
            event.remove(app_engine, "after_cursor_execute", _inject_concurrent_write)

        assert fired, "the injected concurrent write never ran - the test proves nothing"

        report = exc_info.value.report
        assert report.expected_row_version == 1
        assert report.current_row_version == 2
        assert any(
            c.field == "preferred_term" and c.current == "Raced ahead" for c in report.conflicts
        )

        with owner_engine.connect() as check_connection:
            audit_count = check_connection.execute(
                text(
                    "SELECT count(*) FROM audit_event "
                    "WHERE entity_type = 'catalogue_entry' AND entity_id = :entity_id"
                ),
                {"entity_id": str(entry_id)},
            ).scalar_one()
            assert audit_count == 0
    finally:
        with owner_engine.connect() as cleanup_connection:
            cleanup_connection.execute(
                text("DELETE FROM catalogue_entry WHERE business_key = :key"),
                {"key": business_key},
            )
            cleanup_connection.commit()
