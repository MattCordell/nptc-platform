"""Integration tests for nptc.audit.verification.verify_chain's tamper
detection (issue #36, NFR-10): every mutation a table owner could make -
edit a column, delete a row, reorder two rows' content - must be caught
at the earliest affected row.

Every test here uses the `db` fixture (the table owner role) for *both*
the legitimate appends and the tamper itself, not `app_db` for the
appends: `nptc_app_login` cannot `UPDATE`/`DELETE` audit_event at all
(NFR-09, proven in test_db_audit_privileges.py), so there is no
privilege-respecting way to tamper via `app_db` in the first place, and
two separate connections each in their own uncommitted transaction would
not see each other's writes anyway - the very isolation that makes the
append-only privilege model meaningful in production would just make a
cross-connection version of this test flaky. test_audit_chain.py already
proves `append_audit_event` works over the least-privilege `app_db`
connection; this module is purely about `verify_chain`'s detection logic
once history has already been rewritten out-of-band.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.hashing import digest_field_names
from nptc.audit.verification import verify_chain
from nptc.audit.writer import AuditContext, append_audit_event
from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User

_DIGEST_COLUMNS = sorted(digest_field_names(AuditEvent.__table__))


def _append_three(session: Session) -> list[uuid.UUID]:
    ids = []
    for entity_id in ("1", "2", "3"):
        event = append_audit_event(
            session,
            AuditContext.system(),
            action="test.action",
            entity_type="test_entity",
            entity_id=entity_id,
        )
        ids.append(event.id)
    session.flush()
    return ids


def _tamper_value(column: str, original: Mapping[str, Any], other_user_id: uuid.UUID) -> object:
    if column == "id":
        return uuid.uuid4()
    if column == "occurred_at":
        return original["occurred_at"] + timedelta(seconds=1)
    if column == "actor_user_id":
        return other_user_id
    if column == "actor_ip":
        return "203.0.113.99"
    if column == "user_agent":
        return "tampered-agent"
    if column == "correlation_id":
        return uuid.uuid4()
    if column in ("action", "entity_type", "entity_id"):
        return f"tampered-{column}"
    if column in ("before", "after"):
        return json.dumps({"tampered": True})
    if column == "reason":
        return "tampered reason"
    if column == "prev_hash":
        return "9" * 64
    raise AssertionError(f"no tamper value defined for digest column {column!r}")


def _build_update_sql(column: str) -> str:
    # Column name is one of AuditEvent.__table__'s own fixed column names
    # (parametrised from digest_field_names below), never request data -
    # backend/tests is outside test_sql_parameterisation.py's SCAN_DIRS
    # (backend/src, backend/migrations) for exactly this reason: a test
    # parametrised over a model's own column names has no runtime-supplied
    # SQL to guard against in the first place.
    if column in ("before", "after"):
        return f"UPDATE audit_event SET {column} = CAST(:value AS jsonb) WHERE id = :id"
    return f"UPDATE audit_event SET {column} = :value WHERE id = :id"


@pytest.mark.req("NFR-10")
@pytest.mark.integration
@pytest.mark.parametrize("column", _DIGEST_COLUMNS)
def test_tampering_one_column_of_a_historical_row_is_detected(db: Connection, column: str) -> None:
    session = Session(bind=db)
    other_user = User(username="tamper-actor", display_name="Tamper Actor")
    session.add(other_user)
    session.flush()

    ids = _append_three(session)
    middle_id = ids[1]

    original = (
        db.execute(text("SELECT * FROM audit_event WHERE id = :id"), {"id": middle_id})
        .mappings()
        .one()
    )
    new_value = _tamper_value(column, original, other_user.id)

    db.execute(text(_build_update_sql(column)), {"value": new_value, "id": middle_id})

    result = verify_chain(db)

    assert result.ok is False
    assert result.first_broken_sequence == original["sequence"]
    if column == "prev_hash":
        assert result.break_reason == "prev_hash mismatch"
    else:
        assert result.break_reason == "entry_hash mismatch"


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_deleting_a_middle_row_is_detected_as_a_prev_hash_mismatch_at_the_successor(
    db: Connection,
) -> None:
    session = Session(bind=db)
    ids = _append_three(session)
    middle_id = ids[1]

    middle_sequence = db.execute(
        text("SELECT sequence FROM audit_event WHERE id = :id"), {"id": middle_id}
    ).scalar_one()
    successor_sequence = db.execute(
        text("SELECT min(sequence) FROM audit_event WHERE sequence > :seq"),
        {"seq": middle_sequence},
    ).scalar_one()

    db.execute(text("DELETE FROM audit_event WHERE id = :id"), {"id": middle_id})

    result = verify_chain(db)

    assert result.ok is False
    assert result.break_reason == "prev_hash mismatch"
    assert result.first_broken_sequence == successor_sequence


@pytest.mark.req("NFR-10")
@pytest.mark.integration
def test_swapping_two_adjacent_rows_content_is_detected(db: Connection) -> None:
    """A re-order attack: the two adjacent rows' own `sequence`/`prev_hash`/
    `entry_hash` are untouched (a `GENERATED ALWAYS` column cannot be
    updated directly), but their content is swapped - equivalent in effect
    to having appended them in the other order."""
    session = Session(bind=db)
    ids = _append_three(session)
    first_id, second_id = ids[0], ids[1]

    first_entity_id, first_sequence = db.execute(
        text("SELECT entity_id, sequence FROM audit_event WHERE id = :id"), {"id": first_id}
    ).one()
    second_entity_id = db.execute(
        text("SELECT entity_id FROM audit_event WHERE id = :id"), {"id": second_id}
    ).scalar_one()

    db.execute(
        text("UPDATE audit_event SET entity_id = :value WHERE id = :id"),
        {"value": second_entity_id, "id": first_id},
    )
    db.execute(
        text("UPDATE audit_event SET entity_id = :value WHERE id = :id"),
        {"value": first_entity_id, "id": second_id},
    )

    result = verify_chain(db)

    assert result.ok is False
    assert result.break_reason == "entry_hash mismatch"
    assert result.first_broken_sequence == first_sequence
