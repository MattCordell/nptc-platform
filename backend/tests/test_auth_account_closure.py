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
from nptc.auth.errors_authorisation import LastAdministratorError
from nptc.auth.grants import grant_role_unchecked, roles_for_user
from nptc.auth.identity import close_account
from nptc.auth.permissions import Role
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
        "status": "active",
        "closed_at": None,
        "_redacted": ["display_name", "organisation", "username"],
    }
    after = row["after"]
    assert after["status"] == "closed"
    assert after["_redacted"] == ["display_name", "organisation", "username"]
    assert set(after) == {"status", "closed_at", "_redacted"}
    # NFR-26/NFR-35/NFR-16/NFR-17: the identifying values themselves never
    # appear in `before` or `after` - `audit_event` is INSERT/SELECT-only
    # for the app role, so anything written into `before` would be
    # permanent and would defeat the pseudonymisation this event is
    # itself recording. Strengthened to the row's full JSON text, not just
    # before/after: this is the redaction regression test on a real write
    # path (issue #37).
    full_row_text = str(dict(row))
    assert "frank" not in full_row_text
    assert "Frank" not in full_row_text
    assert "RCPA-QAP" not in full_row_text


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_closing_the_last_active_administrators_account_is_refused(app_db: Connection) -> None:
    """The exact bypass FR-01's guard exists to prevent: closure never
    calls `revoke_role`, so without `assert_not_last_administrator` running
    *inside* `close_account` before anything else, closing your own
    account would be the trivial way around "the system MUST prevent
    removal of the last remaining administrator" - simply deleting the
    account instead of revoking the role. Both the `app_user` row and its
    `user_role` grant must be left completely intact."""
    session = Session(bind=app_db)
    user = _create_active_user(session, "hank")
    grant_role_unchecked(
        session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()

    with pytest.raises(LastAdministratorError):
        close_account(session, user.id, AuditContext.system())

    session.expire_all()
    still_active = session.get(User, user.id)
    assert still_active is not None
    assert still_active.status == UserStatus.ACTIVE
    assert still_active.username == "hank"
    assert roles_for_user(session, user.id) == frozenset({Role.ADMINISTRATOR})


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_closing_a_non_last_administrators_account_revokes_their_grants(app_db: Connection) -> None:
    """The companion positive case: closing an administrator's account is
    allowed - and must actually revoke their `user_role` grants, recording
    `user_role.revoked` - when a second active administrator remains, so
    the guard's refusal is genuinely conditional rather than blanket."""
    session = Session(bind=app_db)
    departing = _create_active_user(session, "ivan")
    remaining_admin = _create_active_user(session, "judy")
    for user in (departing, remaining_admin):
        grant_role_unchecked(
            session,
            target_user_id=user.id,
            role=Role.ADMINISTRATOR,
            granted_by_user_id=None,
            audit=AuditContext.system(),
        )
    departing_id = departing.id
    session.flush()

    close_account(session, departing_id, AuditContext.system())
    session.flush()

    assert roles_for_user(session, departing_id) == frozenset()
    assert roles_for_user(session, remaining_admin.id) == frozenset({Role.ADMINISTRATOR})

    revoked_count = session.execute(
        text(
            "SELECT count(*) FROM audit_event WHERE action = 'user_role.revoked' "
            "AND before->>'user_id' = :user_id"
        ),
        {"user_id": str(departing_id)},
    ).scalar_one()
    assert revoked_count == 1


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
