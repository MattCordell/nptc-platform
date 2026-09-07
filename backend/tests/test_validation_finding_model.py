"""validation_finding constraint and privilege tests (issue #141, FR-18,
FR-45, FR-55).

Each constraint/privilege violation gets its own test function - see
`test_db_catalogue_entry.py`'s own module docstring for why (a failed
statement aborts the surrounding transaction, 25P02).

Seeded exclusively via `db` (the owner connection), never `app_db` -
`nptc.db.roles.GRANT_VALIDATION_FINDING_SQL` grants `nptc_app` SELECT
only, matching `test_audit_tamper_detection.py`'s own precedent for
privileged direct writes the app role could not itself perform.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_FINDING = text(
    "INSERT INTO validation_finding (entry_id, binding_id, finding_type, severity, status) "
    "VALUES (:entry_id, :binding_id, :finding_type, :severity, :status) RETURNING id"
)


def _insert_entry(
    connection: Connection,
    *,
    business_key: str = "NPTC-410001",
    preferred_term: str = "Free thyroxine",
) -> object:
    return connection.execute(
        _INSERT_ENTRY, {"business_key": business_key, "preferred_term": preferred_term}
    ).scalar_one()


def _insert_finding(
    connection: Connection,
    *,
    entry_id: object,
    binding_id: object | None = None,
    finding_type: str = "code_inactive",
    severity: str = "error",
    status: str = "open",
) -> object:
    return connection.execute(
        _INSERT_FINDING,
        {
            "entry_id": entry_id,
            "binding_id": binding_id,
            "finding_type": finding_type,
            "severity": severity,
            "status": status,
        },
    ).scalar_one()


@pytest.mark.req("FR-45")
@pytest.mark.integration
def test_finding_type_must_be_one_of_fr_45s_named_checks(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_finding(db, entry_id=entry_id, finding_type="made_up_check")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-45")
@pytest.mark.integration
def test_severity_must_be_error_warning_or_info(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_finding(db, entry_id=entry_id, severity="critical")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-55")
@pytest.mark.integration
def test_status_must_be_one_of_fr_55s_four_lifecycle_states(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_finding(db, entry_id=entry_id, status="ignored")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-55")
@pytest.mark.integration
def test_status_defaults_to_open(db: Connection) -> None:
    entry_id = _insert_entry(db, business_key="NPTC-410002")

    finding_id = db.execute(
        text(
            "INSERT INTO validation_finding (entry_id, finding_type, severity) "
            "VALUES (:entry_id, :finding_type, :severity) RETURNING id"
        ),
        {"entry_id": entry_id, "finding_type": "code_inactive", "severity": "error"},
    ).scalar_one()

    row = db.execute(
        text("SELECT status FROM validation_finding WHERE id = :id"), {"id": finding_id}
    ).one()
    assert row.status == "open"


@pytest.mark.req("FR-18")
@pytest.mark.integration
def test_binding_id_is_nullable(db: Connection) -> None:
    """FR-18 speaks of the binding's finding, but the indicator itself is
    entry-scoped - a finding need not carry a binding_id."""
    entry_id = _insert_entry(db, business_key="NPTC-410003")

    finding_id = _insert_finding(db, entry_id=entry_id, binding_id=None)

    row = db.execute(
        text("SELECT binding_id FROM validation_finding WHERE id = :id"), {"id": finding_id}
    ).one()
    assert row.binding_id is None


@pytest.mark.integration
def test_app_role_can_select(app_db: Connection) -> None:
    """Proves the SELECT grant itself, not cross-connection visibility of
    a row - `db` and `app_db` are each their own uncommitted transaction
    (see test_audit_tamper_detection.py's own docstring on this), so a row
    inserted via `db` is never visible to `app_db` within one test. An
    empty result is fine; a `ProgrammingError` (42501) is not."""
    rows = app_db.execute(text("SELECT finding_type FROM validation_finding")).all()
    assert rows == []


@pytest.mark.integration
def test_app_role_is_refused_insert(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-410005")

    with pytest.raises(ProgrammingError) as exc_info:
        _insert_finding(app_db, entry_id=entry_id)

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_update(db: Connection, app_db: Connection) -> None:
    entry_id = _insert_entry(db, business_key="NPTC-410006")
    finding_id = _insert_finding(db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE validation_finding SET status = 'resolved' WHERE id = :id"),
            {"id": finding_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_delete(db: Connection, app_db: Connection) -> None:
    entry_id = _insert_entry(db, business_key="NPTC-410007")
    finding_id = _insert_finding(db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM validation_finding WHERE id = :id"), {"id": finding_id})

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_truncate(db: Connection, app_db: Connection) -> None:
    entry_id = _insert_entry(db, business_key="NPTC-410008")
    _insert_finding(db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE validation_finding"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
