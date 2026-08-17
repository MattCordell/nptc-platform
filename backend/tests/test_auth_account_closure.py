"""`close_account` tests (issue #42, NFR-17): pseudonymise, never delete.

`Session(bind=app_db)` joins the existing testcontainers fixture
connection - see test_auth_identity_resolution.py's module docstring for
why.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.hashing import GENESIS_HASH
from nptc.audit.writer import AuditContext
from nptc.auth.identity import close_account
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity

# A fixed, valid-shape (64 lowercase hex) placeholder pair for prev_hash/
# entry_hash - this test is about the byte-for-byte-unchanged assertion
# below, not the hash chain itself (that is test_audit_chain.py/
# test_audit_tamper_detection.py's job), so the literal need not satisfy
# the real digest, only the NOT NULL + CHECK constraints. GENESIS_HASH
# reused for prev_hash; a distinct literal for entry_hash so it never
# collides with a real chain's own genesis-successor hash in the same
# rolled-back transaction.
_PLACEHOLDER_ENTRY_HASH = "e" * 64
_INSERT_AUDIT_EVENT = text(
    "INSERT INTO audit_event "
    "(actor_user_id, correlation_id, action, entity_type, entity_id, prev_hash, entry_hash) "
    "VALUES "
    "(:actor_user_id, :correlation_id, :action, :entity_type, :entity_id, :prev_hash, :entry_hash)"
)


def _create_active_user(session: Session, username: str) -> User:
    user = User(username=username, display_name=username.title(), organisation="RCPA-QAP")
    session.add(user)
    session.flush()
    session.add(
        UserIdentity(user_id=user.id, issuer="https://idp.example", subject=f"sub-{username}")
    )
    session.flush()
    return user


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_close_account_nulls_identifying_fields_and_retains_the_uuid(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "alice")
    original_id = user.id

    close_account(session, original_id, AuditContext.system())
    session.flush()
    session.expire_all()

    closed = session.get(User, original_id)
    assert closed is not None
    assert closed.id == original_id
    assert closed.username is None
    assert closed.display_name is None
    assert closed.organisation is None
    assert closed.status == UserStatus.CLOSED
    assert closed.closed_at is not None


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_close_account_deletes_every_user_identity_row(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "bob")

    close_account(session, user.id, AuditContext.system())
    session.flush()

    remaining = (
        session.execute(
            text("SELECT count(*) FROM user_identity WHERE user_id = :id"), {"id": user.id}
        )
    ).scalar_one()
    assert remaining == 0


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_close_account_does_not_delete_the_user_row(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "carol")

    close_account(session, user.id, AuditContext.system())
    session.flush()

    remaining = session.execute(
        text("SELECT count(*) FROM app_user WHERE id = :id"), {"id": user.id}
    ).scalar_one()
    assert remaining == 1


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_close_account_is_idempotent(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "dave")

    close_account(session, user.id, AuditContext.system())
    session.flush()
    session.expire_all()
    first_closed_at = session.get(User, user.id).closed_at  # type: ignore[union-attr]

    close_account(session, user.id, AuditContext.system())
    session.flush()
    session.expire_all()
    second_closed_at = session.get(User, user.id).closed_at  # type: ignore[union-attr]

    assert first_closed_at == second_closed_at


@pytest.mark.req("NFR-13")
@pytest.mark.integration
def test_audit_rows_for_a_closed_account_are_byte_for_byte_unchanged(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "erin")
    session.execute(
        _INSERT_AUDIT_EVENT,
        {
            "actor_user_id": user.id,
            "correlation_id": str(uuid.uuid4()),
            "action": "test.action",
            "entity_type": "test_entity",
            "entity_id": "1",
            "prev_hash": GENESIS_HASH,
            "entry_hash": _PLACEHOLDER_ENTRY_HASH,
        },
    )
    session.flush()
    before = (
        session.execute(
            text("SELECT * FROM audit_event WHERE actor_user_id = :id"), {"id": user.id}
        )
        .mappings()
        .one()
    )

    # close_account's own `user.closed` event (below) is attributed to no
    # actor (AuditContext.system()), so it never matches this WHERE clause
    # and cannot interfere with the byte-for-byte comparison.
    close_account(session, user.id, AuditContext.system())
    session.flush()

    after = (
        session.execute(
            text("SELECT * FROM audit_event WHERE actor_user_id = :id"), {"id": user.id}
        )
        .mappings()
        .one()
    )
    assert dict(before) == dict(after)


@pytest.mark.req("NFR-08")
@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_close_account_emits_a_user_closed_audit_event(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "frank")
    user_id = user.id

    close_account(session, user_id, AuditContext.system())
    session.flush()

    row = (
        session.execute(
            text(
                "SELECT action, entity_type, entity_id, before, after "
                "FROM audit_event WHERE entity_id = :entity_id AND action = 'user.closed'"
            ),
            {"entity_id": str(user_id)},
        )
        .mappings()
        .one()
    )

    assert row["entity_type"] == "app_user"
    assert row["before"] == {
        "username": "frank",
        "display_name": "Frank",
        "organisation": "RCPA-QAP",
        "status": "active",
    }
    assert row["after"] == {
        "username": None,
        "display_name": None,
        "organisation": None,
        "status": "closed",
    }
    # NFR-26/NFR-35: the identifying values themselves never appear in
    # `after`, only that they are now null.
    assert "frank" not in str(row["after"])


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_close_account_is_idempotent_and_emits_no_second_event(app_db: Connection) -> None:
    session = Session(bind=app_db)
    user = _create_active_user(session, "grace")
    user_id = user.id

    close_account(session, user_id, AuditContext.system())
    session.flush()
    close_account(session, user_id, AuditContext.system())
    session.flush()

    count = session.execute(
        text(
            "SELECT count(*) FROM audit_event WHERE entity_id = :entity_id AND action = 'user.closed'"
        ),
        {"entity_id": str(user_id)},
    ).scalar_one()
    assert count == 1
