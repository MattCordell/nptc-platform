"""audit_event privilege tests (issue #33): the app role can INSERT/SELECT
and is refused UPDATE/DELETE/TRUNCATE, authenticated as a genuinely separate
login (nptc_app_login), never a superuser connection with SET ROLE.

Each refusal gets its own test function: a privilege error aborts the
surrounding transaction, so a second statement in the same transaction
would fail with 25P02 and mask the assertion actually under test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import ProgrammingError

_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_ONE_ROW = text(
    "INSERT INTO audit_event (correlation_id, action, entity_type, entity_id) "
    "VALUES (:correlation_id, :action, :entity_type, :entity_id) "
    "RETURNING id, sequence"
)


def _insert_row(connection: Connection) -> None:
    connection.execute(
        _INSERT_ONE_ROW,
        {
            "correlation_id": str(uuid.uuid4()),
            "action": "test.action",
            "entity_type": "test_entity",
            "entity_id": "1",
        },
    )


@pytest.mark.integration
def test_app_role_can_insert_and_select(app_db: Connection) -> None:
    """Also proves the identity-sequence and schema-USAGE grants are
    sufficient on their own: nothing beyond INSERT on the table itself was
    granted, and this succeeds regardless (see nptc.db.models.audit's note
    on why identity, not `serial`, is what makes that true)."""
    _insert_row(app_db)

    rows = app_db.execute(text("SELECT action, entity_type, entity_id FROM audit_event")).all()

    assert rows == [("test.action", "test_entity", "1")]


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_refused_update(app_db: Connection) -> None:
    _insert_row(app_db)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("UPDATE audit_event SET reason = 'edited'"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    _insert_row(app_db)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM audit_event"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    _insert_row(app_db)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE audit_event"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
