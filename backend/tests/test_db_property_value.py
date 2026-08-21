"""property_value constraint and privilege tests (issue #51, FR-09,
FR-10). See ADR-0012 for the design record this schema implements.

A value row needs both a `catalogue_entry` row and a `property_definition`
row to exist first, satisfying the two FKs - `_seed` inserts both, using
the owner connection so the FK targets exist before any test exercises
`property_value` itself.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_VALUE = text(
    "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
    "VALUES (:entry_id, :property_key, :ordinal, :value)"
)


def _seed(connection: Connection, *, key: str) -> uuid.UUID:
    entry_id = connection.execute(
        text(
            "INSERT INTO catalogue_entry (business_key, preferred_term) "
            "VALUES (:business_key, 'Sample entry') RETURNING id"
        ),
        {"business_key": f"NPTC-{uuid.uuid4().int % 900000 + 100000}"},
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO property_definition "
            "(key, label, datatype, cardinality, scope, required_for_submission, "
            "required_for_publication, filterable, origin, display_order) "
            "VALUES (:key, :key, 'string', '0..*', 'both', false, false, false, 'admin', 0)"
        ),
        {"key": key},
    )
    return entry_id


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_a_value_can_be_recorded_and_read_back(db: Connection) -> None:
    entry_id = _seed(db, key="repeatable_prop")

    db.execute(
        _INSERT_VALUE,
        {
            "entry_id": entry_id,
            "property_key": "repeatable_prop",
            "ordinal": 0,
            "value": '"first"',
        },
    )
    row = db.execute(
        text(
            "SELECT value FROM property_value "
            "WHERE entry_id = :entry_id AND property_key = 'repeatable_prop'"
        ),
        {"entry_id": entry_id},
    ).one()
    assert row.value == "first"


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_ordinal_must_be_non_negative(db: Connection) -> None:
    entry_id = _seed(db, key="negative_ordinal_prop")

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            _INSERT_VALUE,
            {
                "entry_id": entry_id,
                "property_key": "negative_ordinal_prop",
                "ordinal": -1,
                "value": '"x"',
            },
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_property_key_must_reference_a_definition(db: Connection) -> None:
    entry_id = db.execute(
        text(
            "INSERT INTO catalogue_entry (business_key, preferred_term) "
            "VALUES ('NPTC-500001', 'Sample entry') RETURNING id"
        )
    ).scalar_one()

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            _INSERT_VALUE,
            {
                "entry_id": entry_id,
                "property_key": "does_not_exist",
                "ordinal": 0,
                "value": '"x"',
            },
        )

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_app_role_can_insert_select_update_and_delete(app_db: Connection) -> None:
    entry_id = _seed(app_db, key="editable_value_prop")

    app_db.execute(
        _INSERT_VALUE,
        {
            "entry_id": entry_id,
            "property_key": "editable_value_prop",
            "ordinal": 0,
            "value": '"first"',
        },
    )
    app_db.execute(
        text(
            "UPDATE property_value SET value = '\"second\"' "
            "WHERE entry_id = :entry_id AND property_key = 'editable_value_prop'"
        ),
        {"entry_id": entry_id},
    )
    row = app_db.execute(
        text(
            "SELECT value FROM property_value "
            "WHERE entry_id = :entry_id AND property_key = 'editable_value_prop'"
        ),
        {"entry_id": entry_id},
    ).one()
    assert row.value == "second"

    app_db.execute(
        text(
            "DELETE FROM property_value "
            "WHERE entry_id = :entry_id AND property_key = 'editable_value_prop'"
        ),
        {"entry_id": entry_id},
    )
    remaining = app_db.execute(
        text(
            "SELECT 1 FROM property_value "
            "WHERE entry_id = :entry_id AND property_key = 'editable_value_prop'"
        ),
        {"entry_id": entry_id},
    ).first()
    assert remaining is None


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    _seed(app_db, key="no_truncate_value_prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE property_value"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
