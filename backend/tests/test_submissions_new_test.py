"""`nptc.submissions.new_test.create_new_test_submission` service-layer tests (FR-23, FR-24, FR-26,
FR-27, NFR-08).

Each refusal asserts that no `submission` row and no audit event was added, because a refused
request must leave nothing behind. Rows are counted for the submitter this test created, and audit
events as a delta, because `backend/tests` shares one Postgres container (see `CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.audit.diffing import REDACTED_KEY
from nptc.audit.writer import AuditContext
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import PropertyValidationError, PropertyValueInput
from nptc.catalogue.term_hygiene import TermCleaningError
from nptc.db.bootstrap import seed_system_properties
from nptc.db.definitions import deprecate_definition
from nptc.db.models.audit import AuditEvent
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.user import User
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc.submissions.errors import CodeRefusal, FreeTextRefusedError, SubmissionCodeRefusedError
from nptc.submissions.new_test import NewTestSubmissionInput, create_new_test_submission
from nptc.submissions.reference_check import (
    ReferenceCheckFailedError,
    ReferenceCheckResult,
    ReferenceCheckUnavailableError,
    ReferenceFailure,
)
from nptc.terminology.errors import TerminologyUnavailableError
from nptc_shared.sctid import InvalidSCTIDError
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    SNOMED_SYSTEM,
    ConceptProperty,
    Designation,
    LookupResult,
    Operation,
    StubConcept,
    StubTerminologyClient,
    TerminologyStatusError,
    TerminologyTimeoutError,
)
from nptc_shared.terminology.models import Edition, ValidationResult


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


StubReferenceChecker = _load("api_app_support").StubReferenceChecker

_REFERENCE_URL = "https://example.org/evidence"
_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_FSN_USE_CODE = "900000000000003001"
_PROFILE_ORGANISATION = "Profile Pathology"
NBSP = chr(0xA0)
ZERO_WIDTH_SPACE = chr(0x200B)


def _registry(session: Session, client: StubTerminologyClient) -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=client, local_code_lookup=DatabaseLocalCodeLookup(session)
            )
        )
    )


def _client_with_concept(*, active: bool = True) -> StubTerminologyClient:
    client = StubTerminologyClient()
    client.add_concept(
        StubConcept(
            code=_CODE,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            active=active,
        )
    )
    return client


def _submitter(session: Session) -> tuple[User, AuditContext]:
    user = User(
        username=f"submitter-{uuid.uuid4()}",
        display_name="Test Submitter",
        organisation=_PROFILE_ORGANISATION,
    )
    session.add(user)
    session.flush()
    ctx = AuditContext(
        actor_user_id=user.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )
    return user, ctx


def _property(
    session: Session,
    key: str,
    *,
    scope: PropertyScope = PropertyScope.SUBMISSION,
    required: bool = False,
    max_length: int | None = None,
) -> PropertyDefinition:
    definition = PropertyDefinition(
        key=key,
        label=key.replace("_", " ").title(),
        datatype="string",
        cardinality="0..1",
        scope=scope,
        required_for_submission=required,
        required_for_publication=False,
        filterable=False,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        constraints={"maxLength": max_length} if max_length is not None else {},
    )
    session.add(definition)
    session.flush()
    return definition


def _submission_count(session: Session, submitter_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == submitter_id)
    ).scalar_one()


def _audit_event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _create(
    session: Session,
    ctx: AuditContext,
    content: NewTestSubmissionInput,
    client: StubTerminologyClient | None = None,
    *,
    profile_organisation: str | None = _PROFILE_ORGANISATION,
    reference_checker: Any = None,
) -> Submission:
    terminology = client if client is not None else StubTerminologyClient()
    return create_new_test_submission(
        session,
        ctx,
        content=content,
        profile_organisation=profile_organisation,
        registry=_registry(session, terminology),
        terminology_client=terminology,
        reference_checker=reference_checker
        if reference_checker is not None
        else StubReferenceChecker(),
    )


# --- the stored record -------------------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_creates_a_new_test_submission_in_state_submitted(app_session: Session) -> None:
    user, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL,
            preferred_term="  Serum" + NBSP + "sodium  ",
            synonyms=["Sodium, serum", "Na (serum)"],
            notes="  Seen on the new analyser panel  ",
        ),
    )

    assert submission.kind == SubmissionKind.NEW_TEST
    assert submission.state == SubmissionState.SUBMITTED
    assert submission.submitter_id == user.id
    assert submission.preferred_term == "Serum sodium"
    assert submission.synonyms == ["Sodium, serum", "Na (serum)"]
    assert submission.notes == "Seen on the new analyser panel"
    assert submission.snomed_code is None
    assert submission.snomed_fsn is None
    assert submission.property_values == {}
    assert submission.row_version == 1


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_the_organisation_defaults_to_the_profile_value(app_session: Session) -> None:
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
    )

    assert submission.organisation == _PROFILE_ORGANISATION


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("Other Pathology", "Other Pathology"),
        ("  Other Pathology  ", "Other Pathology"),
        ("  ", None),
    ],
    ids=["override", "trimmed", "blank_means_none"],
)
def test_the_request_can_override_the_organisation(
    app_session: Session, requested: str, expected: str | None
) -> None:
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL, preferred_term="Serum sodium", organisation=requested
        ),
    )

    assert submission.organisation == expected


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_profile_without_an_organisation_leaves_the_copy_empty(app_session: Session) -> None:
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
        profile_organisation=None,
    )

    assert submission.organisation is None


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_one_audit_event_records_the_content_and_withholds_the_organisation(
    app_session: Session,
) -> None:
    _, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL, preferred_term="Serum sodium", notes="A note"
        ),
    )

    assert _audit_event_count(app_session) == before + 1
    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.action == "submission.created"
    assert event.entity_type == "submission"
    assert event.entity_id == str(submission.id)
    assert event.actor_user_id == ctx.actor_user_id
    assert event.before is None
    assert event.after is not None
    assert event.after["preferred_term"] == "Serum sodium"
    assert event.after["notes"] == "A note"
    assert event.after["state"] == "Submitted"
    assert REDACTED_KEY in event.after
    assert event.after[REDACTED_KEY] == ["organisation"]
    assert _PROFILE_ORGANISATION not in str(event.after)


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_submission_needs_a_human_submitter(app_session: Session) -> None:
    anonymous_ctx = AuditContext.system()
    before = _audit_event_count(app_session)

    with pytest.raises(ValueError, match="human submitter"):
        _create(
            app_session,
            anonymous_ctx,
            NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
        )

    assert _audit_event_count(app_session) == before


# --- the term (FR-63) --------------------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "term", ["", "   ", "Serum" + ZERO_WIDTH_SPACE + "sodium"], ids=["empty", "blank", "zero_width"]
)
def test_a_term_that_cannot_be_cleaned_is_refused(app_session: Session, term: str) -> None:
    user, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(TermCleaningError):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term=term),
        )

    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_synonym_that_cannot_be_cleaned_is_refused(app_session: Session) -> None:
    user, ctx = _submitter(app_session)

    with pytest.raises(TermCleaningError):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                synonyms=["Sodium", "  "],
            ),
        )

    assert _submission_count(app_session, user.id) == 0


# --- the SNOMED CT code (FR-26) ----------------------------------------------------------------


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_the_stored_fsn_is_the_one_the_server_returned(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    client = _client_with_concept()

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL,
            preferred_term="Acid fast bacilli microscopy",
            snomed_code=_CODE,
        ),
        client,
    )

    assert submission.snomed_code == _CODE
    assert submission.snomed_fsn == _FSN
    assert [(r.operation, r.detail) for r in client.requests] == [(Operation.LOOKUP, _CODE)]


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_a_code_the_server_does_not_know_is_refused_with_a_reason(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("not found", status_code=404))
    before = _audit_event_count(app_session)

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL, preferred_term="Serum sodium", snomed_code=_CODE
            ),
            client,
        )

    assert excinfo.value.reason is CodeRefusal.NOT_FOUND
    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_an_inactive_code_is_refused_with_a_reason(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL, preferred_term="Serum sodium", snomed_code=_CODE
            ),
            _client_with_concept(active=False),
        )

    assert excinfo.value.reason is CodeRefusal.INACTIVE
    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_a_code_whose_active_status_is_not_reported_is_refused(app_session: Session) -> None:
    """Hazard H-05: a server that omits the `inactive` property has not said the code is active."""
    user, ctx = _submitter(app_session)
    client = StubTerminologyClient()
    client.seed_lookup(
        _CODE,
        LookupResult(
            code=_CODE,
            system=SNOMED_SYSTEM,
            display="Acid fast bacilli microscopy",
            designations=(
                Designation(value=_FSN, use_system=SNOMED_SYSTEM, use_code=_FSN_USE_CODE),
            ),
            properties=(),
        ),
    )

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL, preferred_term="Serum sodium", snomed_code=_CODE
            ),
            client,
        )

    assert excinfo.value.reason is CodeRefusal.STATUS_NOT_REPORTED
    assert _submission_count(app_session, user.id) == 0


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_a_code_with_no_served_fsn_is_refused(app_session: Session) -> None:
    user, ctx = _submitter(app_session)
    client = StubTerminologyClient()
    client.seed_lookup(
        _CODE,
        LookupResult(
            code=_CODE,
            system=SNOMED_SYSTEM,
            display="Acid fast bacilli microscopy",
            properties=(ConceptProperty(code="inactive", value="false", value_type="boolean"),),
        ),
    )

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL, preferred_term="Serum sodium", snomed_code=_CODE
            ),
            client,
        )

    assert excinfo.value.reason is CodeRefusal.NO_FSN
    assert _submission_count(app_session, user.id) == 0


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_a_terminology_outage_is_reported_as_unavailable_not_as_a_bad_code(
    app_session: Session,
) -> None:
    user, ctx = _submitter(app_session)
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"))
    before = _audit_event_count(app_session)

    with pytest.raises(TerminologyUnavailableError):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL, preferred_term="Serum sodium", snomed_code=_CODE
            ),
            client,
        )

    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-06")
@pytest.mark.integration
@pytest.mark.parametrize("code", ["not-a-code", "391483009"], ids=["malformed", "bad_check_digit"])
def test_a_malformed_code_is_refused_without_asking_the_server(
    app_session: Session, code: str
) -> None:
    user, ctx = _submitter(app_session)
    client = _client_with_concept()

    with pytest.raises(InvalidSCTIDError):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL, preferred_term="Serum sodium", snomed_code=code
            ),
            client,
        )

    assert client.requests == ()
    assert _submission_count(app_session, user.id) == 0


# --- property values (FR-24) -------------------------------------------------------------------


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_submission_scoped_values_are_stored_under_their_property_key(
    app_session: Session,
) -> None:
    _property(app_session, "sub_analyte")
    _property(app_session, "sub_either_scope", scope=PropertyScope.BOTH)
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL,
            preferred_term="Serum sodium",
            property_values={
                "sub_analyte": [PropertyValueInput(value="Sodium", justification="Per panel")],
                "sub_either_scope": [PropertyValueInput(value="Plasma")],
            },
        ),
    )

    assert submission.property_values == {
        "sub_analyte": [{"value": "Sodium", "justification": "Per panel"}],
        "sub_either_scope": [{"value": "Plasma", "justification": None}],
    }


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_property_with_no_values_is_treated_as_absent(app_session: Session) -> None:
    _property(app_session, "sub_optional")
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL,
            preferred_term="Serum sodium",
            property_values={"sub_optional": []},
        ),
    )

    assert submission.property_values == {}


def _issue_codes(error: PropertyValidationError) -> list[tuple[str, str]]:
    return [(issue.property_key, issue.code) for issue in error.issues]


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_property_outside_submission_scope_is_refused(app_session: Session) -> None:
    _property(app_session, "sub_maintenance_only", scope=PropertyScope.MAINTENANCE)
    user, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={"sub_maintenance_only": [PropertyValueInput(value="x")]},
            ),
        )

    assert _issue_codes(excinfo.value) == [("sub_maintenance_only", "out-of-scope")]
    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_an_out_of_scope_coded_value_never_reaches_the_terminology_server(
    app_session: Session,
) -> None:
    """The scope check comes first, so a refused property costs no server call."""
    _property(app_session, "sub_maintenance_only", scope=PropertyScope.MAINTENANCE)
    _, ctx = _submitter(app_session)
    client = StubTerminologyClient()

    with pytest.raises(PropertyValidationError):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={"sub_maintenance_only": [PropertyValueInput(value="x")]},
            ),
            client,
        )

    assert client.requests == ()


@pytest.mark.req("FR-24")
@pytest.mark.integration
@pytest.mark.parametrize("key", ["length", "no_such_property"])
def test_an_unknown_property_or_a_computed_field_is_refused(app_session: Session, key: str) -> None:
    """`length` is computed from the term (FR-24, FR-85) and is no property at all."""
    user, ctx = _submitter(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={key: [PropertyValueInput(value=12)]},
            ),
        )

    assert _issue_codes(excinfo.value) == [(key, "unknown-property")]
    assert _submission_count(app_session, user.id) == 0


@pytest.mark.req("FR-24")
@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_a_deprecated_property_is_refused(app_session: Session) -> None:
    definition = _property(app_session, "sub_retired")
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason="Retired for the submission test",
    )
    app_session.flush()
    user, ctx = _submitter(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={"sub_retired": [PropertyValueInput(value="x")]},
            ),
        )

    assert _issue_codes(excinfo.value) == [("sub_retired", "deprecated-property")]
    assert _submission_count(app_session, user.id) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_value_the_registry_handler_refuses_is_refused(app_session: Session) -> None:
    _property(app_session, "sub_short", max_length=3)
    user, ctx = _submitter(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={"sub_short": [PropertyValueInput(value="too long")]},
            ),
        )

    assert [issue.property_key for issue in excinfo.value.issues] == ["sub_short"]
    assert _submission_count(app_session, user.id) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_missing_required_property_is_refused(app_session: Session) -> None:
    _property(app_session, "sub_required", required=True)
    user, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
        )

    assert ("sub_required", "required-property-missing") in _issue_codes(excinfo.value)
    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_required_property_given_no_values_counts_as_missing(app_session: Session) -> None:
    _property(app_session, "sub_required", required=True)
    _, ctx = _submitter(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={"sub_required": []},
            ),
        )

    assert ("sub_required", "required-property-missing") in _issue_codes(excinfo.value)


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_supplied_required_property_is_accepted(app_session: Session) -> None:
    _property(app_session, "sub_required", required=True)
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL,
            preferred_term="Serum sodium",
            property_values={"sub_required": [PropertyValueInput(value="given")]},
        ),
    )

    assert submission.property_values == {
        "sub_required": [{"value": "given", "justification": None}]
    }


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_deprecated_required_property_does_not_block_a_submission(app_session: Session) -> None:
    """FR-11: a deprecated property accepts no new values, so it cannot also be demanded."""
    definition = _property(app_session, "sub_retired_required", required=True)
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason="Retired for the submission test",
    )
    app_session.flush()
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
    )

    assert "sub_retired_required" not in submission.property_values


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_every_property_problem_is_reported_at_once(app_session: Session) -> None:
    _property(app_session, "sub_required", required=True)
    _property(app_session, "sub_maintenance_only", scope=PropertyScope.MAINTENANCE)
    _, ctx = _submitter(app_session)

    with pytest.raises(PropertyValidationError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                property_values={
                    "length": [PropertyValueInput(value=12)],
                    "sub_maintenance_only": [PropertyValueInput(value="x")],
                },
            ),
        )

    codes = _issue_codes(excinfo.value)
    assert ("length", "unknown-property") in codes
    assert ("sub_maintenance_only", "out-of-scope") in codes
    assert ("sub_required", "required-property-missing") in codes


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_maintenance_only_property_marked_required_does_not_block_a_submission(
    app_session: Session,
) -> None:
    """A submitter cannot supply a property whose scope excludes submissions, so it cannot be
    demanded of them either."""
    _property(
        app_session, "sub_maintenance_required", scope=PropertyScope.MAINTENANCE, required=True
    )
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
    )

    assert submission.property_values == {}


# --- synonyms are a set ------------------------------------------------------------------------


def _synonyms_of(app_session: Session, preferred_term: str, *synonyms: str) -> list[str]:
    _, ctx = _submitter(app_session)
    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL, preferred_term=preferred_term, synonyms=list(synonyms)
        ),
    )
    return list(submission.synonyms)


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_repeated_synonym_is_stored_once(app_session: Session) -> None:
    stored = _synonyms_of(
        app_session, "Serum sodium", "Sodium, serum", "Na (serum)", "Sodium, serum"
    )

    assert stored == ["Sodium, serum", "Na (serum)"]


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_synonyms_that_fold_to_one_comparison_key_keep_the_first_spelling(
    app_session: Session,
) -> None:
    """The comparison key is the one `add_synonyms` folds on, so the later step that turns a
    submission into an entry meets no duplicate the submitter was told was accepted."""
    stored = _synonyms_of(app_session, "Serum sodium", "ADA2", "ada2", "Ada2")

    assert stored == ["ADA2"]


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_synonym_that_folds_to_the_preferred_term_is_dropped(app_session: Session) -> None:
    stored = _synonyms_of(app_session, "Serum sodium", "serum sodium", "Sodium, serum")

    assert stored == ["Sodium, serum"]


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_distinct_synonyms_keep_the_order_given(app_session: Session) -> None:
    stored = _synonyms_of(app_session, "Serum sodium", "Zinc", "Alpha", "Mid")

    assert stored == ["Zinc", "Alpha", "Mid"]


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_bad_synonym_is_refused_even_when_it_would_be_a_duplicate(app_session: Session) -> None:
    with pytest.raises(TermCleaningError):
        _synonyms_of(app_session, "Serum sodium", "Sodium", "Sodium" + ZERO_WIDTH_SPACE)


# --- free text (FR-63) -------------------------------------------------------------------------

_RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
_BELL = chr(0x07)


@pytest.mark.req("FR-63")
@pytest.mark.integration
@pytest.mark.parametrize("field", ["notes", "organisation"])
@pytest.mark.parametrize(
    "character",
    [ZERO_WIDTH_SPACE, _RIGHT_TO_LEFT_OVERRIDE, _BELL],
    ids=["zero_width_space", "bidi_override", "control"],
)
def test_free_text_with_an_invisible_character_is_refused(
    app_session: Session, field: str, character: str
) -> None:
    user, ctx = _submitter(app_session)
    before = _audit_event_count(app_session)
    text = "text" + character + "more"
    content = NewTestSubmissionInput(
        reference_url=_REFERENCE_URL,
        preferred_term="Serum sodium",
        notes=text if field == "notes" else None,
        organisation=text if field == "organisation" else None,
    )

    with pytest.raises(FreeTextRefusedError) as excinfo:
        _create(app_session, ctx, content)

    assert excinfo.value.field == field
    assert f"U+{ord(character):04X}" in str(excinfo.value)
    assert character not in str(excinfo.value)
    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_a_note_may_span_lines_and_use_tabs(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    note = "First line\nSecond line\r\n\tIndented third"

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL, preferred_term="Serum sodium", notes=note
        ),
    )

    assert submission.notes == note


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_the_organisation_is_one_line(app_session: Session) -> None:
    _, ctx = _submitter(app_session)

    with pytest.raises(FreeTextRefusedError) as excinfo:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Serum sodium",
                organisation="Example\nPathology",
            ),
        )

    assert excinfo.value.field == "organisation"


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_non_breaking_space_in_free_text_is_normalised_not_refused(app_session: Session) -> None:
    _, ctx = _submitter(app_session)

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(
            reference_url=_REFERENCE_URL,
            preferred_term="Serum sodium",
            notes="Seen" + NBSP + "here",
            organisation="Example" + NBSP + "Pathology",
        ),
    )

    assert submission.notes == "Seen here"
    assert submission.organisation == "Example Pathology"


# --- the append lock is never held across the terminology server -------------------------------

_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009"
_SERUM = "119364003"


class _CallRecordingClient(StubTerminologyClient):
    """Records, for every terminology call, whether the append lock was already held."""

    def __init__(self, lock_held: Callable[[], bool], calls: list[tuple[str, bool]]) -> None:
        super().__init__()
        self._lock_held = lock_held
        self._calls = calls

    def lookup(self, *args: Any, **kwargs: Any) -> Any:
        self._calls.append(("lookup", self._lock_held()))
        return super().lookup(*args, **kwargs)

    def validate_code(self, *args: Any, **kwargs: Any) -> Any:
        self._calls.append(("validate_code", self._lock_held()))
        return super().validate_code(*args, **kwargs)


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_no_terminology_call_is_made_while_the_append_lock_is_held(
    app_session: Session, capture_statements: Any
) -> None:
    """The lock is one global advisory lock, so a call to the terminology server made while
    holding it would stall every audit write. The lock-ordering guard exempts this writer because
    it must not take the lock first, so this test is what pins the order."""
    seed_system_properties(app_session)
    app_session.flush()
    _, ctx = _submitter(app_session)
    calls: list[tuple[str, bool]] = []

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with capture_statements(app_session.get_bind(), keep=is_lock) as locks:
        client = _CallRecordingClient(lambda: bool(locks), calls)
        client.add_concept(
            StubConcept(
                code=_CODE,
                fsn=_FSN,
                preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            )
        )
        client.seed_validate_code(
            _SERUM,
            ValidationResult(code=_SERUM, result=True),
            value_set_url=_SPECIMEN_VALUE_SET_URI,
            edition=Edition(module_id="au", label="au"),
        )
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(
                reference_url=_REFERENCE_URL,
                preferred_term="Acid fast bacilli microscopy",
                snomed_code=_CODE,
                property_values={
                    "specimen": [
                        PropertyValueInput(value={"system": SNOMED_SYSTEM, "code": _SERUM})
                    ]
                },
            ),
            client,
        )

    assert {name for name, _ in calls} == {"lookup", "validate_code"}
    assert [held for _, held in calls] == [False, False]
    # The probe does see the lock the write takes, so the assertions above cannot pass vacuously.
    assert locks


# --- the supporting reference link (FR-27) -----------------------------------------------------


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_the_reference_is_stored_with_what_the_check_saw(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    checker = StubReferenceChecker()
    checker.status = 403

    submission = _create(
        app_session,
        ctx,
        NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
        reference_checker=checker,
    )

    assert checker.urls == [_REFERENCE_URL]
    assert submission.reference_url == _REFERENCE_URL
    assert submission.reference_status == 403
    assert submission.reference_checked_at is not None


@pytest.mark.req("FR-27")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_audit_event_records_the_reference_and_its_check(app_session: Session) -> None:
    _, ctx = _submitter(app_session)

    _create(
        app_session,
        ctx,
        NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
    )

    event = app_session.execute(
        select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(1)
    ).scalar_one()
    assert event.after is not None
    assert event.after["reference_url"] == _REFERENCE_URL
    assert event.after["reference_status"] == 200
    assert event.after["reference_checked_at"]


@pytest.mark.req("FR-27")
@pytest.mark.integration
@pytest.mark.parametrize(
    "error",
    [
        ReferenceCheckFailedError(ReferenceFailure.BAD_STATUS, status=404),
        ReferenceCheckFailedError(ReferenceFailure.INTERNAL_ADDRESS),
        ReferenceCheckUnavailableError(),
    ],
    ids=["bad_status", "internal_address", "no_outbound_access"],
)
def test_a_reference_that_fails_its_check_saves_nothing(
    app_session: Session, error: Exception
) -> None:
    user, ctx = _submitter(app_session)
    checker = StubReferenceChecker()
    checker.error = error
    before = _audit_event_count(app_session)

    with pytest.raises(type(error)):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
            reference_checker=checker,
        )

    assert _submission_count(app_session, user.id) == 0
    assert _audit_event_count(app_session) == before


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_a_cheaper_refusal_means_no_link_is_fetched(app_session: Session) -> None:
    _, ctx = _submitter(app_session)
    checker = StubReferenceChecker()

    with pytest.raises(TermCleaningError):
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="  "),
            reference_checker=checker,
        )

    assert checker.urls == []


@pytest.mark.req("FR-27")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_reference_is_fetched_before_the_append_lock_is_taken(
    app_session: Session, capture_statements: Any
) -> None:
    """A fetch can run for seconds, and the lock is one global advisory lock, so a fetch made
    while holding it would stall every audit write."""
    _, ctx = _submitter(app_session)
    calls: list[bool] = []

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    class _Recording(StubReferenceChecker):
        def check(self, url: str) -> ReferenceCheckResult:
            calls.append(bool(locks))
            return super().check(url)

    with capture_statements(app_session.get_bind(), keep=is_lock) as locks:
        _create(
            app_session,
            ctx,
            NewTestSubmissionInput(reference_url=_REFERENCE_URL, preferred_term="Serum sodium"),
            reference_checker=_Recording(),
        )

    assert calls == [False]
    assert locks
