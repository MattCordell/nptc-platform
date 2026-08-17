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

from nptc.auth.identity import close_account
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity

_INSERT_AUDIT_EVENT = text(
    "INSERT INTO audit_event (actor_user_id, correlation_id, action, entity_type, entity_id) "
    "VALUES (:actor_user_id, :correlation_id, :action, :entity_type, :entity_id)"
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

    close_account(session, original_id)
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

    close_account(session, user.id)
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

    close_account(session, user.id)
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

    close_account(session, user.id)
    session.flush()
    session.expire_all()
    first_closed_at = session.get(User, user.id).closed_at  # type: ignore[union-attr]

    close_account(session, user.id)
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

    close_account(session, user.id)
    session.flush()

    after = (
        session.execute(
            text("SELECT * FROM audit_event WHERE actor_user_id = :id"), {"id": user.id}
        )
        .mappings()
        .one()
    )
    assert dict(before) == dict(after)
