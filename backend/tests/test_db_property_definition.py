"""property_definition constraint and privilege tests (issue #51, FR-09,
FR-10, FR-11, FR-12). See ADR-0012 for the design record this schema
implements.

Six privilege-refusal tests, one refusal per test function for the
`25P02` reason ADR-0011 recorded (a privilege error aborts the
surrounding transaction, so a second assertion in the same function would
be masked) - matching `test_db_catalogue_entry.py`'s own convention.
Five assert `42501` (a real column-level privilege refusal);
`test_app_role_is_refused_update_of_index_seq` asserts `428C9` instead,
since `index_seq` is `GENERATED ALWAYS AS IDENTITY` and Postgres refuses
that `UPDATE` before the privilege check is ever reached (ADR-0012).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"
_GENERATED_ALWAYS_VIOLATION = "428C9"

_INSERT_DEFINITION = text(
    "INSERT INTO property_definition "
    "(key, label, datatype, cardinality, scope, required_for_submission, "
    "required_for_publication, filterable, origin, display_order) "
    "VALUES (:key, :label, 'string', '0..1', 'entry', false, false, false, 'admin', 0) "
    "RETURNING id"
)

_INSERT_CODE_DEFINITION = text(
    "INSERT INTO property_definition "
    "(key, label, datatype, cardinality, scope, required_for_submission, "
    "required_for_publication, binding_target, value_set_uri, strength, "
    "filterable, origin, display_order) "
    "VALUES (:key, :label, 'code', '0..1', 'entry', false, false, "
    "'value_set', 'https://example.test/vs', 'required', false, 'admin', 0) "
    "RETURNING id"
)


def _insert_definition(
    connection: Connection, *, key: str = "sample_property", label: str = "Sample property"
) -> None:
    connection.execute(_INSERT_DEFINITION, {"key": key, "label": label})


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_key_must_match_the_registry_format(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_definition(db, key="Not-A-Valid-Key")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_code_datatype_without_binding_is_rejected(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO property_definition "
                "(key, label, datatype, cardinality, scope, required_for_submission, "
                "required_for_publication, filterable, origin, display_order) "
                "VALUES ('coded_prop', 'Coded prop', 'code', '0..1', 'entry', "
                "false, false, false, 'admin', 0)"
            )
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_value_set_binding_without_a_value_set_uri_is_rejected(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO property_definition "
                "(key, label, datatype, cardinality, scope, required_for_submission, "
                "required_for_publication, binding_target, strength, filterable, origin, "
                "display_order) "
                "VALUES ('coded_prop', 'Coded prop', 'code', '0..1', 'entry', "
                "false, false, 'value_set', 'required', false, 'admin', 0)"
            )
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-10")
@pytest.mark.integration
def test_code_datatype_with_a_value_set_binding_is_accepted(db: Connection) -> None:
    _insert_code_definition = db.execute(
        _INSERT_CODE_DEFINITION, {"key": "coded_prop", "label": "Coded prop"}
    )
    row = db.execute(
        text(
            "SELECT binding_target, value_set_uri FROM property_definition WHERE key = 'coded_prop'"
        )
    ).one()
    assert row.binding_target == "value_set"
    assert row.value_set_uri == "https://example.test/vs"
    assert _insert_code_definition.scalar_one() is not None


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_deprecated_status_requires_a_deprecated_at_timestamp(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO property_definition "
                "(key, label, datatype, cardinality, scope, required_for_submission, "
                "required_for_publication, filterable, origin, status, display_order) "
                "VALUES ('deprecated_prop', 'Deprecated prop', 'string', '0..1', 'entry', "
                "false, false, false, 'admin', 'deprecated', 0)"
            )
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_app_role_can_insert_select_and_update_non_key_columns(app_db: Connection) -> None:
    _insert_definition(app_db, key="editable_prop", label="Editable prop")

    app_db.execute(
        text("UPDATE property_definition SET label = 'Renamed' WHERE key = 'editable_prop'")
    )
    row = app_db.execute(
        text("SELECT label FROM property_definition WHERE key = 'editable_prop'")
    ).one()
    assert row.label == "Renamed"


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_app_role_is_refused_update_of_key(app_db: Connection) -> None:
    _insert_definition(app_db, key="immutable_prop", label="Immutable prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE property_definition SET key = 'renamed_prop' WHERE key = 'immutable_prop'")
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_app_role_is_refused_update_of_id(app_db: Connection) -> None:
    _insert_definition(app_db, key="id_prop", label="Id prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE property_definition SET id = gen_random_uuid() WHERE key = 'id_prop'")
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_app_role_is_refused_update_of_origin(app_db: Connection) -> None:
    _insert_definition(app_db, key="origin_prop", label="Origin prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE property_definition SET origin = 'system' WHERE key = 'origin_prop'")
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_app_role_is_refused_update_of_created_at(app_db: Connection) -> None:
    _insert_definition(app_db, key="created_at_prop", label="Created at prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE property_definition SET created_at = now() WHERE key = 'created_at_prop'")
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_app_role_is_refused_update_of_index_seq(app_db: Connection) -> None:
    """`index_seq` is `GENERATED ALWAYS AS IDENTITY`, so Postgres refuses
    this `UPDATE` with `428C9` before the privilege check is ever reached
    - a different mechanism from the other five refusal tests, which is
    why it belongs in this suite but asserts a different sqlstate
    (ADR-0012)."""
    _insert_definition(app_db, key="index_seq_prop", label="Index seq prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text(
                "UPDATE property_definition SET index_seq = index_seq + 1 "
                "WHERE key = 'index_seq_prop'"
            )
        )

    assert exc_info.value.orig.sqlstate == _GENERATED_ALWAYS_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    _insert_definition(app_db, key="no_delete_prop", label="No delete prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM property_definition WHERE key = 'no_delete_prop'"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    _insert_definition(app_db, key="no_truncate_prop", label="No truncate prop")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE property_definition"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_app_role_can_update_row_version(app_db: Connection) -> None:
    """`row_version` must sit inside the column-level UPDATE grant, or
    every SQLAlchemy `version_id_col` write 500s with a permission error
    rather than a version check (see `nptc.db.roles.
    GRANT_PROPERTY_DEFINITION_UPDATE_SQL`'s own comment)."""
    _insert_definition(app_db, key="versioned_prop", label="Versioned prop")

    app_db.execute(
        text(
            "UPDATE property_definition SET row_version = row_version + 1 "
            "WHERE key = 'versioned_prop'"
        )
    )
    row = app_db.execute(
        text("SELECT row_version FROM property_definition WHERE key = 'versioned_prop'")
    ).one()
    assert row.row_version == 2
