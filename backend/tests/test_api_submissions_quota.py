"""The submission quota on `POST /api/v1/submissions` and `POST /api/v1/submissions/amendments`
(FR-43, NFR-24, NFR-08, FR-44).

The real app over a stub identity provider, with the terminology client and the reference checker
replaced by stubs (`api_app_support`). Counts are taken for the submitter each test created, and
audit events are read for that user's id, because `backend/tests` shares one Postgres container (see
`CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import random
import re
import sys
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.auth.permissions import Role
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc_shared.terminology import AU_LANGUAGE_TAG, StubConcept


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
add_submissions = _load("quota_support").add_submissions

_NEW_TEST_PATH = "/submissions"
_AMENDMENT_PATH = "/submissions/amendments"
_CHECK_PATH = "/submissions/duplicate-check"
_REFERENCE_URL = "https://example.org/evidence"
_CODE = "391483001"
_REFUSED_ACTION = "submission.quota_refused"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _token(api: ApiTestApp, role: Role) -> tuple[str, User]:
    subject = f"sub-quota-{uuid.uuid4().hex[:10]}"
    token = api.token_for_role(
        subject=subject,
        role=role,
        with_mfa=role is Role.ADMINISTRATOR,
        replace_roles=True,
    )
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    return token, user


def _entry(api: ApiTestApp) -> CatalogueEntry:
    entry = CatalogueEntry(
        business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
        preferred_term=f"Quota target {uuid.uuid4()}",
        status="active",
    )
    api.session.add(entry)
    api.session.flush()
    return entry


def _new_test(api: ApiTestApp, token: str, **body: object) -> Any:
    payload: dict[str, object] = {
        "preferred_term": f"Quota test {uuid.uuid4()}",
        "reference_url": _REFERENCE_URL,
    }
    payload.update(body)
    return api.post(_NEW_TEST_PATH, token=token, json=payload)


def _amendment(api: ApiTestApp, token: str, **body: object) -> Any:
    payload: dict[str, object] = {
        "entry_business_key": _entry(api).business_key,
        "synonyms": [f"Quota synonym {uuid.uuid4()}"],
    }
    payload.update(body)
    return api.post(_AMENDMENT_PATH, token=token, json=payload)


def _submission_count(api: ApiTestApp, user: User) -> int:
    return api.session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()


def _refusal_events(api: ApiTestApp, user: User) -> list[AuditEvent]:
    return list(
        api.session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == _REFUSED_ACTION, AuditEvent.entity_id == str(user.id))
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _seed_concept(api: ApiTestApp) -> None:
    api.terminology.add_concept(
        StubConcept(
            code=_CODE,
            fsn="Microscopy (acid fast bacilli) (procedure)",
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            active=True,
        )
    )


# --- a refusal ---------------------------------------------------------------------------------


@pytest.mark.req("FR-43")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_provisional_users_sixth_new_test_is_429_with_no_retry_time_and_is_audited(
    api: ApiTestApp,
) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    add_submissions(api.session, user.id, 5)

    response = _new_test(api, token)

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["limit"] == "lifetime"
    assert body["maximum"] == 5
    assert "5 submissions" in body["detail"]
    assert "Retry-After" not in response.headers
    assert _submission_count(api, user) == 5
    (event,) = _refusal_events(api, user)
    assert event.entity_type == "app_user"
    assert event.actor_user_id == user.id
    assert event.after == {
        "limit": "lifetime",
        "maximum": 5,
        "count": 5,
        "submission_kind": "new_test",
    }


@pytest.mark.req("FR-43")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_members_21st_amendment_in_an_hour_is_429_with_retry_after_and_is_audited(
    api: ApiTestApp,
) -> None:
    token, user = _token(api, Role.MEMBER)
    add_submissions(api.session, user.id, 20, age=timedelta(minutes=30))

    response = _amendment(api, token)

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["limit"] == "hourly"
    assert body["maximum"] == 20
    assert response.headers["Retry-After"] == str(30 * 60)
    assert "30 minutes" in body["detail"]
    assert _submission_count(api, user) == 20
    (event,) = _refusal_events(api, user)
    assert event.after is not None
    assert event.after["submission_kind"] == "amendment"


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_refusal_makes_no_terminology_or_reference_call(api: ApiTestApp) -> None:
    """The check comes before any network call, so an over-limit caller costs the server none."""
    _seed_concept(api)
    token, user = _token(api, Role.PROVISIONAL)
    add_submissions(api.session, user.id, 5)

    new_test = _new_test(api, token, snomed_code=_CODE)
    amendment = _amendment(api, token, snomed_code=_CODE, reference_url=_REFERENCE_URL)

    assert new_test.status_code == 429, new_test.text
    assert amendment.status_code == 429, amendment.text
    assert api.reference_checker.urls == []
    assert api.terminology.requests == ()


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_the_refusal_body_names_no_role_permission_or_identifier(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    add_submissions(api.session, user.id, 5)

    response = _new_test(api, token)

    assert response.status_code == 429, response.text
    assert not _UUID.search(response.text)
    for role in Role:
        assert role.value not in response.text.lower()
    assert "permission" not in response.text.lower()


# --- the count ---------------------------------------------------------------------------------


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_provisional_user_makes_five_submissions_and_the_sixth_is_refused(
    api: ApiTestApp,
) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    statuses = [_new_test(api, token).status_code for _ in range(6)]

    assert statuses == [201, 201, 201, 201, 201, 429]
    assert _submission_count(api, user) == 5
    assert len(_refusal_events(api, user)) == 1


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_new_test_and_an_amendment_share_one_counter(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    add_submissions(api.session, user.id, 3, kind=SubmissionKind.NEW_TEST)
    add_submissions(api.session, user.id, 2, kind=SubmissionKind.AMENDMENT)

    assert _new_test(api, token).status_code == 429
    assert _amendment(api, token).status_code == 429


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_an_amendment_counts_towards_the_limit_of_a_later_new_test(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    add_submissions(api.session, user.id, 4, kind=SubmissionKind.NEW_TEST)

    assert _amendment(api, token).status_code == 201
    assert _new_test(api, token).status_code == 429


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_member_is_served_again_once_the_hour_has_passed(api: ApiTestApp) -> None:
    token, user = _token(api, Role.MEMBER)
    add_submissions(api.session, user.id, 20, age=timedelta(hours=1, minutes=1))

    response = _new_test(api, token)

    assert response.status_code == 201, response.text
    assert _refusal_events(api, user) == []


@pytest.mark.req("FR-43")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.REVIEWER, Role.ADMINISTRATOR], ids=lambda r: r.value)
def test_reviewer_and_administrator_are_never_limited(api: ApiTestApp, role: Role) -> None:
    token, user = _token(api, role)
    add_submissions(api.session, user.id, 30, age=timedelta(minutes=1))

    assert _new_test(api, token).status_code == 201
    assert _amendment(api, token).status_code == 201
    assert _refusal_events(api, user) == []


# --- what the quota is not ---------------------------------------------------------------------


@pytest.mark.req("FR-43")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_observer_is_refused_with_403_before_any_quota_logic(api: ApiTestApp) -> None:
    token, user = _token(api, Role.OBSERVER)

    new_test = _new_test(api, token)
    amendment = _amendment(api, token)

    assert new_test.status_code == 403, new_test.text
    assert amendment.status_code == 403, amendment.text
    assert _refusal_events(api, user) == []


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_the_duplicate_check_is_not_counted_and_is_not_refused(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)
    add_submissions(api.session, user.id, 5)

    response = api.post(_CHECK_PATH, token=token, json={"preferred_term": f"Fresh {uuid.uuid4()}"})

    assert response.status_code == 200, response.text
    assert _refusal_events(api, user) == []


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_request_that_fails_validation_is_not_counted(api: ApiTestApp) -> None:
    token, user = _token(api, Role.PROVISIONAL)

    invalid = [_new_test(api, token, preferred_term=None).status_code for _ in range(6)]
    valid = _new_test(api, token)

    assert set(invalid) == {422}
    assert valid.status_code == 201, valid.text
    assert _submission_count(api, user) == 1
