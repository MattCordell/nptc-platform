"""`terms_acceptance` constraint and privilege tests (NFR-45, ADR-0043).

Each violation gets its own test, because a failed statement aborts the surrounding transaction
(25P02); see `test_db_catalogue_entry.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

_FOREIGN_KEY_VIOLATION = "23503"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Terms tester') RETURNING id"
)
_INSERT_ACCEPTANCE = text(
    "INSERT INTO terms_acceptance (user_id, version) VALUES (:user_id, :version) RETURNING id"
)


def _insert_user(connection: Connection) -> object:
    return connection.execute(_INSERT_USER, {"username": f"terms-{uuid.uuid4()}"}).scalar_one()


def _accept(connection: Connection, user_id: object, version: str) -> int:
    return connection.execute(
        _INSERT_ACCEPTANCE, {"user_id": user_id, "version": version}
    ).scalar_one()


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_app_role_can_insert_and_select_acceptances(app_db: Connection) -> None:
    user_id = _insert_user(app_db)
    _accept(app_db, user_id, "2026-10-06")

    row = app_db.execute(
        text("SELECT version, accepted_at FROM terms_acceptance WHERE user_id = :id"),
        {"id": user_id},
    ).one()
    assert row.version == "2026-10-06"
    assert row.accepted_at is not None


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_user_keeps_every_acceptance_and_the_identity_orders_them(db: Connection) -> None:
    """The prior acceptance is retained (NFR-45), and `id` rises within one transaction where
    `accepted_at` could tie, so it is a safe way to find the latest row."""
    user_id = _insert_user(db)

    first = _accept(db, user_id, "2026-10-06")
    second = _accept(db, user_id, "2027-01-15")

    assert second > first
    versions = (
        db.execute(
            text("SELECT version FROM terms_acceptance WHERE user_id = :id ORDER BY id"),
            {"id": user_id},
        )
        .scalars()
        .all()
    )
    assert versions == ["2026-10-06", "2027-01-15"]


@pytest.mark.integration
def test_an_acceptance_must_reference_a_real_user(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _accept(db, "00000000-0000-0000-0000-000000000000", "2026-10-06")

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
@pytest.mark.parametrize("version", ["", "   "])
def test_version_cannot_be_blank(db: Connection, version: str) -> None:
    user_id = _insert_user(db)

    with pytest.raises(IntegrityError) as exc_info:
        _accept(db, user_id, version)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids
#: name the verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update": "UPDATE terms_acceptance SET version = 'changed'",
    "delete": "DELETE FROM terms_acceptance",
    "truncate": "TRUNCATE terms_acceptance",
}


@pytest.mark.req("NFR-45")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_cannot_edit_or_remove_an_acceptance(app_db: Connection, statement: str) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
