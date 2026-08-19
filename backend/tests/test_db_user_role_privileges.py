"""user_role privilege tests (issue #44, FR-44, FR-01): the app role can
INSERT/SELECT/DELETE a grant and update only `granted_at` (the one column
Postgres requires *some* `UPDATE` privilege on before it will honour
`SELECT ... FOR UPDATE` at all - see `nptc.db.roles.
GRANT_USER_ROLE_UPDATE_SQL`), and is refused updating `user_id`/`role`/
`granted_by_user_id` and TRUNCATE outright - a grant is created or
removed, never *meaningfully* edited, so "who granted this, and when" is
immutable at the privilege level (see `nptc.db.roles`'s own comment).
Authenticated as the genuinely separate `nptc_app_login`, never a
superuser connection with `SET ROLE` - mirrors
test_db_user_privileges.py's own convention, including one refusal per
test function (a privilege error aborts the surrounding transaction).
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
_INSERT_ROLE = text("INSERT INTO user_role (user_id, role) VALUES (:user_id, :role) RETURNING id")


def _insert_user(app_db: Connection, username: str) -> uuid.UUID:
    row = app_db.execute(_INSERT_USER, {"username": username, "display_name": username})
    return row.scalar_one()


def _insert_role(app_db: Connection, user_id: uuid.UUID, role: str = "observer") -> uuid.UUID:
    row = app_db.execute(_INSERT_ROLE, {"user_id": user_id, "role": role})
    return row.scalar_one()


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_app_role_can_insert_select_and_delete_user_role(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "grant-crud")
    role_id = _insert_role(app_db, user_id)

    row = app_db.execute(text("SELECT role FROM user_role WHERE id = :id"), {"id": role_id}).one()
    assert row.role == "observer"

    app_db.execute(text("DELETE FROM user_role WHERE id = :id"), {"id": role_id})
    remaining = app_db.execute(
        text("SELECT 1 FROM user_role WHERE id = :id"), {"id": role_id}
    ).all()
    assert remaining == []


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_app_role_is_refused_update_of_user_role_role_column(app_db: Connection) -> None:
    """`role` is not in the column-level UPDATE grant - a grant is
    created or removed, never edited."""
    user_id = _insert_user(app_db, "grant-no-update")
    role_id = _insert_role(app_db, user_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE user_role SET role = 'administrator' WHERE id = :id"), {"id": role_id}
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_app_role_is_refused_update_of_user_role_user_id_column(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "grant-no-update-uid")
    role_id = _insert_role(app_db, user_id)
    other_user_id = _insert_user(app_db, "grant-no-update-uid-2")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE user_role SET user_id = :other WHERE id = :id"),
            {"other": other_user_id, "id": role_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_app_role_can_update_user_role_granted_at_column(app_db: Connection) -> None:
    """The one column deliberately left updatable - not because anything
    in this codebase ever rewrites it, but because Postgres refuses
    `SELECT ... FOR UPDATE` outright without *some* `UPDATE` privilege on
    the table, and `assert_not_last_administrator`'s row lock (FR-01)
    depends on that working."""
    user_id = _insert_user(app_db, "grant-can-update-granted-at")
    role_id = _insert_role(app_db, user_id)

    app_db.execute(text("UPDATE user_role SET granted_at = now() WHERE id = :id"), {"id": role_id})


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_app_role_can_select_for_update_on_user_role(app_db: Connection) -> None:
    """The actual privilege `assert_not_last_administrator` relies on -
    proven directly, not merely inferred from the column-level UPDATE
    grant above."""
    user_id = _insert_user(app_db, "grant-select-for-update")
    role_id = _insert_role(app_db, user_id)

    row = app_db.execute(
        text("SELECT id FROM user_role WHERE id = :id FOR UPDATE"), {"id": role_id}
    ).one()
    assert row.id == role_id


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_app_role_is_refused_truncate_on_user_role(app_db: Connection) -> None:
    user_id = _insert_user(app_db, "grant-no-truncate")
    _insert_role(app_db, user_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE user_role"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_role_check_constraint_refuses_a_role_not_in_the_grantable_set(app_db: Connection) -> None:
    """Deliberately excludes `'anon'` - ANON is a matrix column, never a
    grantable row (`nptc.auth.permissions.GRANTABLE_ROLES`)."""
    from sqlalchemy.exc import IntegrityError

    user_id = _insert_user(app_db, "grant-bad-role")

    with pytest.raises(IntegrityError):
        app_db.execute(_INSERT_ROLE, {"user_id": user_id, "role": "anon"})
