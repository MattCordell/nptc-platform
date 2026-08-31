"""designation_collision_acknowledgement constraint and privilege tests
(issue #49, FR-05).

Each constraint/privilege violation gets its own test function - see
`test_db_catalogue_entry.py`'s own module docstring for why (a failed
statement aborts the surrounding transaction, 25P02).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.db.errors import unique_violation_constraint

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_ACK = text(
    "INSERT INTO designation_collision_acknowledgement "
    "(entry_id, term_key, language, reason) "
    "VALUES (:entry_id, :term_key, :language, :reason) RETURNING id"
)


def _insert_entry(
    connection: Connection,
    *,
    business_key: str = "NPTC-300001",
    preferred_term: str = "Adenosine deaminase",
) -> object:
    return connection.execute(
        _INSERT_ENTRY, {"business_key": business_key, "preferred_term": preferred_term}
    ).scalar_one()


def _insert_ack(
    connection: Connection,
    *,
    entry_id: object,
    term_key: str = "ada2",
    language: str = "en-AU",
    reason: str = "Genuinely ambiguous abbreviation, disambiguated by specimen",
) -> object:
    return connection.execute(
        _INSERT_ACK,
        {"entry_id": entry_id, "term_key": term_key, "language": language, "reason": reason},
    ).scalar_one()


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_term_key_cannot_be_blank(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_ack(db, entry_id=entry_id, term_key="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_reason_cannot_be_blank(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_ack(db, entry_id=entry_id, reason="  ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_language_must_be_a_well_formed_tag(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_ack(db, entry_id=entry_id, language="not a tag")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_duplicate_acknowledgement_for_the_same_entry_term_and_language_is_refused(
    db: Connection,
) -> None:
    entry_id = _insert_entry(db)
    _insert_ack(db, entry_id=entry_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_ack(db, entry_id=entry_id)

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]
    # Pins the literal constraint name `nptc.catalogue.collisions.
    # acknowledge_collision` matches against (issue #224) - a rename here
    # with no matching update there would silently turn a genuine
    # concurrent-acknowledgement race back into an unmapped 500.
    assert (
        unique_violation_constraint(exc_info.value)
        == "ix_designation_collision_ack_entry_term_language"
    )


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_the_same_term_key_may_be_acknowledged_separately_per_entry(db: Connection) -> None:
    """Scope is (entry, term_key, language) - acknowledging 'ADA2' on one
    entry does not silence it for a different entry, matching #49's own
    per-entry acknowledgement design (a fourth entry later joining the
    group still warns once on its own save)."""
    first_entry_id = _insert_entry(db)
    second_entry_id = _insert_entry(
        db, business_key="NPTC-300002", preferred_term="Adenosine deaminase CSF"
    )
    _insert_ack(db, entry_id=first_entry_id)

    _insert_ack(db, entry_id=second_entry_id)


@pytest.mark.integration
def test_app_role_can_insert_and_select(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-300003")
    _insert_ack(app_db, entry_id=entry_id)

    row = app_db.execute(
        text("SELECT reason FROM designation_collision_acknowledgement WHERE entry_id = :id"),
        {"id": entry_id},
    ).one()
    assert row.reason == "Genuinely ambiguous abbreviation, disambiguated by specimen"


@pytest.mark.integration
def test_app_role_is_refused_update(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-300004")
    _insert_ack(app_db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text(
                "UPDATE designation_collision_acknowledgement SET reason = 'changed' "
                "WHERE entry_id = :id"
            ),
            {"id": entry_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-300005")
    _insert_ack(app_db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("DELETE FROM designation_collision_acknowledgement WHERE entry_id = :id"),
            {"id": entry_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-300006")
    _insert_ack(app_db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE designation_collision_acknowledgement"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
