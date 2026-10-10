"""`submission` constraint, privilege and audit-policy tests (FR-23, FR-27, FR-28, FR-29, NFR-08).

Each violation gets its own test, because a failed statement aborts the surrounding transaction
(25P02); see `test_db_catalogue_entry.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.audit.policy import policy_for
from nptc.db.models.submission import (
    _KIND_CHECK_SQL,
    _STATE_CHECK_SQL,
    Submission,
    SubmissionKind,
    SubmissionState,
)

_FOREIGN_KEY_VIOLATION = "23503"
_CHECK_VIOLATION = "23514"
_INSUFFICIENT_PRIVILEGE = "42501"

_VALID_CODE = "391483001"
#: `_VALID_CODE` with its check digit changed.
_BAD_CHECK_DIGIT_CODE = "391483009"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Submission tester') "
    "RETURNING id"
)


def _insert_user(connection: Connection) -> object:
    return connection.execute(_INSERT_USER, {"username": f"sub-{uuid.uuid4()}"}).scalar_one()


_INSERT_SUBMISSION = text(
    "INSERT INTO submission (kind, state, preferred_term, synonyms, snomed_code, snomed_fsn, "
    "property_values, notes, reference_url, reference_checked_at, reference_status, "
    "duplicate_confirmed_at, duplicate_matches, submitter_id, organisation) "
    "VALUES (:kind, :state, :preferred_term, CAST(:synonyms AS jsonb), :snomed_code, :snomed_fsn, "
    "CAST(:property_values AS jsonb), :notes, :reference_url, :reference_checked_at, "
    ":reference_status, :duplicate_confirmed_at, CAST(:duplicate_matches AS jsonb), "
    ":submitter_id, :organisation) RETURNING id"
)
#: Only the columns with a server default are left out, so a test sees the defaults themselves. A
#: new test must carry its reference, which has no default.
_INSERT_MINIMAL_SUBMISSION = text(
    "INSERT INTO submission (kind, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', 'Serum sodium', 'https://example.org/evidence', now(), 200, "
    ":submitter_id) RETURNING id"
)


def _insert_submission(connection: Connection, submitter_id: object, **overrides: object) -> object:
    """Inserts a valid row, then lets `overrides` replace any column, so a test aims at one
    constraint at a time. A JSON column is passed as JSON text."""
    values: dict[str, object] = {
        "kind": "new_test",
        "state": "Submitted",
        "preferred_term": "Serum sodium",
        "synonyms": "[]",
        "snomed_code": None,
        "snomed_fsn": None,
        "property_values": "{}",
        "notes": None,
        "reference_url": "https://example.org/evidence",
        "reference_checked_at": datetime.now(UTC),
        "reference_status": 200,
        "duplicate_confirmed_at": None,
        "duplicate_matches": "[]",
        "submitter_id": submitter_id,
        "organisation": None,
    }
    values.update(overrides)
    return connection.execute(_INSERT_SUBMISSION, values).scalar_one()


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_app_role_can_insert_and_select_a_submission(app_db: Connection) -> None:
    submitter = _insert_user(app_db)
    submission_id = _insert_submission(
        app_db,
        submitter,
        snomed_code=_VALID_CODE,
        snomed_fsn="Serum sodium measurement (procedure)",
        notes="Seen in a new analyser panel",
        organisation="Example Pathology",
    )

    row = app_db.execute(
        text(
            "SELECT kind, preferred_term, snomed_code, organisation FROM submission WHERE id = :id"
        ),
        {"id": submission_id},
    ).one()
    assert (row.kind, row.preferred_term, row.snomed_code) == (
        "new_test",
        "Serum sodium",
        _VALID_CODE,
    )
    assert row.organisation == "Example Pathology"


@pytest.mark.req("FR-25")
@pytest.mark.integration
def test_a_confirmed_duplicate_keeps_the_matches_and_the_time(db: Connection) -> None:
    submitter = _insert_user(db)
    confirmed_at = datetime(2026, 1, 1, tzinfo=UTC)

    submission_id = _insert_submission(
        db,
        submitter,
        duplicate_confirmed_at=confirmed_at,
        duplicate_matches='[{"source": "submission", "key": "k"}]',
    )

    row = db.execute(
        text("SELECT duplicate_confirmed_at, duplicate_matches FROM submission WHERE id = :id"),
        {"id": submission_id},
    ).one()
    assert row.duplicate_confirmed_at == confirmed_at
    assert row.duplicate_matches == [{"source": "submission", "key": "k"}]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_new_row_defaults_to_submitted_with_empty_json_and_version_one(db: Connection) -> None:
    submission_id = db.execute(
        _INSERT_MINIMAL_SUBMISSION, {"submitter_id": _insert_user(db)}
    ).scalar_one()

    row = db.execute(
        text(
            "SELECT state, synonyms, property_values, duplicate_matches, duplicate_confirmed_at, "
            "row_version, created_at, updated_at "
            "FROM submission WHERE id = :id"
        ),
        {"id": submission_id},
    ).one()
    assert row.state == SubmissionState.SUBMITTED
    assert row.synonyms == []
    assert row.property_values == {}
    assert row.row_version == 1
    assert row.duplicate_matches == []
    assert row.duplicate_confirmed_at is None
    assert row.created_at is not None
    assert row.updated_at is not None


@pytest.mark.integration
def test_a_submission_must_reference_a_real_user(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_submission(db, "00000000-0000-0000-0000-000000000000")

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


#: One violating column value per case, so a failure names the constraint that stopped holding. Each
#: statement is otherwise valid.
_CHECK_VIOLATIONS: dict[str, dict[str, object]] = {
    "unknown_kind": {"kind": "retraction"},
    "unknown_state": {"state": "Approved"},
    "empty_preferred_term": {"preferred_term": ""},
    "blank_preferred_term": {"preferred_term": "   "},
    "synonyms_not_an_array": {"synonyms": '{"a": 1}'},
    "property_values_not_an_object": {"property_values": '["a"]'},
    "code_not_a_sctid": {"snomed_code": "not-a-code", "snomed_fsn": "Label"},
    "code_with_bad_check_digit": {"snomed_code": _BAD_CHECK_DIGIT_CODE, "snomed_fsn": "Label"},
    "code_without_fsn": {"snomed_code": _VALID_CODE},
    "fsn_without_code": {"snomed_fsn": "Label"},
    "blank_fsn": {"snomed_code": _VALID_CODE, "snomed_fsn": "  "},
    "blank_notes": {"notes": "  "},
    "new_test_without_reference": {
        "reference_url": None,
        "reference_checked_at": None,
        "reference_status": None,
    },
    "blank_reference_url": {"reference_url": "  "},
    "reference_without_check_time": {"reference_checked_at": None},
    "reference_without_status": {"reference_status": None},
    "check_without_reference": {"reference_url": None},
    "duplicate_matches_not_an_array": {"duplicate_matches": '{"a": 1}'},
    "matches_without_confirmation_time": {"duplicate_matches": '[{"source": "submission"}]'},
    "confirmation_time_without_matches": {
        "duplicate_confirmed_at": datetime(2026, 1, 1, tzinfo=UTC)
    },
}


@pytest.mark.req("FR-06")
@pytest.mark.integration
@pytest.mark.parametrize("overrides", _CHECK_VIOLATIONS.values(), ids=_CHECK_VIOLATIONS.keys())
def test_check_constraints_refuse_a_malformed_row(
    db: Connection, overrides: dict[str, object]
) -> None:
    submitter = _insert_user(db)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_submission(db, submitter, **overrides)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_an_amendment_may_have_no_reference(db: Connection) -> None:
    submitter = _insert_user(db)

    _insert_submission(
        db,
        submitter,
        kind="amendment",
        reference_url=None,
        reference_checked_at=None,
        reference_status=None,
    )


@pytest.mark.req("FR-06")
def test_kind_and_state_checks_list_exactly_the_enum_values() -> None:
    assert set(re.findall(r"'([^']*)'", _KIND_CHECK_SQL)) == {k.value for k in SubmissionKind}
    assert set(re.findall(r"'([^']*)'", _STATE_CHECK_SQL)) == {s.value for s in SubmissionState}


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_app_role_can_move_state_but_the_row_keeps_what_was_sent(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db, _insert_user(app_db))

    app_db.execute(
        text(
            "UPDATE submission SET state = 'Submitted', updated_at = now(), "
            "row_version = row_version + 1 WHERE id = :id"
        ),
        {"id": submission_id},
    )

    row = app_db.execute(
        text("SELECT row_version FROM submission WHERE id = :id"), {"id": submission_id}
    ).one()
    assert row.row_version == 2


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids name
#: the column or verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update_preferred_term": "UPDATE submission SET preferred_term = 'changed'",
    "update_snomed_code": "UPDATE submission SET snomed_code = NULL, snomed_fsn = NULL",
    "update_property_values": "UPDATE submission SET property_values = '{}'::jsonb",
    "update_notes": "UPDATE submission SET notes = 'changed'",
    "update_reference_url": "UPDATE submission SET reference_url = 'https://example.org/other'",
    "update_reference_status": "UPDATE submission SET reference_status = 200",
    "update_reference_checked_at": "UPDATE submission SET reference_checked_at = now()",
    "update_duplicate_confirmed_at": "UPDATE submission SET duplicate_confirmed_at = now()",
    "update_duplicate_matches": "UPDATE submission SET duplicate_matches = '[]'::jsonb",
    "update_organisation": "UPDATE submission SET organisation = 'changed'",
    "update_submitter": "UPDATE submission SET submitter_id = submitter_id",
    "update_created_at": "UPDATE submission SET created_at = now()",
    "delete": "DELETE FROM submission",
    "truncate": "TRUNCATE submission",
}


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_cannot_edit_content_or_remove_a_submission(
    app_db: Connection, statement: str
) -> None:
    _insert_submission(app_db, _insert_user(app_db))

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-08")
def test_the_audit_policy_withholds_the_organisation_and_records_the_rest() -> None:
    policy = policy_for(Submission)

    assert policy.withheld == frozenset({"organisation"})
    assert policy.auditable == frozenset(
        {
            "kind",
            "state",
            "preferred_term",
            "synonyms",
            "snomed_code",
            "snomed_fsn",
            "property_values",
            "notes",
            "reference_url",
            "reference_checked_at",
            "reference_status",
            "duplicate_confirmed_at",
            "duplicate_matches",
            "submitter_id",
        }
    )
