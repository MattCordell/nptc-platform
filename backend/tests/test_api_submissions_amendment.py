"""`POST /api/v1/submissions/amendments` (FR-35, FR-26, FR-27, FR-80, NFR-08, NFR-45).

The real app over a stub identity provider, with the terminology client and the reference checker
replaced by stubs (`api_app_support`). Each refusal asserts that nothing was stored, counted for the
submitter this test created, because `backend/tests` shares one Postgres container (see
`CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import random
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
from nptc.auth.permissions import Role
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
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
    Operation,
    StubConcept,
    TerminologyTimeoutError,
)


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
_PROFILE_ORGANISATION = "Profile Pathology"
_PATH = "/submissions/amendments"
_REFERENCE_URL = "https://example.org/evidence"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _token(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to, whose profile names an
    organisation. An administrator gets the MFA claim the realm demands for that role."""
    subject = f"sub-amend-{uuid.uuid4().hex[:10]}"
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


def _entry(
    api: ApiTestApp,
    *,
    term: str = "Serum sodium",
    status: CatalogueEntryStatus = CatalogueEntryStatus.ACTIVE,
) -> CatalogueEntry:
    entry = CatalogueEntry(
        business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
        preferred_term=term,
        status=status.value,
    )
    api.session.add(entry)
    api.session.flush()
    return entry


def _submission_count(api: ApiTestApp, user: User) -> int:
    return api.session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()


def _audit_event_count(api: ApiTestApp) -> int:
    return api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _post(api: ApiTestApp, token: str | None, entry: CatalogueEntry, **body: object) -> Any:
    payload: dict[str, object] = {"entry_business_key": entry.business_key}
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


def _assert_nothing_saved(api: ApiTestApp, user: User, audit_events_before: int) -> None:
    assert _submission_count(api, user) == 0
    assert _audit_event_count(api) == audit_events_before


