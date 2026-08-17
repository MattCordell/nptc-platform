"""app_user / user_identity privilege tests (issue #42): the app role can
read/write what NFR-17 needs and is refused everything that would let it
delete a user or mutate the immutable UUID (NFR-04), authenticated as a
genuinely separate login (nptc_app_login), never a superuser connection
with SET ROLE.

Each refusal gets its own test function - see test_db_audit_privileges.py
for why (a privilege error aborts the surrounding transaction).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import ProgrammingError

_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, :display_name) RETURNING id"
)


def _insert_user(app_db: Connection, username: str) -> uuid.UUID:
    row = app_db.execute(_INSERT_USER, {"username": username, "display_name": username})
    return row.scalar_one()


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_app_role_can_insert_select_and_update_app_user(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "alice")

    app_db.execute(
        text("UPDATE app_user SET display_name = 'Alice Updated' WHERE id = :id"), {"id": user_id}
    )

    row = app_db.execute(
        text("SELECT display_name FROM app_user WHERE id = :id"), {"id": user_id}
    ).one()
    assert row.display_name == "Alice Updated"


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_app_role_is_refused_delete_on_app_user(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "bob")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_app_role_is_refused_truncate_on_app_user(app_db: Connection) -> None:
    _insert_user(app_db, "carol")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE app_user"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_app_role_is_refused_update_of_app_user_id(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "dave")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE app_user SET id = :new_id WHERE id = :id"),
            {"new_id": str(uuid.uuid4()), "id": user_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_app_role_can_delete_user_identity(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "erin")
    app_db.execute(
        text(
            "INSERT INTO user_identity (user_id, issuer, subject) VALUES (:id, 'https://idp.example', 'sub-1')"
        ),
        {"id": user_id},
    )

    app_db.execute(text("DELETE FROM user_identity WHERE user_id = :id"), {"id": user_id})

    rows = app_db.execute(
        text("SELECT 1 FROM user_identity WHERE user_id = :id"), {"id": user_id}
    ).all()
    assert rows == []


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_app_role_is_refused_truncate_on_user_identity(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "frank")
    app_db.execute(
        text(
            "INSERT INTO user_identity (user_id, issuer, subject) VALUES (:id, 'https://idp.example', 'sub-2')"
        ),
        {"id": user_id},
    )

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE user_identity"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
