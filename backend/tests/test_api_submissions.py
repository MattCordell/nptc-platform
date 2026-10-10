"""`POST /api/v1/submissions` (FR-23, FR-24, FR-26, FR-27, FR-80, NFR-08, NFR-45).

The real app over a stub identity provider, with the terminology client replaced by a stub
(`api_app_support`). Each refusal asserts that nothing was stored, counted for the submitter this
test created, because `backend/tests` shares one Postgres container (see `CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.api.errors import TERMS_ACCEPTANCE_REQUIRED_CODE
from nptc.audit.diffing import REDACTED_KEY
from nptc.auth.permissions import Role
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.audit import AuditEvent
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.db.models.submission import Submission
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc.submissions.reference_check import (
    ReferenceCheckFailedError,
    ReferenceCheckUnavailableError,
    ReferenceFailure,
)
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    SNOMED_SYSTEM,
    ConceptProperty,
    Designation,
    LookupResult,
    Operation,
    StubConcept,
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


_api_support = _load("api_app_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp

latest_audit_event = _load("audit_support").latest_audit_event

_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_FSN_USE_CODE = "900000000000003001"
_PROFILE_ORGANISATION = "Profile Pathology"
_PATH = "/submissions"
_REFERENCE_URL = "https://example.org/evidence"
_SNOMED = "http://snomed.info/sct"
_SPECIMEN_ROOT = "123038009"
_SERUM = "119364003"
_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C%3C123038009"
_SPECIMEN_EDITION = Edition(module_id="au", label="au")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _subject() -> str:
    return f"sub-submit-{uuid.uuid4().hex[:10]}"


def _token(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to, whose profile names an
    organisation. An administrator gets the MFA claim the realm demands for that role."""
    subject = _subject()
    token = api.token_for_role(
        subject=subject,
        role=role,
        with_mfa=role is Role.ADMINISTRATOR,
        replace_roles=True,
        accept_terms=accept_terms,
    )
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    user.organisation = _PROFILE_ORGANISATION
    api.session.flush()
    return token, user


def _submission_count(api: ApiTestApp, user: User) -> int:
    return api.session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()


def _audit_event_count(api: ApiTestApp) -> int:
    return api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _post(api: ApiTestApp, token: str | None, **body: object) -> Any:
    payload: dict[str, object] = {
        "preferred_term": "Serum sodium",
        "reference_url": _REFERENCE_URL,
    }
    payload.update(body)
    return api.post(_PATH, token=token, json=payload)


def _seed_concept(api: ApiTestApp, *, active: bool = True) -> None:
    api.terminology.add_concept(
        StubConcept(
            code=_CODE,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            active=active,
        )
    )


def _property(
    api: ApiTestApp,
    key: str,
    *,
    scope: PropertyScope = PropertyScope.SUBMISSION,
    required: bool = False,
    max_length: int | None = None,
    cardinality: str = "0..1",
) -> None:
    api.session.add(
        PropertyDefinition(
            key=key,
            label=key.replace("_", " ").title(),
            datatype="string",
            cardinality=cardinality,
            scope=scope,
            required_for_submission=required,
            required_for_publication=False,
            filterable=False,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={"maxLength": max_length} if max_length is not None else {},
        )
    )
    api.session.flush()


