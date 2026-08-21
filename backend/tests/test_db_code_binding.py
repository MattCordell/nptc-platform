"""code_binding constraint and privilege tests (issue #48, FR-06, FR-08,
FR-82).

Each constraint/privilege violation gets its own test function - see
`test_db_catalogue_entry.py`'s own module docstring for why (a failed
statement aborts the surrounding transaction, 25P02).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc_shared.sctid import has_valid_check_digit, has_valid_format

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

#: PRD SS6.4/FR-82's own regression fixture: `391483001` |Microscopy (acid
#: fast bacilli) (procedure)|.
_VALID_CODE = "391483001"
_VALID_FSN = "Microscopy (acid fast bacilli) (procedure)"
_VALID_AU_PREFERRED_TERM = "Microscopy (acid fast bacilli)"

_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term) "
    "VALUES (:business_key, :preferred_term) RETURNING id"
)
_INSERT_BINDING = text(
    "INSERT INTO code_binding "
    "(entry_id, system, code, fsn, au_preferred_term, edition_hint, status, "
    "replaced_by_binding_id, retirement_reason) "
    "VALUES (:entry_id, :system, :code, :fsn, :au_preferred_term, :edition_hint, :status, "
    ":replaced_by_binding_id, :retirement_reason) "
    "RETURNING id"
)


def _insert_entry(
    connection: Connection,
    *,
    business_key: str = "NPTC-200001",
    preferred_term: str = "Full blood count",
) -> object:
    return connection.execute(
        _INSERT_ENTRY, {"business_key": business_key, "preferred_term": preferred_term}
    ).scalar_one()


def _insert_binding(
    connection: Connection,
    *,
    entry_id: object,
    system: str = "http://snomed.info/sct",
    code: str = _VALID_CODE,
    fsn: str = _VALID_FSN,
    au_preferred_term: str | None = _VALID_AU_PREFERRED_TERM,
    edition_hint: str = "au",
    status: str = "active",
    replaced_by_binding_id: object | None = None,
    retirement_reason: str | None = None,
) -> object:
    return connection.execute(
        _INSERT_BINDING,
        {
            "entry_id": entry_id,
            "system": system,
            "code": code,
            "fsn": fsn,
            "au_preferred_term": au_preferred_term,
            "edition_hint": edition_hint,
            "status": status,
            "replaced_by_binding_id": replaced_by_binding_id,
            "retirement_reason": retirement_reason,
        },
    ).scalar_one()


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_fsn_round_trips_byte_for_byte_with_the_semantic_tag_intact(db: Connection) -> None:
    """The FR-82/PRD SS6.4 regression fixture: `391483001`'s FSN survives
    storage exactly as served, tag intact - no cleaning, no stripping."""
    entry_id = _insert_entry(db)
    binding_id = _insert_binding(db, entry_id=entry_id)

    row = db.execute(
        text("SELECT fsn, au_preferred_term FROM code_binding WHERE id = :id"),
        {"id": binding_id},
    ).one()

    assert row.fsn == _VALID_FSN
    assert row.au_preferred_term == _VALID_AU_PREFERRED_TERM


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_code_of_18_digits_is_accepted(db: Connection) -> None:
    """FR-07's own boundary case (16/17/18-digit SCTIDs) needs a genuinely
    valid 18-digit code to exist at all in the fixture set this table can
    accept - `111111111111111118` (seventeen `1`s plus a check digit of
    `8`), chosen so its Verhoeff checksum reduces to zero (verified below
    against `nptc_shared.sctid.has_valid_check_digit`, not merely
    asserted)."""
    entry_id = _insert_entry(db)
    eighteen_digit_code = "111111111111111118"
    assert has_valid_format(eighteen_digit_code)
    assert has_valid_check_digit(eighteen_digit_code)

    _insert_binding(db, entry_id=entry_id, code=eighteen_digit_code)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_code_too_short_is_rejected(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, code="12345")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_code_with_non_digit_characters_is_rejected(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, code="39148300X")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_format_valid_but_verhoeff_failing_code_is_rejected(db: Connection) -> None:
    """`391483002` is the same length and digit shape as the valid fixture
    but fails the Verhoeff checksum - format alone must not be enough."""
    entry_id = _insert_entry(db)
    candidate = "391483002"
    assert has_valid_format(candidate)
    assert not has_valid_check_digit(candidate)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, code=candidate)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_fsn_cannot_be_blank(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, fsn="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_au_preferred_term_may_be_null(db: Connection) -> None:
    """Not every edition serves an AU preferred term - `nptc_transform.
    dataset` already types this `str | None`."""
    entry_id = _insert_entry(db)

    _insert_binding(db, entry_id=entry_id, au_preferred_term=None, edition_hint="int")


@pytest.mark.integration
def test_au_preferred_term_cannot_be_blank_when_present(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, au_preferred_term="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_edition_hint_is_constrained(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, edition_hint="made_up_edition")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_status_is_constrained(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, status="made_up_status")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_second_active_binding_on_the_same_entry_is_refused(db: Connection) -> None:
    entry_id = _insert_entry(db)
    _insert_binding(db, entry_id=entry_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, code="71388002", fsn="Procedure (procedure)")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_same_code_active_on_two_entries_is_refused(db: Connection) -> None:
    """Issue #49's blocking severity: `ix_code_binding_one_active_per_entry`
    only rules out one entry holding two active bindings, not the same
    code bound active on two *different* entries."""
    first_entry_id = _insert_entry(db)
    second_entry_id = _insert_entry(db, business_key="NPTC-200002", preferred_term="Other")
    _insert_binding(db, entry_id=first_entry_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=second_entry_id)

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_same_code_is_rebindable_once_the_first_binding_is_retired(db: Connection) -> None:
    first_entry_id = _insert_entry(db)
    second_entry_id = _insert_entry(db, business_key="NPTC-200003", preferred_term="Other")
    _insert_binding(
        db,
        entry_id=first_entry_id,
        status="retired",
        retirement_reason="Superseded",
    )

    _insert_binding(db, entry_id=second_entry_id)


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retiring_without_a_reason_is_refused(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, status="retired", retirement_reason=None)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retiring_with_a_blank_reason_is_refused(db: Connection) -> None:
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, status="retired", retirement_reason="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_active_binding_with_a_retirement_reason_is_refused(db: Connection) -> None:
    """A stale reason cannot linger on a binding that is active - mandatory
    exactly when retired, forbidden otherwise."""
    entry_id = _insert_entry(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(db, entry_id=entry_id, status="active", retirement_reason="superseded")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_active_binding_with_replaced_by_is_refused(db: Connection) -> None:
    """`replaced_by_binding_id` may only be set on a binding that is
    itself retired - a binding cannot be active and superseded at once.
    Two different entries, so the FR-08 "one active binding per entry"
    index can't also explain the rejection."""
    successor_entry_id = _insert_entry(db, business_key="NPTC-200008")
    successor_id = _insert_binding(
        db,
        entry_id=successor_entry_id,
        code="71388002",
        fsn="Procedure (procedure)",
        status="active",
    )
    other_entry_id = _insert_entry(db, business_key="NPTC-200009")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_binding(
            db,
            entry_id=other_entry_id,
            code="123037004",
            fsn="Body structure (body structure)",
            status="active",
            replaced_by_binding_id=successor_id,
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_self_supersession_is_refused(db: Connection) -> None:
    entry_id = _insert_entry(db)
    binding_id = _insert_binding(db, entry_id=entry_id)

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "UPDATE code_binding SET status = 'retired', retirement_reason = 'x', "
                "replaced_by_binding_id = id WHERE id = :id"
            ),
            {"id": binding_id},
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retired_binding_may_name_its_successor(db: Connection) -> None:
    entry_id = _insert_entry(db)
    superseded_id = _insert_binding(db, entry_id=entry_id)

    # Retire the superseded binding before inserting its replacement - the
    # FR-08 "one active binding per entry" index would otherwise refuse a
    # second concurrently-active row on the same entry.
    db.execute(
        text(
            "UPDATE code_binding SET status = 'retired', retirement_reason = 'superseded' "
            "WHERE id = :id"
        ),
        {"id": superseded_id},
    )
    successor_id = _insert_binding(
        db, entry_id=entry_id, code="71388002", fsn="Procedure (procedure)", status="active"
    )

    db.execute(
        text("UPDATE code_binding SET replaced_by_binding_id = :successor_id WHERE id = :id"),
        {"id": superseded_id, "successor_id": successor_id},
    )

    row = db.execute(
        text("SELECT status, replaced_by_binding_id FROM code_binding WHERE id = :id"),
        {"id": superseded_id},
    ).one()
    assert row.status == "retired"
    assert row.replaced_by_binding_id == successor_id


