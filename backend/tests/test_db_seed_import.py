"""seed_import and entry_seed_provenance constraint and privilege tests (issue #329, FR-76).

Each violation gets its own test, because a failed statement aborts the surrounding transaction
(25P02); see `test_db_catalogue_entry.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.db.errors import unique_violation_constraint

_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_VALID_SHA256 = "a" * 64

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_SEED_IMPORT = text(
    "INSERT INTO seed_import (release_name, release_note, source_filename, source_sha256, "
    "dataset_schema_version, entry_count) "
    "VALUES (:release_name, 'note', 'workbook.xlsx', :source_sha256, 1, :entry_count) "
    "RETURNING id"
)
_INSERT_PROVENANCE = text(
    "INSERT INTO entry_seed_provenance "
    "(entry_id, seed_import_id, source_sheet, source_row, legacy_version, legacy_history) "
    "VALUES (:entry_id, :seed_import_id, :source_sheet, :source_row, NULL, NULL)"
)


def _insert_entry(connection: Connection, business_key: str = "NPTC-320001") -> object:
    return connection.execute(
        _INSERT_ENTRY, {"business_key": business_key, "preferred_term": "Adenosine deaminase"}
    ).scalar_one()


def _insert_seed_import(
    connection: Connection,
    *,
    release_name: str = "2026-06",
    source_sha256: str = _VALID_SHA256,
    entry_count: int = 1,
) -> object:
    return connection.execute(
        _INSERT_SEED_IMPORT,
        {
            "release_name": release_name,
            "source_sha256": source_sha256,
            "entry_count": entry_count,
        },
    ).scalar_one()


def _insert_provenance(
    connection: Connection,
    *,
    entry_id: object,
    seed_import_id: object,
    source_sheet: str = "Chemical Pathology",
    source_row: int = 2,
) -> None:
    connection.execute(
        _INSERT_PROVENANCE,
        {
            "entry_id": entry_id,
            "seed_import_id": seed_import_id,
            "source_sheet": source_sheet,
            "source_row": source_row,
        },
    )


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_a_second_seed_import_row_is_refused(db: Connection) -> None:
    _insert_seed_import(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_seed_import(db, release_name="2026-07")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]
    assert unique_violation_constraint(exc_info.value) == "ix_seed_import_singleton"


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_singleton_cannot_be_false(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO seed_import (singleton, release_name, release_note, source_filename, "
                "source_sha256, dataset_schema_version, entry_count) "
                "VALUES (false, '2026-06', 'note', 'workbook.xlsx', :sha, 1, 1)"
            ),
            {"sha": _VALID_SHA256},
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_release_name_cannot_be_blank(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_seed_import(db, release_name="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64])
def test_source_sha256_must_be_64_lowercase_hex_characters(db: Connection, digest: str) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_seed_import(db, source_sha256=digest)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_entry_count_must_be_positive(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_seed_import(db, entry_count=0)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_an_entry_has_at_most_one_provenance_row(db: Connection) -> None:
    seed_import_id = _insert_seed_import(db)
    entry_id = _insert_entry(db)
    _insert_provenance(db, entry_id=entry_id, seed_import_id=seed_import_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_provenance(db, entry_id=entry_id, seed_import_id=seed_import_id, source_row=3)

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_provenance_must_reference_a_real_seed_import(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_provenance(
            db, entry_id=entry_id, seed_import_id="00000000-0000-0000-0000-000000000000"
        )

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_source_sheet_cannot_be_blank(db: Connection) -> None:
    seed_import_id = _insert_seed_import(db)
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_provenance(db, entry_id=entry_id, seed_import_id=seed_import_id, source_sheet="  ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_source_row_must_be_at_least_one(db: Connection) -> None:
    seed_import_id = _insert_seed_import(db)
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_provenance(db, entry_id=entry_id, seed_import_id=seed_import_id, source_row=0)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-76")
@pytest.mark.integration
def test_app_role_can_insert_and_select_both_tables(app_db: Connection) -> None:
    seed_import_id = _insert_seed_import(app_db)
    entry_id = _insert_entry(app_db)
    _insert_provenance(app_db, entry_id=entry_id, seed_import_id=seed_import_id)

    row = app_db.execute(
        text("SELECT source_sheet FROM entry_seed_provenance WHERE entry_id = :id"),
        {"id": entry_id},
    ).one()
    assert row.source_sheet == "Chemical Pathology"


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids
#: name the table and verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "seed_import-update": "UPDATE seed_import SET release_note = 'changed'",
    "seed_import-delete": "DELETE FROM seed_import",
    "seed_import-truncate": "TRUNCATE seed_import",
    "provenance-update": "UPDATE entry_seed_provenance SET legacy_history = 'changed'",
    "provenance-delete": "DELETE FROM entry_seed_provenance",
    "provenance-truncate": "TRUNCATE entry_seed_provenance",
}


@pytest.mark.req("FR-76")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_cannot_edit_or_remove_seed_provenance(app_db: Connection, statement: str) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
