"""local_code_system/local_code/local_code_snomed_map constraint and
privilege tests (issue #56, FR-90, FR-91, FR-92).

Each constraint/privilege violation gets its own test function - see
`test_db_code_binding.py`'s own module docstring for why (a failed
statement aborts the surrounding transaction, 25P02).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_SYSTEM = text(
    "INSERT INTO local_code_system (key, uri, title, description, owner) "
    "VALUES (:key, :uri, :title, :description, :owner) RETURNING id"
)
_INSERT_CODE = text(
    "INSERT INTO local_code "
    "(system_id, code, display, definition, provisional, status, "
    "deprecated_at, deprecation_reason) "
    "VALUES (:system_id, :code, :display, :definition, :provisional, :status, "
    ":deprecated_at, :deprecation_reason) "
    "RETURNING id"
)
_INSERT_MAP_ROW = text(
    "INSERT INTO local_code_snomed_map "
    "(local_code_id, system, code, display, match_strength, advisory_note) "
    "VALUES (:local_code_id, :system, :code, :display, :match_strength, :advisory_note) "
    "RETURNING id"
)

#: FR-06/`code_binding`'s own regression fixture, reused here since the map
#: row's `code` shares the same `nptc_sctid_is_valid` check.
_VALID_SNOMED_CODE = "394596001"


def _insert_system(
    connection: Connection,
    *,
    key: str = "discipline_test",
    uri: str = "https://nptc.example.org/CodeSystem/discipline_test",
    title: str = "Discipline (test)",
    description: str = "Test fixture",
    owner: str = "RCPA-QAP",
) -> object:
    return connection.execute(
        _INSERT_SYSTEM,
        {"key": key, "uri": uri, "title": title, "description": description, "owner": owner},
    ).scalar_one()


def _insert_code(
    connection: Connection,
    *,
    system_id: object,
    code: str = "chemical_pathology",
    display: str = "Chemical pathology",
    definition: str | None = None,
    provisional: bool = False,
    status: str = "active",
    deprecated_at: object | None = None,
    deprecation_reason: str | None = None,
) -> object:
    return connection.execute(
        _INSERT_CODE,
        {
            "system_id": system_id,
            "code": code,
            "display": display,
            "definition": definition,
            "provisional": provisional,
            "status": status,
            "deprecated_at": deprecated_at,
            "deprecation_reason": deprecation_reason,
        },
    ).scalar_one()


def _insert_map_row(
    connection: Connection,
    *,
    local_code_id: object,
    system: str = "http://snomed.info/sct",
    code: str = _VALID_SNOMED_CODE,
    display: str = "Chemical pathology",
    match_strength: str = "exact",
    advisory_note: str = "Advisory only, not a code_binding: test fixture.",
) -> object:
    return connection.execute(
        _INSERT_MAP_ROW,
        {
            "local_code_id": local_code_id,
            "system": system,
            "code": code,
            "display": display,
            "match_strength": match_strength,
            "advisory_note": advisory_note,
        },
    ).scalar_one()


# --- local_code_system ---


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_key_must_match_the_property_definition_pattern(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_system(db, key="Not A Valid Key", uri="https://nptc.example.org/x1")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_key_is_unique(db: Connection) -> None:
    _insert_system(db, key="uniqueness_test", uri="https://nptc.example.org/u1")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_system(db, key="uniqueness_test", uri="https://nptc.example.org/u2")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_uri_is_unique(db: Connection) -> None:
    _insert_system(db, key="uri_test_1", uri="https://nptc.example.org/shared")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_system(db, key="uri_test_2", uri="https://nptc.example.org/shared")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_title_cannot_be_blank(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_system(db, key="blank_title", uri="https://nptc.example.org/bt", title="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_owner_cannot_be_blank(db: Connection) -> None:
    """FR-90: "owned by RCPA-QAP" is a recorded fact, not an assumption -
    the column cannot be silently empty."""
    with pytest.raises(IntegrityError) as exc_info:
        _insert_system(db, key="blank_owner", uri="https://nptc.example.org/bo", owner="  ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_system_status_is_constrained(db: Connection) -> None:
    system_id = _insert_system(db, key="bad_status", uri="https://nptc.example.org/bs")

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text("UPDATE local_code_system SET status = 'made_up' WHERE id = :id"),
            {"id": system_id},
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


# --- local_code ---


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_code_is_unique_within_a_system(db: Connection) -> None:
    system_id = _insert_system(db, key="dup_code_system", uri="https://nptc.example.org/dc")
    _insert_code(db, system_id=system_id, code="chemical_pathology")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_code(db, system_id=system_id, code="chemical_pathology")

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.integration
def test_same_code_is_permitted_in_two_different_systems(db: Connection) -> None:
    first_system_id = _insert_system(db, key="system_one", uri="https://nptc.example.org/s1")
    second_system_id = _insert_system(db, key="system_two", uri="https://nptc.example.org/s2")

    _insert_code(db, system_id=first_system_id, code="shared_code")
    _insert_code(db, system_id=second_system_id, code="shared_code")


@pytest.mark.req("FR-92")
@pytest.mark.integration
def test_provisional_code_may_have_no_definition(db: Connection) -> None:
    """FR-92: a Subgroup string migrated verbatim ahead of RCPA-QAP
    settling the vocabulary is marked provisional, with no definition
    yet."""
    system_id = _insert_system(db, key="subgroup_test", uri="https://nptc.example.org/sg")

    code_id = _insert_code(
        db,
        system_id=system_id,
        code="coagulation",
        display="Coagulation",
        definition=None,
        provisional=True,
    )

    row = db.execute(
        text("SELECT provisional, definition FROM local_code WHERE id = :id"), {"id": code_id}
    ).one()
    assert row.provisional is True
    assert row.definition is None


@pytest.mark.integration
def test_display_cannot_be_blank(db: Connection) -> None:
    system_id = _insert_system(db, key="blank_display", uri="https://nptc.example.org/bd")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_code(db, system_id=system_id, display="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_deprecating_without_a_reason_is_refused(db: Connection) -> None:
    system_id = _insert_system(db, key="deprecate_no_reason", uri="https://nptc.example.org/dnr")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_code(db, system_id=system_id, status="deprecated", deprecation_reason=None)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-90")
@pytest.mark.integration
def test_active_code_with_a_deprecation_reason_is_refused(db: Connection) -> None:
    system_id = _insert_system(db, key="active_with_reason", uri="https://nptc.example.org/awr")

    with pytest.raises(IntegrityError) as exc_info:
        _insert_code(db, system_id=system_id, status="active", deprecation_reason="superseded")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


# --- local_code_snomed_map ---


@pytest.mark.req("FR-91")
@pytest.mark.integration
def test_map_row_code_must_be_a_valid_sctid(db: Connection) -> None:
    system_id = _insert_system(db, key="map_bad_code", uri="https://nptc.example.org/mbc")
    code_id = _insert_code(db, system_id=system_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_map_row(db, local_code_id=code_id, code="not-a-code")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-91")
@pytest.mark.integration
def test_map_row_match_strength_is_constrained(db: Connection) -> None:
    system_id = _insert_system(db, key="map_bad_strength", uri="https://nptc.example.org/mbs")
    code_id = _insert_code(db, system_id=system_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_map_row(db, local_code_id=code_id, match_strength="perfect")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-91")
@pytest.mark.integration
def test_map_row_advisory_note_cannot_be_blank(db: Connection) -> None:
    """FR-91: the map MUST be explicit that it is advisory - every row
    carries its own caveat, never an empty one."""
    system_id = _insert_system(db, key="map_blank_note", uri="https://nptc.example.org/mbn")
    code_id = _insert_code(db, system_id=system_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_map_row(db, local_code_id=code_id, advisory_note="   ")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-91")
@pytest.mark.integration
def test_one_local_code_may_have_two_ambiguous_map_rows(db: Connection) -> None:
    """PRD SS6.6: `Microbiology` is genuinely ambiguous between two SNOMED
    candidates - no uniqueness constraint on `local_code_id` collapses
    this to one row."""
    system_id = _insert_system(db, key="microbiology_map", uri="https://nptc.example.org/mm")
    code_id = _insert_code(db, system_id=system_id, code="microbiology", display="Microbiology")

    _insert_map_row(
        db,
        local_code_id=code_id,
        code="408454008",
        display="Clinical microbiology",
        match_strength="ambiguous",
    )
    _insert_map_row(
        db,
        local_code_id=code_id,
        code="394820005",
        display="Medical microbiology",
        match_strength="ambiguous",
    )

    count = db.execute(
        text("SELECT count(*) FROM local_code_snomed_map WHERE local_code_id = :id"),
        {"id": code_id},
    ).scalar_one()
    assert count == 2


# --- privileges ---


@pytest.mark.integration
def test_app_role_can_insert_select_and_update_local_code_system(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="app_role_system", uri="https://nptc.example.org/ars")

    app_db.execute(
        text("UPDATE local_code_system SET status = 'deprecated' WHERE id = :id"),
        {"id": system_id},
    )
    row = app_db.execute(
        text("SELECT status FROM local_code_system WHERE id = :id"), {"id": system_id}
    ).one()
    assert row.status == "deprecated"


@pytest.mark.integration
def test_app_role_is_refused_update_of_local_code_system_key(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="immutable_key", uri="https://nptc.example.org/ik")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE local_code_system SET key = 'renamed' WHERE id = :id"),
            {"id": system_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_delete_of_local_code_system(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="no_delete_system", uri="https://nptc.example.org/nds")

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM local_code_system WHERE id = :id"), {"id": system_id})

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_update_of_local_code_code(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="app_role_code", uri="https://nptc.example.org/arc")
    code_id = _insert_code(app_db, system_id=system_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE local_code SET code = 'renamed' WHERE id = :id"), {"id": code_id}
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_delete_of_local_code(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="no_delete_code", uri="https://nptc.example.org/ndc")
    code_id = _insert_code(app_db, system_id=system_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM local_code WHERE id = :id"), {"id": code_id})

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_can_insert_and_select_map_row(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="app_role_map", uri="https://nptc.example.org/arm")
    code_id = _insert_code(app_db, system_id=system_id)

    map_row_id = _insert_map_row(app_db, local_code_id=code_id)

    row = app_db.execute(
        text("SELECT id FROM local_code_snomed_map WHERE id = :id"), {"id": map_row_id}
    ).one()
    assert row.id == map_row_id


@pytest.mark.req("FR-91")
@pytest.mark.integration
def test_app_role_is_refused_update_of_map_row(app_db: Connection) -> None:
    """FR-91: an advisory map row is never edited, only replaced - see
    `LocalCodeSnomedMap`'s own module docstring."""
    system_id = _insert_system(app_db, key="no_update_map", uri="https://nptc.example.org/num")
    code_id = _insert_code(app_db, system_id=system_id)
    map_row_id = _insert_map_row(app_db, local_code_id=code_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE local_code_snomed_map SET advisory_note = 'edited' WHERE id = :id"),
            {"id": map_row_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-91")
@pytest.mark.integration
def test_app_role_is_refused_delete_of_map_row(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="no_delete_map", uri="https://nptc.example.org/ndm")
    code_id = _insert_code(app_db, system_id=system_id)
    map_row_id = _insert_map_row(app_db, local_code_id=code_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("DELETE FROM local_code_snomed_map WHERE id = :id"), {"id": map_row_id})

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_app_role_is_refused_truncate_of_map_row(app_db: Connection) -> None:
    system_id = _insert_system(app_db, key="no_truncate_map", uri="https://nptc.example.org/ntm")
    code_id = _insert_code(app_db, system_id=system_id)
    _insert_map_row(app_db, local_code_id=code_id)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("TRUNCATE local_code_snomed_map"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.integration
def test_seeded_discipline_system_and_map_exist(db: Connection) -> None:
    """Migration 0010's seed data (FR-90/FR-91): the six PRD SS6.6
    disciplines exist, Molecular and Serology have no map row, and
    Microbiology has exactly two."""
    system_row = db.execute(text("SELECT id FROM local_code_system WHERE key = 'discipline'")).one()

    codes = (
        db.execute(text("SELECT code FROM local_code WHERE system_id = :id"), {"id": system_row.id})
        .scalars()
        .all()
    )
    assert set(codes) == {
        "chemical_pathology",
        "haematology",
        "immunopathology",
        "microbiology",
        "molecular",
        "serology",
    }

    for unmapped in ("molecular", "serology"):
        code_id = db.execute(
            text("SELECT id FROM local_code WHERE system_id = :sid AND code = :code"),
            {"sid": system_row.id, "code": unmapped},
        ).scalar_one()
        map_count = db.execute(
            text("SELECT count(*) FROM local_code_snomed_map WHERE local_code_id = :id"),
            {"id": code_id},
        ).scalar_one()
        assert map_count == 0, f"{unmapped} must have no advisory map row (FR-91)"

    microbiology_id = db.execute(
        text("SELECT id FROM local_code WHERE system_id = :sid AND code = 'microbiology'"),
        {"sid": system_row.id},
    ).scalar_one()
    microbiology_map_count = db.execute(
        text("SELECT count(*) FROM local_code_snomed_map WHERE local_code_id = :id"),
        {"id": microbiology_id},
    ).scalar_one()
    assert microbiology_map_count == 2
