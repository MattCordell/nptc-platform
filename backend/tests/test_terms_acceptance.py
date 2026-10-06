"""`nptc.terms.acceptance` as a library, against a real PostgreSQL (NFR-45, NFR-08, ADR-0043).

The HTTP behaviour is in `test_api_terms.py`. This file covers what a single-transaction HTTP
test cannot show: the order of the statements that keep two concurrent accepts from both writing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.db.models.audit import AuditEvent
from nptc.db.models.terms_acceptance import TermsAcceptance
from nptc.db.models.user import User
from nptc.terms.acceptance import accept_terms

_VERSION = "2026-10-06"


def _user(session: Session) -> uuid.UUID:
    user = User(username=f"terms-lib-{uuid.uuid4()}", display_name="Terms tester")
    session.add(user)
    session.flush()
    return user.id


def _rows(session: Session, user_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count()).select_from(TermsAcceptance).where(TermsAcceptance.user_id == user_id)
    ).scalar_one()


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_the_append_lock_is_taken_before_the_latest_acceptance_is_read(
    app_db: Connection, app_session: Session, capture_statements
) -> None:
    """Two simultaneous accepts would otherwise both read "not accepted" and both insert. The
    lock serialises them, so the second reads the first's committed row and writes nothing."""
    user_id = _user(app_session)

    with capture_statements(app_db) as statements:
        accept_terms(
            app_session,
            AuditContext.system(),
            user_id=user_id,
            version=_VERSION,
            current_version=_VERSION,
        )

    lock = next(n for n, s in enumerate(statements) if "pg_advisory_xact_lock" in s)
    read = next(n for n, s in enumerate(statements) if "FROM terms_acceptance" in s)
    assert lock < read


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_repeated_accept_returns_the_existing_row_and_writes_one_audit_event(
    app_session: Session,
) -> None:
    user_id = _user(app_session)
    events = (
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "terms_acceptance.created")
    )
    before = app_session.execute(events).scalar_one()

    first = accept_terms(
        app_session,
        AuditContext.system(),
        user_id=user_id,
        version=_VERSION,
        current_version=_VERSION,
    )
    second = accept_terms(
        app_session,
        AuditContext.system(),
        user_id=user_id,
        version=_VERSION,
        current_version=_VERSION,
    )

    assert second.id == first.id
    assert _rows(app_session, user_id) == 1
    assert app_session.execute(events).scalar_one() == before + 1
