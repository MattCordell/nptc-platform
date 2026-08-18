"""Integration tests for nptc.audit.recording against a real Postgres
database (issue #37, NFR-08). `Session(bind=app_db)` joins the existing
testcontainers fixture connection - see test_auth_identity_resolution.py's
module docstring for why.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import AuditFieldPolicy
from nptc.audit.recording import AuditNoOpError, record_change, record_snapshot_change
from nptc.audit.writer import AuditContext
from nptc.db.models.user import User, UserStatus
from nptc_shared.sctid import SCTID


def _create_active_user(session: Session, username: str) -> User:
    user = User(username=username, display_name=username.title(), organisation="RCPA-QAP")
    session.add(user)
    session.flush()
    return user


def _audit_event_count(session: Session, entity_id: str, action: str) -> int:
    return session.execute(
        text("SELECT count(*) FROM audit_event WHERE entity_id = :id AND action = :action"),
        {"id": entity_id, "action": action},
    ).scalar_one()


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_single_field_update_emits_one_event_with_a_field_level_diff(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "harriet")
    user_id = user.id

    user.status = UserStatus.SUSPENDED
    record_change(
        session,
        AuditContext.system(),
        action="user.status_changed",
        instance=user,
        kind=ChangeKind.UPDATED,
    )
    session.flush()

    row = (
        session.execute(
            text(
                "SELECT before, after FROM audit_event "
                "WHERE entity_id = :id AND action = 'user.status_changed'"
            ),
            {"id": str(user_id)},
        )
        .mappings()
        .one()
    )
    assert row["before"] == {"status": "active"}
    assert row["after"] == {"status": "suspended"}
    assert _audit_event_count(session, str(user_id), "user.status_changed") == 1


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_created_user_emits_a_full_snapshot_via_record_change(app_db: Connection) -> None:
    """The CREATED success path: `record_change` must flush a pending
    instance itself before diffing/resolving `entity_id` - otherwise the
    not-yet-assigned primary key can't be derived, and server-default
    columns (`User.status`) would still read as `None` on the Python
    side."""
    session = Session(bind=app_db)
    user = User(username="kelly", display_name="Kelly", organisation="RCPA-QAP")
    session.add(user)

    event = record_change(
        session,
        AuditContext.system(),
        action="user.created",
        instance=user,
        kind=ChangeKind.CREATED,
    )
    session.flush()

    assert event.entity_id == str(user.id)
    row = (
        session.execute(
            text(
                "SELECT before, after FROM audit_event "
                "WHERE entity_id = :id AND action = 'user.created'"
            ),
            {"id": str(user.id)},
        )
        .mappings()
        .one()
    )
    assert row["before"] is None
    assert row["after"]["status"] == "active"


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_record_change_created_refuses_an_already_flushed_instance(app_db: Connection) -> None:
    """The `session.new` refusal: calling `record_change(kind=CREATED)`
    after the instance has already left `session.new` (a prior flush, most
    likely from an entirely separate operation) must not silently diff a
    settled row as though it were only just created."""
    session = Session(bind=app_db)
    user = User(username="len", display_name="Len", organisation="RCPA-QAP")
    session.add(user)
    session.flush()

    with pytest.raises(AuditNoOpError):
        record_change(
            session,
            AuditContext.system(),
            action="user.created",
            instance=user,
            kind=ChangeKind.CREATED,
        )


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_record_snapshot_change_refuses_an_empty_diff(app_db: Connection) -> None:
    session = Session(bind=app_db)
    policy = AuditFieldPolicy(
        entity_type="test_widget",
        auditable=frozenset({"score"}),
        withheld=frozenset(),
        ignored=frozenset(),
        known=frozenset({"score"}),
    )

    with pytest.raises(AuditNoOpError):
        record_snapshot_change(
            session,
            AuditContext.system(),
            action="widget.rescored",
            entity_type="test_widget",
            entity_id="1",
            policy=policy,
            before={"score": 1.0},
            after={"score": 1.0},
            kind=ChangeKind.UPDATED,
        )


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_write_that_changes_nothing_emits_no_event(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "ian")
    user_id = user.id

    with pytest.raises(AuditNoOpError):
        record_change(
            session,
            AuditContext.system(),
            action="user.status_changed",
            instance=user,
            kind=ChangeKind.UPDATED,
        )

    assert _audit_event_count(session, str(user_id), "user.status_changed") == 0


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_diff_payload_survives_the_jsonb_round_trip(app_db: Connection) -> None:
    """Drives writer.py's write-time self-check with real diff content
    flowing through record_snapshot_change, rather than a mocked digest -
    catches the `1e2 -> 100.0` class of JSONB normalisation bug a mock
    would hide. A raised `AuditChainWriteError` would fail this test."""
    session = Session(bind=app_db)
    policy = AuditFieldPolicy(
        entity_type="test_widget",
        auditable=frozenset({"score"}),
        withheld=frozenset(),
        ignored=frozenset(),
        known=frozenset({"score"}),
    )

    event = record_snapshot_change(
        session,
        AuditContext.system(),
        action="widget.rescored",
        entity_type="test_widget",
        entity_id="1",
        policy=policy,
        before={"score": 1.0},
        after={"score": 100.0},
        kind=ChangeKind.UPDATED,
    )
    session.flush()

    stored_after = session.execute(
        text("SELECT after FROM audit_event WHERE id = :id"), {"id": event.id}
    ).scalar_one()
    assert stored_after == {"score": 100.0}


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_sctid_in_a_diff_is_stored_as_a_jsonb_string(app_db: Connection) -> None:
    """AC-3: no ORM model carries an SCTID column until #48, so this
    exercises record_snapshot_change with an explicit policy instead."""
    session = Session(bind=app_db)
    policy = AuditFieldPolicy(
        entity_type="test_code_binding",
        auditable=frozenset({"code"}),
        withheld=frozenset(),
        ignored=frozenset(),
        known=frozenset({"code"}),
    )

    record_snapshot_change(
        session,
        AuditContext.system(),
        action="code_binding.created",
        entity_type="test_code_binding",
        entity_id="1",
        policy=policy,
        before=None,
        after={"code": SCTID("873871000168106")},
        kind=ChangeKind.CREATED,
    )
    session.flush()

    result = (
        session.execute(
            text(
                "SELECT jsonb_typeof(after -> 'code') AS kind, after ->> 'code' AS value "
                "FROM audit_event WHERE entity_type = 'test_code_binding' AND entity_id = '1'"
            )
        )
        .mappings()
        .one()
    )
    assert result["kind"] == "string"
    assert result["value"] == "873871000168106"


@pytest.mark.req("NFR-26")
@pytest.mark.integration
def test_a_withheld_field_change_is_recorded_by_name_only(app_db: Connection) -> None:
    """AC-4 in the database: a withheld field that changed is named under
    `_redacted`, and its value appears nowhere in the row."""
    session = Session(bind=app_db)
    user = _create_active_user(session, "june")
    user_id = user.id

    user.display_name = "Juniper"
    record_change(
        session,
        AuditContext.system(),
        action="user.renamed",
        instance=user,
        kind=ChangeKind.UPDATED,
    )
    session.flush()

    row = (
        session.execute(
            text(
                "SELECT before, after FROM audit_event "
                "WHERE entity_id = :id AND action = 'user.renamed'"
            ),
            {"id": str(user_id)},
        )
        .mappings()
        .one()
    )
    assert row["after"]["_redacted"] == ["display_name"]
    assert "Juniper" not in str(dict(row))
    assert "June" not in str(dict(row))
