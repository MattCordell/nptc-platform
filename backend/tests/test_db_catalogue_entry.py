"""catalogue_entry constraint and privilege tests (issue #46, FR-03).

Each constraint/privilege violation gets its own test function: a failed
statement aborts the surrounding transaction (25P02), which would mask the
assertion actually under test if a second statement followed it in the
same connection - the same convention `test_db_user_model.py`/
`test_db_user_privileges.py` already set. Privilege tests run entirely on
`app_db` (a single connection authenticated as `nptc_app_login`), matching
`test_db_user_privileges.py`'s own pattern: privilege checks apply to
every statement regardless of whether the transaction has committed, so
there is no need to commit across connections to prove one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)


def _insert_entry(
    connection: Connection,
    *,
    business_key: str = "NPTC-000001",
    preferred_term: str = "Full blood count",
) -> None:
    connection.execute(
        _INSERT_ENTRY, {"business_key": business_key, "preferred_term": preferred_term}
    )


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_business_key_is_unique(db: Connection) -> None:
    _insert_entry(db, business_key="NPTC-000001")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_entry(db, business_key="NPTC-000001", preferred_term="Something else")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_business_key_must_match_the_nptc_format(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_entry(db, business_key="NOT-A-KEY")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_status_is_constrained_to_the_lifecycle_values(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO catalogue_entry (business_key, preferred_term, status) "
                "VALUES ('NPTC-000002', 'Something', 'made_up_status')"
            )
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_default_row_shape(db: Connection) -> None:
    entry_id = db.execute(_INSERT_ENTRY, {"business_key": "NPTC-000003", "preferred_term": "X"})
    row = db.execute(
        text(
            "SELECT status, specimen_unconstrained, row_version FROM catalogue_entry WHERE id = :id"
        ),
        {"id": entry_id.scalar_one()},
    ).one()
    assert row.status == "draft"
    assert row.specimen_unconstrained is False
    assert row.row_version == 1


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_app_role_can_insert_select_and_update_non_key_columns(app_db: Connection) -> None:
    _insert_entry(app_db, business_key="NPTC-000004")

    app_db.execute(
        text(
            "UPDATE catalogue_entry SET preferred_term = 'Renamed' "
            "WHERE business_key = 'NPTC-000004'"
        )
    )
    row = app_db.execute(
        text("SELECT preferred_term FROM catalogue_entry WHERE business_key = 'NPTC-000004'")
    ).one()
    assert row.preferred_term == "Renamed"


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_app_role_is_refused_update_of_business_key(app_db: Connection) -> None:
    _insert_entry(app_db, business_key="NPTC-000005")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text(
                "UPDATE catalogue_entry SET business_key = 'NPTC-000099' "
                "WHERE business_key = 'NPTC-000005'"
            )
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    _insert_entry(app_db, business_key="NPTC-000006")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM catalogue_entry WHERE business_key = 'NPTC-000006'"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-03")
@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    _insert_entry(app_db, business_key="NPTC-000007")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE catalogue_entry"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_app_role_can_update_row_version(app_db: Connection) -> None:
    """`row_version` must sit inside the column-level UPDATE grant, or
    every SQLAlchemy `version_id_col` write 500s with a permission error
    rather than a version check (see `nptc.db.roles.
    GRANT_CATALOGUE_ENTRY_UPDATE_SQL`'s own comment)."""
    _insert_entry(app_db, business_key="NPTC-000008")

    app_db.execute(
        text(
            "UPDATE catalogue_entry SET row_version = row_version + 1 "
            "WHERE business_key = 'NPTC-000008'"
        )
    )
    row = app_db.execute(
        text("SELECT row_version FROM catalogue_entry WHERE business_key = 'NPTC-000008'")
    ).one()
    assert row.row_version == 2
