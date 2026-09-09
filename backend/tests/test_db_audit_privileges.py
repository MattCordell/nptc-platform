"""audit_event privilege tests (issue #33): the app role can INSERT/SELECT
and is refused UPDATE/DELETE/TRUNCATE, authenticated as a genuinely separate
login (nptc_app_login), never a superuser connection with SET ROLE.

Each refusal gets its own test function: a privilege error aborts the
surrounding transaction, so a second statement in the same transaction
would fail with 25P02 and mask the assertion actually under test.

The refusal helpers themselves live in audit_privilege_support.py (issue
#35), shared with test_db_round_trip.py's post-round-trip refusal tests -
loaded by file path, exactly as auth_jwt_support.py is loaded by the four
auth test modules, since backend/tests has no __init__.py and pytest runs
with --import-mode=importlib.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.queries import AuditEventFilter, search_audit_events

_support_spec = importlib.util.spec_from_file_location(
    "_test_db_audit_privileges_support", Path(__file__).parent / "audit_privilege_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
insert_one_row = _support.insert_one_row
assert_refused = _support.assert_refused


@pytest.mark.integration
def test_app_role_can_insert_and_select(app_db: Connection) -> None:
    """Also proves the identity-sequence and schema-USAGE grants are
    sufficient on their own: nothing beyond INSERT on the table itself was
    granted, and this succeeds regardless (see nptc.db.models.audit's note
    on why identity, not `serial`, is what makes that true)."""
    insert_one_row(app_db)

    rows = app_db.execute(text("SELECT action, entity_type, entity_id FROM audit_event")).all()

    assert rows == [("test.action", "test_entity", "1")]


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_refused_update(app_db: Connection) -> None:
    insert_one_row(app_db)

    assert_refused(app_db, "UPDATE audit_event SET reason = 'edited'")


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    insert_one_row(app_db)

    assert_refused(app_db, "DELETE FROM audit_event")


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    insert_one_row(app_db)

    assert_refused(app_db, "TRUNCATE audit_event")


@pytest.mark.req("NFR-09")
@pytest.mark.req("NFR-12")
@pytest.mark.integration
def test_app_role_can_search_audit_events_but_remains_refused_update(
    app_db: Connection,
) -> None:
    """Issue #286: `nptc.audit.queries.search_audit_events` reaches
    `audit_event` through the same `nptc_app_login` role every other query
    in this file exercises - the NFR-12 read surface adds no new grant,
    and this role is still refused a write afterwards. `assert_refused`
    must be the last statement on this connection (see its own
    docstring), so the read runs first."""
    insert_one_row(app_db)
    session = Session(bind=app_db)

    page = search_audit_events(session, AuditEventFilter(), limit=50)

    assert len(page.events) >= 1
    assert_refused(app_db, "UPDATE audit_event SET reason = 'edited'")