# --- who may propose an amendment (FR-80) ------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.req("FR-80")
@pytest.mark.integration
def test_a_member_amends_an_active_entry_with_one_new_synonym(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    entry = _entry(api)

    response = _post(api, token, entry, synonyms=["Na (serum)"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "amendment"
    assert body["state"] == "Submitted"
    assert body["entry_business_key"] == entry.business_key
    assert body["preferred_term"] == entry.preferred_term
    assert body["synonyms"] == ["Na (serum)"]
    assert body["snomed_code"] is None
    assert body["property_values"] == {}
    assert body["reference_url"] is None
    assert body["reference_checked_at"] is None
    assert body["reference_status"] is None
    assert body["organisation"] == _PROFILE_ORGANISATION
    stored = api.session.execute(
        select(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert str(stored.id) == body["id"]
    assert stored.entry_id == entry.id


@pytest.mark.req("FR-80")
@pytest.mark.integration
@pytest.mark.parametrize(
    "role",
    [Role.PROVISIONAL, Role.MEMBER, Role.REVIEWER, Role.ADMINISTRATOR],
    ids=lambda r: r.value,
)
def test_every_role_that_holds_amendment_propose_can_amend(api: ApiTestApp, role: Role) -> None:
    token, user = _token(api, role)

    response = _post(api, token, _entry(api), synonyms=["Na (serum)"])

    assert response.status_code == 201, response.text
    assert _submission_count(api, user) == 1


@pytest.mark.req("FR-80")
@pytest.mark.integration
def test_an_observer_is_refused_with_403_and_nothing_is_stored(api: ApiTestApp) -> None:
    token, user = _token(api, Role.OBSERVER)
    entry = _entry(api)
    before = _audit_event_count(api)

    response = _post(api, token, entry, synonyms=["Na (serum)"], reference_url=_REFERENCE_URL)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    _assert_nothing_saved(api, user, before)
    assert api.reference_checker.urls == []
    for role in Role:
        assert role.value not in response.text
    assert not _UUID.search(response.text)


@pytest.mark.req("FR-80")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    response = _post(api, None, _entry(api), synonyms=["Na (serum)"])

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert api.reference_checker.urls == []


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_user_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER, accept_terms=False)
    entry = _entry(api)
    before = _audit_event_count(api)

    refused = _post(api, token, entry, synonyms=["Na (serum)"])

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_nothing_saved(api, user, before)

    api.accept_current_terms(user.id)
    accepted = _post(api, token, entry, synonyms=["Na (serum)"])

    assert accepted.status_code == 201, accepted.text


# --- the entry --------------------------------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_unknown_business_key_is_404_and_nothing_is_stored(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    before = _audit_event_count(api)

    response = api.post(
        _PATH,
        token=token,
        json={"entry_business_key": "NPTC-000000000", "synonyms": ["Na (serum)"]},
    )

    assert response.status_code == 404, response.text
    _assert_nothing_saved(api, user, before)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_malformed_business_key_fails_validation(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    before = _audit_event_count(api)

    response = api.post(
        _PATH, token=token, json={"entry_business_key": "not-a-key", "synonyms": ["Na (serum)"]}
    )

    assert response.status_code == 422, response.text
    _assert_nothing_saved(api, user, before)


@pytest.mark.req("FR-35")
@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [
        CatalogueEntryStatus.DRAFT,
        CatalogueEntryStatus.DEPRECATED,
        CatalogueEntryStatus.WITHDRAWN,
    ],
    ids=lambda s: s.value,
)
def test_an_entry_that_is_not_active_is_409_and_the_reason_names_the_status(
    api: ApiTestApp, status: CatalogueEntryStatus
) -> None:
    token, user = _token(api, Role.MEMBER)
    entry = _entry(api, status=status)
    before = _audit_event_count(api)

    response = _post(api, token, entry, synonyms=["Na (serum)"], reference_url=_REFERENCE_URL)

    assert response.status_code == 409, response.text
    assert status.value in response.json()["detail"]
    assert entry.business_key not in response.text
    _assert_nothing_saved(api, user, before)
    assert api.reference_checker.urls == []


# --- an amendment must change something -------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_with_no_synonym_and_no_code_is_422(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    entry = _entry(api)
    before = _audit_event_count(api)

    response = _post(api, token, entry, notes="Only a note", reference_url=_REFERENCE_URL)

    assert response.status_code == 422, response.text
    assert "synonym" in response.json()["detail"]
    _assert_nothing_saved(api, user, before)
    assert api.reference_checker.urls == []


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_synonym_equal_to_the_entrys_preferred_term_leaves_nothing_to_propose(
    api: ApiTestApp,
) -> None:
    token, user = _token(api, Role.MEMBER)
    entry = _entry(api)
    before = _audit_event_count(api)

    response = _post(api, token, entry, synonyms=[entry.preferred_term.upper()])

    assert response.status_code == 422, response.text
    _assert_nothing_saved(api, user, before)


# --- the code (FR-26, FR-82) ------------------------------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_a_code_is_resolved_and_stored_with_the_fsn_the_server_returned(api: ApiTestApp) -> None:
    token, _ = _token(api, Role.MEMBER)
    _seed_concept(api)

    response = _post(api, token, _entry(api), snomed_code=_CODE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["snomed_code"] == _CODE
    assert body["snomed_fsn"] == _FSN
    assert body["label_provenance"]["snomed_fsn"]


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_inactive_code_is_422_and_nothing_is_stored(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    _seed_concept(api, active=False)
    before = _audit_event_count(api)

    response = _post(api, token, _entry(api), snomed_code=_CODE)

    assert response.status_code == 422, response.text
    assert "inactive" in response.json()["detail"]
    _assert_nothing_saved(api, user, before)


@pytest.mark.req("FR-26")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_malformed_code_is_422_and_nothing_is_stored(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    before = _audit_event_count(api)

    response = _post(api, token, _entry(api), snomed_code="391483009")

    assert response.status_code == 422, response.text
    _assert_nothing_saved(api, user, before)


@pytest.mark.req("FR-54")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_terminology_outage_is_503_and_nothing_is_stored(api: ApiTestApp) -> None:
    api.terminology.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"))
    token, user = _token(api, Role.MEMBER)
    before = _audit_event_count(api)

    response = _post(api, token, _entry(api), snomed_code=_CODE)

    assert response.status_code == 503, response.text
    assert "terminology server" in response.json()["detail"]
    _assert_nothing_saved(api, user, before)


@pytest.mark.req("FR-82")
@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    ["snomed_fsn", "property_values", "confirm_not_duplicate", "kind", "preferred_term"],
)
def test_a_field_an_amendment_does_not_take_is_422(api: ApiTestApp, field: str) -> None:
    token, user = _token(api, Role.MEMBER)
    before = _audit_event_count(api)

    response = _post(api, token, _entry(api), synonyms=["Na (serum)"], **{field: "x"})

    assert response.status_code == 422, response.text
    _assert_nothing_saved(api, user, before)


# --- the optional reference link (FR-27) -------------------------------------------------------


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_without_a_reference_is_saved_and_nothing_is_fetched(
    api: ApiTestApp,
) -> None:
    token, _ = _token(api, Role.MEMBER)

    response = _post(api, token, _entry(api), synonyms=["Na (serum)"])

    assert response.status_code == 201, response.text
    assert api.reference_checker.urls == []


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_reference_that_is_given_is_checked_and_stored(api: ApiTestApp) -> None:
    token, _ = _token(api, Role.MEMBER)

    response = _post(api, token, _entry(api), synonyms=["Na (serum)"], reference_url=_REFERENCE_URL)

    assert response.status_code == 201, response.text
    body = response.json()
    assert api.reference_checker.urls == [_REFERENCE_URL]
    assert body["reference_url"] == _REFERENCE_URL
    assert body["reference_status"] == 200
    assert body["reference_checked_at"]


@pytest.mark.req("FR-27")
@pytest.mark.req("FR-35")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ReferenceCheckFailedError(ReferenceFailure.BAD_STATUS, status=404), 422),
        (ReferenceCheckFailedError(ReferenceFailure.INTERNAL_ADDRESS), 422),
        (ReferenceCheckUnavailableError(), 503),
    ],
    ids=["bad_status", "internal_address", "no_outbound_access"],
)
def test_a_reference_that_fails_its_check_stores_nothing(
    api: ApiTestApp, error: Exception, status_code: int
) -> None:
    token, user = _token(api, Role.MEMBER)
    api.reference_checker.error = error
    before = _audit_event_count(api)

    response = _post(api, token, _entry(api), synonyms=["Na (serum)"], reference_url=_REFERENCE_URL)

    assert response.status_code == status_code, response.text
    assert _REFERENCE_URL not in response.text
    _assert_nothing_saved(api, user, before)


# --- the audit event (NFR-08) -----------------------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_the_audit_event_records_the_kind_and_the_entry_link(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    entry = _entry(api)

    response = _post(api, token, entry, synonyms=["Na (serum)"])

    assert response.status_code == 201, response.text
    event = latest_audit_event(
        api.session, entity_type="submission", entity_id=response.json()["id"]
    )
    assert event.action == "submission.created"
    assert event.actor_user_id == user.id
    assert event.before is None
    assert event.after is not None
    assert event.after["kind"] == "amendment"
    assert event.after["entry_id"] == str(entry.id)
    assert event.after["synonyms"] == ["Na (serum)"]
