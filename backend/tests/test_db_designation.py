"""designation constraint and privilege tests (issue #47, FR-04, FR-24,
FR-37, FR-85).

Each constraint/privilege violation gets its own test function - see
`test_db_catalogue_entry.py`'s own module docstring for why (a failed
statement aborts the surrounding transaction, 25P02).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.db.models.designation import _LANGUAGE_CHECK_SQL
from nptc_shared.language import LANGUAGE_TAG_PATTERN

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_DESIGNATION = text(
    "INSERT INTO designation (entry_id, term, use, language) "
    "VALUES (:entry_id, :term, :use, :language) RETURNING id"
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
    use: str = "synonym",
    language: str = "en-AU",
) -> None:
    connection.execute(
        _INSERT_DESIGNATION,
        {"entry_id": entry_id, "term": term, "use": use, "language": language},
    )


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_use_is_constrained_to_preferred_or_synonym(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, use="made_up_use")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


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


@pytest.mark.integration
def test_language_must_be_a_well_formed_tag(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, language="not a tag")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


def test_designation_language_check_matches_the_shared_pattern() -> None:
    """`ck_designation_language`'s regex is built from
    `LANGUAGE_TAG_PATTERN.pattern` (`nptc.db.models.designation.
    _LANGUAGE_CHECK_SQL`), not hand-copied - this pins that so the database
    invariant and `nptc_shared.language.is_well_formed_language_tag` (the
    model's own `@validates("language")` hook) can never silently
    diverge."""
    assert f"language ~ '{LANGUAGE_TAG_PATTERN.pattern}'" == _LANGUAGE_CHECK_SQL


@pytest.mark.integration
def test_en_au_preferred_designation_row_is_refused(db: Connection) -> None:
    """The catalogue's en-AU preferred term lives only on
    `catalogue_entry.preferred_term` (issue #46/#47's own design decision) -
    a `designation` row can never duplicate it."""
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, term="Full blood count", use="preferred")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_a_non_en_au_preferred_designation_is_permitted(db: Connection) -> None:
    entry_id = _insert_entry(db)

    _insert_designation(
        db, entry_id=entry_id, term="Panui toto katoa", use="preferred", language="mi-NZ"
    )


@pytest.mark.integration
def test_second_active_preferred_in_same_language_is_refused(db: Connection) -> None:
    entry_id = _insert_entry(db)
    _insert_designation(db, entry_id=entry_id, term="First", use="preferred", language="mi-NZ")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_designation(db, entry_id=entry_id, term="Second", use="preferred", language="mi-NZ")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


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


@pytest.mark.integration
def test_app_role_can_insert_select_and_update(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-100002")
    _insert_designation(app_db, entry_id=entry_id, term="FBC")

    app_db.execute(text("UPDATE designation SET status = 'retired' WHERE term = 'FBC'"))
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
