"""designation constraint and privilege tests (issue #47, FR-04, FR-24,
FR-37, FR-85).

Each constraint/privilege violation gets its own test function - see
`test_db_catalogue_entry.py`'s own module docstring for why (a failed
statement aborts the surrounding transaction, 25P02).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc_shared.similarity import collision_key

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_DESIGNATION = text(
    "INSERT INTO designation (entry_id, term, term_key, status, retired_at) "
    "VALUES (:entry_id, :term, :term_key, :status, :retired_at) RETURNING id"
)


def _insert_entry(
    connection: Connection,
    *,
    business_key: str = "NPTC-100001",
    preferred_term: str = "Full blood count",
) -> object:
    return connection.execute(
        _INSERT_ENTRY, {"business_key": business_key, "preferred_term": preferred_term}
    ).scalar_one()


def _insert_designation(
    connection: Connection,
    *,
    entry_id: object,
    term: str = "FBC",
    status: str = "active",
    retired_at: datetime | None = None,
) -> None:
    # `term_key` is computed here via the real `collision_key` (issue
    # #49), not left to the column's own `server_default = ''` - every
    # test in this module exercises `designation` as if a real write path
    # had populated it, matching what `Designation._validate_term` always
    # does in practice. Leaving it at the default would make
    # `ix_designation_no_duplicate_active_term` (keyed on `term_key`) read
    # as "at most one raw-inserted active designation per entry" instead of
    # the real, term-scoped invariant.
    connection.execute(
        _INSERT_DESIGNATION,
        {
            "entry_id": entry_id,
            "term": term,
            "term_key": collision_key(term),
            "status": status,
            "retired_at": retired_at,
        },
    )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_a_designation_has_no_use_or_language_column(db: Connection) -> None:
    """Every row is a synonym in the catalogue's one language (ADR-0022), so neither is stored."""
    columns = set(
        db.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'designation'"
            )
        ).scalars()
    )

    assert {"term", "term_key", "status"} <= columns
    assert not {"use", "language"} & columns


@pytest.mark.integration
def test_status_is_constrained_to_active_or_retired(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "INSERT INTO designation (entry_id, term, status) "
                "VALUES (:entry_id, 'FBC', 'made_up_status')"
            ),
            {"entry_id": entry_id},
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_term_cannot_be_blank(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, term="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_duplicate_active_synonym_on_the_same_entry_is_refused(db: Connection) -> None:
    """The same synonym attached twice to one entry (a doubled delimiter,
    or a whitespace variant - PRD Appendix A.4) collapses to one row rather
    than being representable at all."""
    entry_id = _insert_entry(db)
    _insert_designation(db, entry_id=entry_id, term="FBC")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, term="FBC")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_case_and_punctuation_variant_of_an_active_synonym_is_also_refused(
    db: Connection,
) -> None:
    """`ix_designation_no_duplicate_active_term` is keyed on
    `term_key`, not `term` - a case/punctuation variant of an
    already-active synonym on the same entry is unrepresentable too, not
    merely a byte-for-byte duplicate (the case the previous test covers)."""
    entry_id = _insert_entry(db)
    _insert_designation(db, entry_id=entry_id, term="ADA2")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, term="ada2.")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_retiring_without_retired_at_is_refused(db: Connection) -> None:
    """`ck_designation_retired_at` mirrors `ck_code_binding_retired_at`
    (issue #313, issue #140) exactly - mandatory exactly when retired."""
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, status="retired", retired_at=None)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_active_designation_with_retired_at_is_refused(db: Connection) -> None:
    """A stale `retired_at` cannot linger on a designation that is active -
    forbidden exactly when not retired, the other half of
    `ck_designation_retired_at`."""
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, status="active", retired_at=datetime.now(UTC))

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_can_insert_select_and_update(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-100002")
    _insert_designation(app_db, entry_id=entry_id, term="FBC")

    app_db.execute(
        text("UPDATE designation SET status = 'retired', retired_at = now() WHERE term = 'FBC'")
    )
    row = app_db.execute(text("SELECT status FROM designation WHERE term = 'FBC'")).one()
    assert row.status == "retired"


@pytest.mark.integration
def test_app_role_is_refused_update_of_entry_id(app_db: Connection) -> None:
    first_entry = _insert_entry(app_db, business_key="NPTC-100003")
    second_entry = _insert_entry(app_db, business_key="NPTC-100004", preferred_term="Other")
    _insert_designation(app_db, entry_id=first_entry, term="FBC")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE designation SET entry_id = :entry_id WHERE term = 'FBC'"),
            {"entry_id": second_entry},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    """A retired designation is retained, not deleted - #47's own
    acceptance criterion, enforced at the privilege level."""
    entry_id = _insert_entry(app_db, business_key="NPTC-100005")
    _insert_designation(app_db, entry_id=entry_id, term="FBC")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM designation WHERE term = 'FBC'"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-100006")
    _insert_designation(app_db, entry_id=entry_id, term="FBC")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE designation"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
