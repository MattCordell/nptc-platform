"""Tests for `nptc.audit.queries.search_audit_events` (issue #286, NFR-12).

The four filters (actor, entity, action, date range), independently and
combined; keyset pagination stable under a concurrent append (issue #190);
and attribution for a closed account (NFR-13, NFR-17) and a system event.

Cursor/filter *validation* is offline (no `@pytest.mark.integration`):
`search_audit_events` raises `MalformedAuditCursorError`/`AuditFilterError`
before ever calling `session.execute` - see those errors' own docstrings -
so a bare, unbound `Session()` proves it without touching a database.

Every other test scopes its assertions to a `User`/`entity_type` this test
itself created (`AuditEventFilter.actor_user_id` and/or a UUID-suffixed
`entity_type`), never a whole-table count - `audit_event` is one
session-scoped table shared by the whole run (CLAUDE.md, issue #190).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.queries import (
    AuditEventFilter,
    AuditFilterError,
    MalformedAuditCursorError,
    search_audit_events,
)
from nptc.audit.writer import AuditContext, append_audit_event
from nptc.auth.identity import close_account
from nptc.db.models.user import User

# --- offline: validation errors raised before any query runs --------------


def test_cursor_beyond_bigint_range_is_refused() -> None:
    with pytest.raises(MalformedAuditCursorError):
        search_audit_events(Session(), AuditEventFilter(), limit=50, before=2**63)


def test_entity_id_without_entity_type_is_refused() -> None:
    """`entity_id` alone cannot use `ix_audit_event_entity_type_entity_id_
    sequence` and cannot mean anything on its own - `entity_id` is not
    unique across entity types (see `nptc.catalogue.history`'s own
    `property_value_set` composite-key precedent)."""
    with pytest.raises(AuditFilterError):
        search_audit_events(Session(), AuditEventFilter(entity_id="some-id"), limit=50)


def test_occurred_from_after_occurred_to_is_refused() -> None:
    later = datetime.now(UTC)
    earlier = later - timedelta(hours=1)
    with pytest.raises(AuditFilterError):
        search_audit_events(
            Session(),
            AuditEventFilter(occurred_from=later, occurred_to=earlier),
            limit=50,
        )


# --- integration: real filtering, pagination, and attribution -------------


def _create_active_user(session: Session, username: str) -> User:
    user = User(username=username, display_name=username.title(), organisation="RCPA-QAP")
    session.add(user)
    session.flush()
    return user


def _seed(
    session: Session,
    *,
    actor_user_id: uuid.UUID | None,
    entity_type: str,
    entity_id: str = "1",
    action: str = "test.action",
) -> int:
    event = append_audit_event(
        session,
        AuditContext(
            actor_user_id=actor_user_id,
            actor_ip=None,
            user_agent=None,
            correlation_id=uuid.uuid4(),
        )
        if actor_user_id is not None
        else AuditContext.system(),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    session.flush()
    return event.sequence


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_filter_by_actor_narrows_the_result_set(app_db: Connection) -> None:
    session = Session(bind=app_db)
    entity_type = f"test-actor-{uuid.uuid4()}"
    alice = _create_active_user(session, "alice-audit-search")
    bob = _create_active_user(session, "bob-audit-search")
    _seed(session, actor_user_id=alice.id, entity_type=entity_type)
    _seed(session, actor_user_id=bob.id, entity_type=entity_type)

    page = search_audit_events(
        session, AuditEventFilter(actor_user_id=alice.id, entity_type=entity_type), limit=50
    )

    assert len(page.events) == 1
    assert page.events[0].actor is not None
    assert page.events[0].actor.id == alice.id


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_filter_by_entity_narrows_the_result_set(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "carol-audit-search")
    entity_type = f"test-entity-{uuid.uuid4()}"
    _seed(session, actor_user_id=user.id, entity_type=entity_type, entity_id="one")
    _seed(session, actor_user_id=user.id, entity_type=entity_type, entity_id="two")

    page = search_audit_events(
        session,
        AuditEventFilter(entity_type=entity_type, entity_id="one"),
        limit=50,
    )

    assert len(page.events) == 1
    assert page.events[0].entity_id == "one"


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_filter_by_action_narrows_the_result_set(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "dana-audit-search")
    entity_type = f"test-action-{uuid.uuid4()}"
    _seed(session, actor_user_id=user.id, entity_type=entity_type, action="test.created")
    _seed(session, actor_user_id=user.id, entity_type=entity_type, action="test.updated")

    page = search_audit_events(
        session,
        AuditEventFilter(entity_type=entity_type, action="test.created"),
        limit=50,
    )

    assert len(page.events) == 1
    assert page.events[0].action == "test.created"


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_filter_by_occurred_range_narrows_the_result_set(app_db: Connection) -> None:
    """The range is half-open `[from, to)` - see `AuditEventFilter`'s own
    docstring. `occurred_at` is set by the database clock
    (`nptc.audit.writer.append_audit_event`), never the test, so this reads
    the two real timestamps back rather than asserting against ones it
    picked itself."""
    session = Session(bind=app_db)
    user = _create_active_user(session, "erin-audit-search")
    entity_type = f"test-range-{uuid.uuid4()}"
    first_sequence = _seed(session, actor_user_id=user.id, entity_type=entity_type)
    second_sequence = _seed(session, actor_user_id=user.id, entity_type=entity_type)
    first = next(
        e
        for e in search_audit_events(
            session, AuditEventFilter(entity_type=entity_type), limit=50
        ).events
        if e.sequence == first_sequence
    )
    second = next(
        e
        for e in search_audit_events(
            session, AuditEventFilter(entity_type=entity_type), limit=50
        ).events
        if e.sequence == second_sequence
    )
    assert second.occurred_at > first.occurred_at

    page = search_audit_events(
        session,
        AuditEventFilter(
            entity_type=entity_type,
            occurred_from=first.occurred_at,
            occurred_to=second.occurred_at,
        ),
        limit=50,
    )

    assert [e.sequence for e in page.events] == [first_sequence]


@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_filters_combine_with_and(app_db: Connection) -> None:
    session = Session(bind=app_db)
    entity_type = f"test-combine-{uuid.uuid4()}"
    alice = _create_active_user(session, "frank-audit-search")
    bob = _create_active_user(session, "grace-audit-search")
    _seed(session, actor_user_id=alice.id, entity_type=entity_type, action="test.created")
    _seed(session, actor_user_id=alice.id, entity_type=entity_type, action="test.updated")
    _seed(session, actor_user_id=bob.id, entity_type=entity_type, action="test.created")

    page = search_audit_events(
        session,
        AuditEventFilter(actor_user_id=alice.id, entity_type=entity_type, action="test.created"),
        limit=50,
    )

    assert len(page.events) == 1
    assert page.events[0].action == "test.created"
    assert page.events[0].actor is not None
    assert page.events[0].actor.id == alice.id


@pytest.mark.integration
def test_pagination_is_stable_under_a_concurrent_append(app_db: Connection) -> None:
    """Issue #190: a page already served must not change once a later
    event is appended. `sequence` is a globally monotonic identity column,
    so the second page's `sequence < before` predicate excludes anything
    appended after the first page was fetched, regardless of insertion
    order - see `nptc.audit.queries`' own module docstring."""
    session = Session(bind=app_db)
    user = _create_active_user(session, "henry-audit-search")
    entity_type = f"test-paging-{uuid.uuid4()}"
    oldest = _seed(session, actor_user_id=user.id, entity_type=entity_type)
    newest = _seed(session, actor_user_id=user.id, entity_type=entity_type)

    first_page = search_audit_events(session, AuditEventFilter(entity_type=entity_type), limit=1)
    assert [e.sequence for e in first_page.events] == [newest]
    assert first_page.next_cursor == str(newest)

    # A concurrent append, after the first page was already served.
    concurrent = _seed(session, actor_user_id=user.id, entity_type=entity_type)
    assert concurrent > newest

    second_page = search_audit_events(
        session,
        AuditEventFilter(entity_type=entity_type),
        limit=1,
        before=int(first_page.next_cursor),
    )

    assert [e.sequence for e in second_page.events] == [oldest]
    assert second_page.next_cursor is None