# --- who may submit (FR-80) --------------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-80")
@pytest.mark.integration
def test_a_provisional_user_creates_a_submission_in_state_submitted(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, synonyms=["Sodium, serum"], notes="Seen on a new panel")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "new_test"
    assert body["state"] == "Submitted"
    assert body["preferred_term"] == "Serum sodium"
    assert body["synonyms"] == ["Sodium, serum"]
    assert body["notes"] == "Seen on a new panel"
    assert body["organisation"] == _PROFILE_ORGANISATION
    assert body["row_version"] == 1
    stored = api.session.execute(
        select(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert str(stored.id) == body["id"]
    assert stored.state == "Submitted"
    assert stored.organisation == _PROFILE_ORGANISATION


@pytest.mark.req("FR-80")
@pytest.mark.integration
@pytest.mark.parametrize(
    "role",
    [Role.PROVISIONAL, Role.MEMBER, Role.REVIEWER, Role.ADMINISTRATOR],
    ids=lambda r: r.value,
)
def test_every_role_that_holds_submission_create_can_submit(api: ApiTestApp, role: Role) -> None:
    token, user = _token(api, role)

    response = _post(api, token)

    assert response.status_code == 201, response.text
    assert _submission_count(api, user) == 1


@pytest.mark.req("FR-80")
@pytest.mark.integration
def test_an_observer_is_refused_with_403_and_nothing_is_stored(api: ApiTestApp) -> None:
    token, user = _token(api, Role.OBSERVER)
    before = _audit_event_count(api)

    response = _post(api, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before
    assert api.reference_checker.urls == []
    for role in Role:
        assert role.value not in response.text
    assert not _UUID.search(response.text)


@pytest.mark.req("FR-80")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    response = _post(api, None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert api.reference_checker.urls == []


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_user_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL, accept_terms=False)
    before = _audit_event_count(api)

    refused = _post(api, token)

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before
    assert api.reference_checker.urls == []

    api.accept_current_terms(user.id)
    accepted = _post(api, token)

    assert accepted.status_code == 201, accepted.text


# --- the request body (FR-23, FR-24) -----------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_request_without_a_preferred_term_is_422(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    response = api.post(_PATH, token=token, json={"notes": "No name given"})

    assert response.status_code == 422, response.text
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize("term", ["", "   "], ids=["empty", "blank"])
def test_a_blank_preferred_term_is_422(api: ApiTestApp, term: str) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, preferred_term=term)

    assert response.status_code == 422, response.text
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
@pytest.mark.parametrize("field", ["length", "snomed_fsn", "state", "submitter_id", "kind"])
def test_a_field_the_caller_may_not_supply_is_422(api: ApiTestApp, field: str) -> None:
    """`length` is computed (FR-24), the FSN is served (FR-82), and state, kind and submitter are
    set by the platform."""
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, **{field: 12})

    assert response.status_code == 422, response.text
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("requested", "expected"),
    [("Other Pathology", "Other Pathology"), ("  ", None)],
    ids=["override", "blank_means_none"],
)
def test_the_request_can_override_the_organisation_copy(
    api: ApiTestApp, requested: str, expected: str | None
) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, organisation=requested)

    assert response.status_code == 201, response.text
    assert response.json()["organisation"] == expected
    stored = api.session.execute(
        select(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert stored.organisation == expected


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_the_organisation_copy_outlives_the_profile_value(api: ApiTestApp) -> None:
    """Closing an account clears the profile (NFR-17), and the submission keeps its own copy."""
    token, user = _token(api, Role.PROVISIONAL)
    created = _post(api, token)
    assert created.status_code == 201, created.text

    user.organisation = None
    api.session.flush()

    stored = api.session.execute(
        select(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert stored.organisation == _PROFILE_ORGANISATION


# --- property values (FR-24) -------------------------------------------------------------------


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_submission_scoped_values_are_validated_and_stored(api: ApiTestApp) -> None:
    _property(api, "api_sub_analyte")
    token, _ = _token(api, Role.PROVISIONAL)

    response = _post(
        api,
        token,
        property_values={"api_sub_analyte": [{"value": "Sodium", "justification": "Per panel"}]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["property_values"] == {
        "api_sub_analyte": [{"value": "Sodium", "justification": "Per panel"}]
    }


def _issues(response: Any) -> list[tuple[str, str]]:
    return [(issue["property_key"], issue["code"]) for issue in response.json()["issues"]]


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_property_outside_submission_scope_is_422(api: ApiTestApp) -> None:
    _property(api, "api_sub_maintenance", scope=PropertyScope.MAINTENANCE)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, property_values={"api_sub_maintenance": [{"value": "x"}]})

    assert response.status_code == 422, response.text
    assert _issues(response) == [("api_sub_maintenance", "out-of-scope")]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_computed_field_given_as_a_property_is_422(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, property_values={"length": [{"value": 12}]})

    assert response.status_code == 422, response.text
    assert _issues(response) == [("length", "unknown-property")]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_value_the_registry_handler_refuses_is_422(api: ApiTestApp) -> None:
    _property(api, "api_sub_short", max_length=3)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, property_values={"api_sub_short": [{"value": "too long"}]})

    assert response.status_code == 422, response.text
    assert [key for key, _ in _issues(response)] == ["api_sub_short"]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_missing_required_property_is_422(api: ApiTestApp) -> None:
    _property(api, "api_sub_required", required=True)
    token, user = _token(api, Role.PROVISIONAL)

    refused = _post(api, token)

    assert refused.status_code == 422, refused.text
    assert ("api_sub_required", "required-property-missing") in _issues(refused)
    assert _submission_count(api, user) == 0

    accepted = _post(api, token, property_values={"api_sub_required": [{"value": "given"}]})

    assert accepted.status_code == 201, accepted.text


# --- the SNOMED CT code (FR-26) ----------------------------------------------------------------


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_the_stored_fsn_comes_from_the_terminology_server(api: ApiTestApp) -> None:
    _seed_concept(api)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["snomed_code"] == _CODE
    assert isinstance(body["snomed_code"], str)
    assert body["snomed_fsn"] == _FSN
    stored = api.session.execute(
        select(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert (stored.snomed_code, stored.snomed_fsn) == (_CODE, _FSN)
    assert [(r.operation, r.detail) for r in api.terminology.requests] == [
        (Operation.LOOKUP, _CODE)
    ]


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_a_submission_without_a_code_asks_the_terminology_server_nothing(
    api: ApiTestApp,
) -> None:
    token, _ = _token(api, Role.PROVISIONAL)

    response = _post(api, token)

    assert response.status_code == 201, response.text
    assert response.json()["snomed_code"] is None
    assert response.json()["snomed_fsn"] is None
    assert api.terminology.requests == ()


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_an_unknown_code_is_refused_with_a_reason(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP, TerminologyStatusError("not found", status_code=404)
    )
    token, user = _token(api, Role.PROVISIONAL)
    before = _audit_event_count(api)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 422, response.text
    assert "not found" in response.json()["detail"]
    assert _CODE not in response.text
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before


@pytest.mark.req("FR-26")
@pytest.mark.integration
def test_an_inactive_code_is_refused_with_a_reason(api: ApiTestApp) -> None:
    _seed_concept(api, active=False)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 422, response.text
    assert "inactive" in response.json()["detail"]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_a_code_whose_active_status_is_not_reported_is_refused(api: ApiTestApp) -> None:
    api.terminology.seed_lookup(
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
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 422, response.text
    assert "did not say whether" in response.json()["detail"]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_a_code_with_no_served_fsn_is_refused(api: ApiTestApp) -> None:
    api.terminology.seed_lookup(
        _CODE,
        LookupResult(
            code=_CODE,
            system=SNOMED_SYSTEM,
            display="Acid fast bacilli microscopy",
            properties=(ConceptProperty(code="inactive", value="false", value_type="boolean"),),
        ),
    )
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 422, response.text
    assert "fully specified name" in response.json()["detail"]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-06")
@pytest.mark.integration
@pytest.mark.parametrize(
    "code",
    ["not-a-code", "391483009", "", " 391483001"],
    ids=["malformed", "bad_check_digit", "empty", "padded"],
)
def test_a_malformed_code_is_422_without_asking_the_server(api: ApiTestApp, code: str) -> None:
    _seed_concept(api)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=code)

    assert response.status_code == 422, response.text
    assert api.terminology.requests == ()
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_a_terminology_outage_is_503_and_nothing_is_stored(api: ApiTestApp) -> None:
    api.terminology.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"))
    token, user = _token(api, Role.PROVISIONAL)
    before = _audit_event_count(api)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 503, response.text
    assert "terminology server" in response.json()["detail"]
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before


# --- the supporting reference link (FR-27) -----------------------------------------------------


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_a_new_test_without_a_reference_is_422_and_nothing_is_checked_or_stored(
    api: ApiTestApp,
) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    before = _audit_event_count(api)

    response = api.post(_PATH, token=token, json={"preferred_term": "Serum sodium"})

    assert response.status_code == 422, response.text
    assert api.reference_checker.urls == []
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_the_reference_is_checked_stored_and_returned(api: ApiTestApp) -> None:
    api.reference_checker.status = 403
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token)

    assert response.status_code == 201, response.text
    body = response.json()
    assert api.reference_checker.urls == [_REFERENCE_URL]
    assert body["reference_url"] == _REFERENCE_URL
    assert body["reference_status"] == 403
    assert body["reference_checked_at"]
    stored = api.session.execute(
        select(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert stored.reference_url == _REFERENCE_URL
    assert stored.reference_status == 403
    assert stored.reference_checked_at is not None


@pytest.mark.req("FR-27")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_audit_event_records_the_reference_and_its_check(api: ApiTestApp) -> None:
    token, _ = _token(api, Role.PROVISIONAL)

    response = _post(api, token)

    assert response.status_code == 201, response.text
    event = latest_audit_event(
        api.session, entity_type="submission", entity_id=response.json()["id"]
    )
    assert event.after is not None
    assert event.after["reference_url"] == _REFERENCE_URL
    assert event.after["reference_status"] == 200
    assert event.after["reference_checked_at"]


@pytest.mark.req("FR-27")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("failure", "status", "expected"),
    [
        (ReferenceFailure.BAD_STATUS, 404, "404"),
        (ReferenceFailure.INTERNAL_ADDRESS, None, "does not contact"),
        (ReferenceFailure.NAME_NOT_FOUND, None, "could not be found"),
        (ReferenceFailure.TIMEOUT, None, "too long"),
    ],
    ids=["bad_status", "internal_address", "name_not_found", "timeout"],
)
def test_a_link_that_fails_its_check_is_422_with_a_reason_and_nothing_is_stored(
    api: ApiTestApp, failure: ReferenceFailure, status: int | None, expected: str
) -> None:
    api.reference_checker.error = ReferenceCheckFailedError(failure, status=status)
    token, user = _token(api, Role.PROVISIONAL)
    before = _audit_event_count(api)

    response = _post(api, token)

    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]
    assert _REFERENCE_URL not in response.text
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before


@pytest.mark.req("FR-27")
@pytest.mark.integration
def test_no_outbound_access_is_a_503_that_differs_from_a_broken_link(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    api.reference_checker.error = ReferenceCheckFailedError(ReferenceFailure.NAME_NOT_FOUND)
    broken = _post(api, token)
    api.reference_checker.error = ReferenceCheckUnavailableError()
    before = _audit_event_count(api)

    unavailable = _post(api, token)

    assert broken.status_code == 422, broken.text
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["detail"] != broken.json()["detail"]
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before


# --- the response and the audit event ----------------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_audit_event_records_the_content_and_withholds_the_organisation(
    api: ApiTestApp,
) -> None:
    _seed_concept(api)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=_CODE, notes="A supporting note")

    assert response.status_code == 201, response.text
    event = latest_audit_event(
        api.session, entity_type="submission", entity_id=response.json()["id"]
    )
    assert event.action == "submission.created"
    assert event.actor_user_id == user.id
    assert event.before is None
    assert event.after is not None
    assert event.after["preferred_term"] == "Serum sodium"
    assert event.after["snomed_code"] == _CODE
    assert event.after["snomed_fsn"] == _FSN
    assert event.after["notes"] == "A supporting note"
    assert event.after["state"] == "Submitted"
    assert event.after[REDACTED_KEY] == ["organisation"]
    assert _PROFILE_ORGANISATION not in str(event.after)


@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_the_response_declares_which_designation_each_label_is(api: ApiTestApp) -> None:
    _seed_concept(api)
    token, _ = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=_CODE)

    assert response.status_code == 201, response.text
    assert response.json()["label_provenance"] == {
        "preferred_term": {"designation": "au_preferred_term", "semantic_tag": "not_applicable"},
        "synonyms": {"designation": "synonym", "semantic_tag": "not_applicable"},
        "snomed_fsn": {"designation": "fsn", "semantic_tag": "intact"},
    }


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_the_response_names_no_user(api: ApiTestApp) -> None:
    """Who submitted is the read routes' rule (FR-42); this route returns no user id (NFR-04)."""
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token)

    assert response.status_code == 201, response.text
    assert str(user.id) not in response.text
    assert "submitter" not in response.json()


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_code_sent_as_a_number_is_422_never_coerced_to_text(api: ApiTestApp) -> None:
    """The defect class the platform exists to remove: an SCTID that passed through a number."""
    _seed_concept(api)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, snomed_code=int(_CODE))

    assert response.status_code == 422, response.text
    assert api.terminology.requests == ()
    assert _submission_count(api, user) == 0


# --- coded properties go through the same registry handlers (FR-77, FR-89) ---------------------


def _seed_specimen_validation(api: ApiTestApp, *codes: str) -> None:
    seed_system_properties(api.session)
    api.session.flush()
    for code in codes:
        api.terminology.seed_validate_code(
            code,
            ValidationResult(code=code, result=True),
            value_set_url=_SPECIMEN_VALUE_SET_URI,
            edition=_SPECIMEN_EDITION,
        )


@pytest.mark.req("FR-24")
@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_a_coded_property_is_checked_against_its_value_set_by_the_registry(
    api: ApiTestApp,
) -> None:
    _seed_specimen_validation(api, _SERUM)
    token, _ = _token(api, Role.PROVISIONAL)

    response = _post(
        api,
        token,
        property_values={"specimen": [{"value": {"system": _SNOMED, "code": _SERUM}}]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["property_values"] == {
        "specimen": [{"value": {"system": _SNOMED, "code": _SERUM}, "justification": None}]
    }


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_the_specimen_root_beside_a_named_specimen_is_422(api: ApiTestApp) -> None:
    _seed_specimen_validation(api, _SPECIMEN_ROOT, _SERUM)
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(
        api,
        token,
        property_values={
            "specimen": [
                {"value": {"system": _SNOMED, "code": _SERUM}},
                {"value": {"system": _SNOMED, "code": _SPECIMEN_ROOT}},
            ]
        },
    )

    assert response.status_code == 422, response.text
    assert _issues(response) == [("specimen", "specimen-root-conflict")]
    assert _submission_count(api, user) == 0


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_maintenance_only_system_property_is_422(api: ApiTestApp) -> None:
    """`usage_guidance` is the catalogue's own maintenance-scope property: a submitter cannot set
    what only a maintainer may."""
    seed_system_properties(api.session)
    api.session.flush()
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, property_values={"usage_guidance": [{"value": "Fasting"}]})

    assert response.status_code == 422, response.text
    assert _issues(response) == [("usage_guidance", "out-of-scope")]
    assert _submission_count(api, user) == 0


# --- the size of a request is bounded ----------------------------------------------------------

_ZERO_WIDTH_SPACE = chr(0x200B)

#: One case per bound, each one over by one. A property key costs a registry query and a coded
#: value can cost a terminology call, so none of these may reach the registry or the server.
_OVER_THE_BOUND: dict[str, dict[str, object]] = {
    "preferred_term": {"preferred_term": "x" * 501},
    "synonym_count": {"synonyms": [f"Synonym {n}" for n in range(101)]},
    "synonym_length": {"synonyms": ["x" * 501]},
    "notes": {"notes": "x" * 10_001},
    "organisation": {"organisation": "x" * 501},
    "snomed_code": {"snomed_code": "1" * 19},
    "reference_url": {"reference_url": "https://example.org/" + "x" * 2_030},
    "property_count": {"property_values": {f"k{n}": [{"value": "x"}] for n in range(26)}},
    "property_key_length": {"property_values": {"k" * 101: [{"value": "x"}]}},
    "values_per_property": {"property_values": {"api_bound": [{"value": "x"}] * 26}},
    "values_in_total": {
        "property_values": {f"k{n}": [{"value": "x"}] * 20 for n in range(3)},
    },
    "justification": {
        "property_values": {"api_bound": [{"value": "x", "justification": "x" * 2_001}]}
    },
    "value_size": {"property_values": {"api_bound": [{"value": "x" * 10_001}]}},
}


@pytest.mark.integration
@pytest.mark.parametrize("body", _OVER_THE_BOUND.values(), ids=_OVER_THE_BOUND.keys())
def test_a_request_over_a_size_bound_is_422_and_does_no_work(
    api: ApiTestApp, body: dict[str, object]
) -> None:
    _property(api, "api_bound", cardinality="0..*")
    token, user = _token(api, Role.PROVISIONAL)
    before = _audit_event_count(api)

    response = _post(api, token, **body)

    assert response.status_code == 422, response.text
    assert api.terminology.requests == ()
    assert api.reference_checker.urls == []
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == before


@pytest.mark.integration
def test_a_request_with_too_many_properties_runs_no_registry_query(
    api: ApiTestApp, app_db: Connection, capture_statements: Any
) -> None:
    token, _ = _token(api, Role.PROVISIONAL)

    def reads_the_registry(statement: str, _parameters: object) -> bool:
        return "property_definition" in statement

    with capture_statements(app_db, keep=reads_the_registry) as statements:
        response = _post(api, token, property_values={f"k{n}": [{"value": "x"}] for n in range(26)})

    assert response.status_code == 422, response.text
    assert statements == []


@pytest.mark.integration
def test_a_request_at_every_bound_is_accepted(api: ApiTestApp) -> None:
    """The bounds are far above a real submission, and each one is inclusive."""
    for n in range(25):
        _property(api, f"api_key_{n}")
    _property(api, "api_many_a", cardinality="0..*")
    _property(api, "api_many_b", cardinality="0..*")
    token, user = _token(api, Role.PROVISIONAL)

    at_the_string_bounds = _post(
        api,
        token,
        preferred_term="x" * 500,
        reference_url="https://example.org/" + "x" * 2_028,
        synonyms=[f"Synonym {n}" for n in range(100)],
        notes="x" * 10_000,
        organisation="x" * 500,
        property_values={
            "api_key_0": [{"value": "x" * 9_990, "justification": "x" * 2_000}],
        },
    )
    at_the_property_bound = _post(
        api, token, property_values={f"api_key_{n}": [{"value": "x"}] for n in range(25)}
    )
    at_the_value_bounds = _post(
        api,
        token,
        property_values={
            "api_many_a": [{"value": f"a{n}"} for n in range(25)],
            "api_many_b": [{"value": f"b{n}"} for n in range(25)],
        },
    )

    assert at_the_string_bounds.status_code == 201, at_the_string_bounds.text
    assert at_the_property_bound.status_code == 201, at_the_property_bound.text
    assert at_the_value_bounds.status_code == 201, at_the_value_bounds.text
    assert _submission_count(api, user) == 3


# --- synonyms and free text --------------------------------------------------------------------


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_a_repeated_synonym_is_stored_once_and_the_response_shows_what_was_kept(
    api: ApiTestApp,
) -> None:
    token, _ = _token(api, Role.PROVISIONAL)

    response = _post(
        api, token, synonyms=["Sodium, serum", "sodium, serum", "serum sodium", "Na (serum)"]
    )

    assert response.status_code == 201, response.text
    assert response.json()["synonyms"] == ["Sodium, serum", "Na (serum)"]


@pytest.mark.req("FR-63")
@pytest.mark.integration
@pytest.mark.parametrize("field", ["notes", "organisation"])
def test_free_text_with_an_invisible_character_is_422_with_a_fixed_reason(
    api: ApiTestApp, field: str
) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    response = _post(api, token, **{field: "text" + _ZERO_WIDTH_SPACE + "more"})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    other = "organisation" if field == "notes" else "notes"
    assert "invisible character" in detail
    assert field in detail
    assert other not in detail
    assert _ZERO_WIDTH_SPACE not in response.text
    assert "200B" not in response.text.upper()
    assert _submission_count(api, user) == 0