@pytest.mark.integration
def test_app_role_can_insert_select_and_update(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-200002")
    binding_id = _insert_binding(app_db, entry_id=entry_id)

    app_db.execute(
        text(
            "UPDATE code_binding SET status = 'retired', retirement_reason = 'superseded' "
            "WHERE id = :id"
        ),
        {"id": binding_id},
    )
    row = app_db.execute(
        text("SELECT status FROM code_binding WHERE id = :id"), {"id": binding_id}
    ).one()
    assert row.status == "retired"


@pytest.mark.integration
def test_app_role_is_refused_update_of_code(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-200003")
    binding_id = _insert_binding(app_db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE code_binding SET code = '71388002' WHERE id = :id"), {"id": binding_id}
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_update_of_entry_id(app_db: Connection) -> None:
    first_entry = _insert_entry(app_db, business_key="NPTC-200004")
    second_entry = _insert_entry(app_db, business_key="NPTC-200005", preferred_term="Other")
    binding_id = _insert_binding(app_db, entry_id=first_entry)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE code_binding SET entry_id = :entry_id WHERE id = :id"),
            {"entry_id": second_entry, "id": binding_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_delete(app_db: Connection) -> None:
    """A binding is retained once published, never deleted - FR-08's own
    acceptance criterion, enforced at the privilege level."""
    entry_id = _insert_entry(app_db, business_key="NPTC-200006")
    binding_id = _insert_binding(app_db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM code_binding WHERE id = :id"), {"id": binding_id})

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_truncate(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db, business_key="NPTC-200007")
    _insert_binding(app_db, entry_id=entry_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE code_binding"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]