@pytest.mark.req("NFR-13")
@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_a_closed_accounts_actor_resolves_by_id_not_display_name(app_db: Connection) -> None:
    """A closed account is a tombstone (`User`'s own `tombstone` CHECK):
    `display_name` is `NULL`. The row must still resolve to the actor's
    internal UUID with `is_closed=True`, not go blank - matching
    `nptc.catalogue.history`'s own precedent of never dropping attribution
    to a pseudonymised account."""
    session = Session(bind=app_db)
    user = _create_active_user(session, "iris-audit-search")
    entity_type = f"test-closed-{uuid.uuid4()}"
    sequence = _seed(session, actor_user_id=user.id, entity_type=entity_type)
    close_account(session, user.id, AuditContext.system())
    session.flush()

    page = search_audit_events(session, AuditEventFilter(entity_type=entity_type), limit=50)

    event = next(e for e in page.events if e.sequence == sequence)
    assert event.actor is not None
    assert event.actor.id == user.id
    assert event.actor.display_name is None
    assert event.actor.is_closed is True


@pytest.mark.integration
def test_a_system_events_actor_is_none(app_db: Connection) -> None:
    session = Session(bind=app_db)
    entity_type = f"test-system-{uuid.uuid4()}"
    sequence = _seed(session, actor_user_id=None, entity_type=entity_type)

    page = search_audit_events(session, AuditEventFilter(entity_type=entity_type), limit=50)

    event = next(e for e in page.events if e.sequence == sequence)
    assert event.actor is None


@pytest.mark.req("NFR-26")
@pytest.mark.req("NFR-35")
@pytest.mark.integration
def test_before_after_are_served_exactly_as_stored(app_db: Connection) -> None:
    """NFR-12's administrator surface serves the raw JSONB verbatim - see
    `nptc.audit.queries`' own module docstring for why that is safe here.
    A field already withheld under `REDACTED_KEY` at write time (issue #37)
    is served under that same key, never a value - proving this module
    adds no second redaction step and removes none either."""
    session = Session(bind=app_db)
    user = _create_active_user(session, "jack-audit-search")
    entity_type = f"test-redaction-{uuid.uuid4()}"
    event = append_audit_event(
        session,
        AuditContext(
            actor_user_id=user.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
        ),
        action="test.updated",
        entity_type=entity_type,
        entity_id="1",
        before={"status": "active", "_redacted": ["display_name"]},
        after={"status": "suspended", "_redacted": ["display_name"]},
    )
    session.flush()

    page = search_audit_events(session, AuditEventFilter(entity_type=entity_type), limit=50)

    served = next(e for e in page.events if e.sequence == event.sequence)
    assert served.before == {"status": "active", "_redacted": ["display_name"]}
    assert served.after == {"status": "suspended", "_redacted": ["display_name"]}
