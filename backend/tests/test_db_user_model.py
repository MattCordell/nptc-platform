"""app_user / user_identity constraint tests (issue #42).

Each constraint violation gets its own test function: a failed statement
aborts the surrounding transaction (25P02), which would mask the
assertion actually under test if a second statement followed it in the
same connection - the same convention test_db_audit_privileges.py already
sets.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from nptc.audit.hashing import GENESIS_HASH

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"

# A fixed, valid-shape (64 lowercase hex) placeholder pair for prev_hash/
# entry_hash - these are FK-behaviour tests (NFR-13), not hash-chain
# tests, so the literal need not satisfy the real digest, only the
# NOT NULL + CHECK constraints. Distinct from the placeholders in
# audit_privilege_support.py/test_auth_account_closure.py purely so a
# reader never mistakes one module's literal for another's.
_PLACEHOLDER_ENTRY_HASH = "a" * 64

_INSERT_ACTIVE_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, :display_name) RETURNING id"
)
_INSERT_CLOSED_USER = text(
    "INSERT INTO app_user (status, closed_at) VALUES ('closed', now()) RETURNING id"
)
_INSERT_IDENTITY = text(
    "INSERT INTO user_identity (user_id, issuer, subject) VALUES (:user_id, :issuer, :subject)"
)
_INSERT_AUDIT_EVENT = text(
    "INSERT INTO audit_event "
    "(actor_user_id, correlation_id, action, entity_type, entity_id, prev_hash, entry_hash) "
    "VALUES "
    "(:actor_user_id, :correlation_id, :action, :entity_type, :entity_id, :prev_hash, :entry_hash) "
    "RETURNING id"
)


def _insert_active_user(
    db: Connection, *, username: str = "alice", display_name: str = "Alice"
) -> uuid.UUID:
    row = db.execute(_INSERT_ACTIVE_USER, {"username": username, "display_name": display_name})
    return row.scalar_one()


def _insert_audit_event(db: Connection, actor_user_id: uuid.UUID | None) -> None:
    db.execute(
        _INSERT_AUDIT_EVENT,
        {
            "actor_user_id": actor_user_id,
            "correlation_id": str(uuid.uuid4()),
            "action": "test.action",
            "entity_type": "test_entity",
            "entity_id": "1",
            "prev_hash": GENESIS_HASH,
            "entry_hash": _PLACEHOLDER_ENTRY_HASH,
        },
    )


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_user_identity_is_unique_on_issuer_and_subject(db: Connection) -> None:
    user_id = _insert_active_user(db)
    db.execute(
        _INSERT_IDENTITY, {"user_id": user_id, "issuer": "https://idp.example", "subject": "sub-1"}
    )

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            _INSERT_IDENTITY,
            {"user_id": user_id, "issuer": "https://idp.example", "subject": "sub-1"},
        )

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_two_closed_accounts_can_coexist_with_null_usernames(db: Connection) -> None:
    first_id = db.execute(_INSERT_CLOSED_USER).scalar_one()
    second_id = db.execute(_INSERT_CLOSED_USER).scalar_one()

    assert first_id != second_id


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_closing_a_user_without_clearing_identifying_fields_is_rejected(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO app_user (username, display_name, status, closed_at) "
                "VALUES ('bob', 'Bob', 'closed', now())"
            )
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_active_user_without_a_username_is_rejected(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(text("INSERT INTO app_user (username, display_name) VALUES (NULL, 'Carol')"))

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_unknown_status_value_is_rejected(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO app_user (username, display_name, status) "
                "VALUES ('dave', 'Dave', 'bogus')"
            )
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_blank_oidc_subject_is_rejected(db: Connection) -> None:
    user_id = _insert_active_user(db)

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            _INSERT_IDENTITY,
            {"user_id": user_id, "issuer": "https://idp.example", "subject": "   "},
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-13")
@pytest.mark.integration
def test_audit_event_rejects_an_unknown_actor_user_id(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_audit_event(db, uuid.uuid4())

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-13")
@pytest.mark.integration
def test_audit_event_rows_survive_closing_their_actor(db: Connection) -> None:
    user_id = _insert_active_user(db, username="erin", display_name="Erin")
    _insert_audit_event(db, user_id)

    db.execute(
        text(
            "UPDATE app_user SET username = NULL, display_name = NULL, organisation = NULL, "
            "status = 'closed', closed_at = now() WHERE id = :id"
        ),
        {"id": user_id},
    )

    row = db.execute(
        text("SELECT actor_user_id FROM audit_event WHERE actor_user_id = :id"), {"id": user_id}
    ).one()
    assert row.actor_user_id == user_id
