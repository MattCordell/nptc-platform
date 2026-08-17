"""Integration tests for nptc.audit.writer/nptc.audit.verification (issue
#36, NFR-10): the happy-path append -> verify_chain round trip, and the
concurrent-append serialisation the advisory lock exists for.

`app_db` (the `nptc_app_login` role) is used for every append below,
exactly as production code will - `append_audit_event` never needs more
than INSERT/SELECT. `test_audit_tamper_detection.py` is where the owner
role's UPDATE/DELETE privileges get exercised, once a chain already
exists to tamper with.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from nptc.audit.hashing import GENESIS_HASH
from nptc.audit.verification import verify_chain
from nptc.audit.writer import AuditContext, append_audit_event


def _append(session: Session, entity_id: str) -> None:
    append_audit_event(
        session,
        AuditContext.system(),
        action="test.action",
        entity_type="test_entity",
        entity_id=entity_id,
    )


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_empty_table_verifies(app_db: Connection) -> None:
    """An explicit acceptance criterion: an empty chain is not an error."""
    result = verify_chain(app_db)

    assert result.ok is True
    assert result.record_count == 0
    assert result.first_sequence is None
    assert result.last_sequence is None
    assert result.first_broken_sequence is None
    assert result.reason is None


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_single_row_chain_verifies(app_db: Connection) -> None:
    """The other explicit acceptance criterion: a one-row chain is not a
    degenerate case verify_chain mishandles."""
    session = Session(bind=app_db)
    _append(session, "1")
    session.flush()

    result = verify_chain(app_db)

    assert result.ok is True
    assert result.record_count == 1
    assert result.first_sequence == result.last_sequence
    assert result.reason is None


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_three_appends_verify_with_correct_bounds(app_db: Connection) -> None:
    session = Session(bind=app_db)
    for entity_id in ("1", "2", "3"):
        _append(session, entity_id)
    session.flush()

    result = verify_chain(app_db)

    assert result.ok is True
    assert result.record_count == 3
    assert result.first_sequence is not None
    assert result.last_sequence == result.first_sequence + 2
    assert result.first_broken_sequence is None
    assert result.reason is None


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_each_appends_prev_hash_links_to_its_predecessors_entry_hash(app_db: Connection) -> None:
    session = Session(bind=app_db)
    first = append_audit_event(
        session, AuditContext.system(), action="a", entity_type="t", entity_id="1"
    )
    second = append_audit_event(
        session, AuditContext.system(), action="a", entity_type="t", entity_id="2"
    )
    session.flush()

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.entry_hash
    assert first.entry_hash != second.entry_hash


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_awkward_jsonb_payloads_round_trip_through_the_write_time_self_check(
    app_db: Connection,
) -> None:
    """Non-ASCII keys/values, nested objects, an empty object, a float, a
    large integer and an embedded null - `append_audit_event` raises
    `AuditChainWriteError` if the digest recomputed from what Postgres
    actually stored diverges from what was written, so simply not raising
    here already proves the round trip; `verify_chain` afterwards is a
    second, independent confirmation."""
    session = Session(bind=app_db)
    before = {
        "unicode_key_é": "café ☃",
        "nested": {"a": [1, 2, {"b": None}]},
        "empty": {},
        "float": 1.5,
        "large_int": 12345678901234567890,
        "list_with_null": [None, "x", 3],
    }
    after = {"changed": True}

    event = append_audit_event(
        session,
        AuditContext.system(),
        action="test.awkward",
        entity_type="test_entity",
        entity_id="1",
        before=before,
        after=after,
    )
    session.flush()

    assert event.before == before
    assert event.after == after

    result = verify_chain(app_db)
    assert result.ok is True


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_concurrent_append_serialises_through_the_advisory_lock(app_engine: Engine) -> None:
    """The principal failure mode this issue guards against: two
    concurrent appenders reading the same chain tail and forking it.
    `pg_advisory_xact_lock` (`nptc.audit.writer`'s step 1) serialises them
    instead of letting that happen - proven here by giving connection B a
    short `lock_timeout` rather than waiting for it to block indefinitely.
    Connection A then relinquishes the lock (by ending its transaction)
    and B's retry succeeds, producing a chain that verifies.

    Needs two genuinely separate connections/transactions, not the
    `app_db` fixture (a single rolled-back transaction) - `app_engine`
    (session-scoped) is used directly so each side of the contention has
    its own backend connection.
    """
    conn_a = app_engine.connect()
    trans_a = conn_a.begin()
    try:
        session_a = Session(bind=conn_a)
        append_audit_event(
            session_a, AuditContext.system(), action="a", entity_type="t", entity_id="a"
        )

        conn_b = app_engine.connect()
        trans_b = conn_b.begin()
        try:
            conn_b.execute(text("SET LOCAL lock_timeout = '200ms'"))
            session_b = Session(bind=conn_b)
            with pytest.raises(OperationalError) as exc_info:
                append_audit_event(
                    session_b, AuditContext.system(), action="b", entity_type="t", entity_id="b"
                )
            assert exc_info.value.orig.sqlstate == "55P03"  # type: ignore[union-attr]
        finally:
            trans_b.rollback()
            conn_b.close()
    finally:
        # Rolling back (rather than committing) keeps this test hermetic -
        # either ends the transaction and so releases the xact-scoped
        # advisory lock, which is the only thing B's retry below needs.
        trans_a.rollback()
        conn_a.close()

    conn_retry = app_engine.connect()
    trans_retry = conn_retry.begin()
    try:
        session_retry = Session(bind=conn_retry)
        append_audit_event(
            session_retry, AuditContext.system(), action="b", entity_type="t", entity_id="b"
        )
        session_retry.flush()

        result = verify_chain(conn_retry)
        assert result.ok is True
        assert result.record_count == 1
    finally:
        trans_retry.rollback()
        conn_retry.close()
