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

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from nptc.audit.hashing import GENESIS_HASH
from nptc.audit.verification import verify_chain
from nptc.audit.writer import AuditContext, AuditIsolationLevelError, append_audit_event
from nptc.db.models.user import User


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
    assert result.break_reason is None


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
    assert result.break_reason is None


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
    assert result.break_reason is None


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
def test_populated_audit_context_appends_and_verifies(app_db: Connection) -> None:
    """Every other test in this module uses `AuditContext.system()` (all
    fields None), so the request-scoped path with a real actor/IP/user
    agent is never otherwise exercised. `actor_ip` is deliberately given
    an explicit host mask (`/32`) - the exact shape that, before the
    normalisation fix, would be hashed as submitted but stored/re-read by
    Postgres's `inet` type without the mask, tripping the write-time
    self-check's `AuditChainWriteError` on every such input."""
    session = Session(bind=app_db)
    actor = User(username="populated-ctx-actor", display_name="Populated Ctx Actor")
    session.add(actor)
    session.flush()
    ctx = AuditContext(
        actor_user_id=actor.id,
        actor_ip="203.0.113.7/32",
        user_agent="pytest-integration/1.0",
        correlation_id=uuid.uuid4(),
    )

    append_audit_event(
        session, ctx, action="test.populated", entity_type="test_entity", entity_id="1"
    )
    session.flush()

    result = verify_chain(app_db)
    assert result.ok is True


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_append_refuses_under_repeatable_read_isolation(app_db: Connection) -> None:
    """The advisory lock alone does not keep the chain from forking above
    `READ COMMITTED` (see `nptc.audit.writer`'s module docstring, step 1):
    a snapshot fixed before the lock-holder's commit can still read a
    stale tail once the blocked transaction gets its turn. The guard added
    in `append_audit_event` (step 1a) must refuse to proceed rather than
    risk that silently."""
    session = Session(bind=app_db)
    app_db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    with pytest.raises(AuditIsolationLevelError):
        append_audit_event(
            session, AuditContext.system(), action="a", entity_type="t", entity_id="1"
        )


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_concurrent_append_serialises_through_the_advisory_lock(
    app_engine: Engine, owner_engine: Engine
) -> None:
    """The principal failure mode this issue guards against: two
    concurrent appenders reading the same chain tail and forking it.
    `pg_advisory_xact_lock` (`nptc.audit.writer`'s step 1) serialises them
    instead of letting that happen - proven here by giving connection B a
    short `lock_timeout` rather than waiting for it to block indefinitely.

    Connection A then **commits** (not rolls back) so its row survives,
    and B's retry runs afterwards and actually chains against it - proving
    the point of the advisory lock: two appenders contending for the same
    tail still end up with one appender's row correctly linked after the
    other's, rather than merely proving B was refused while A's own row
    never had anything to link against.

    Both connections come from `app_engine` (session-scoped), not the
    per-test `app_db`/`db` fixtures (each a single rolled-back
    transaction) - a real commit is needed for B's retry to have anything
    to chain against. Because that commit is real and `postgres_container`
    is session-scoped, the two rows are deleted via `owner_engine` (the
    login role cannot - NFR-09) once the assertions are done, so this test
    leaves `audit_event` exactly as it found it for every other test
    sharing the same container.
    """
    ids_to_clean: list[object] = []
    try:
        conn_a = app_engine.connect()
        trans_a = conn_a.begin()
        try:
            session_a = Session(bind=conn_a)
            first = append_audit_event(
                session_a, AuditContext.system(), action="a", entity_type="t", entity_id="a"
            )
            session_a.flush()
            first_entry_hash = first.entry_hash
            ids_to_clean.append(first.id)

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

            # Commit (not rollback): A's row must survive for B's retry to
            # chain against, which is the whole point of this test.
            trans_a.commit()
        finally:
            conn_a.close()

        conn_retry = app_engine.connect()
        trans_retry = conn_retry.begin()
        try:
            session_retry = Session(bind=conn_retry)
            second = append_audit_event(
                session_retry, AuditContext.system(), action="b", entity_type="t", entity_id="b"
            )
            session_retry.flush()
            ids_to_clean.append(second.id)

            assert second.prev_hash == first_entry_hash

            result = verify_chain(conn_retry)
            assert result.ok is True
            assert result.record_count == 2

            trans_retry.commit()
        finally:
            conn_retry.close()
    finally:
        if ids_to_clean:
            with owner_engine.connect() as cleanup_conn:
                for event_id in ids_to_clean:
                    cleanup_conn.execute(
                        text("DELETE FROM audit_event WHERE id = :id"), {"id": event_id}
                    )
                cleanup_conn.commit()
