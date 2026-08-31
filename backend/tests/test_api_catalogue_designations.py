"""HTTP tests for `nptc.api.routers.catalogue_designations` (issue #224,
FR-04, FR-05, FR-36, FR-37, NFR-08, NFR-20).

The service layer (`nptc.catalogue.designations`, `nptc.catalogue.
collisions`) already has its own unit tests in `test_catalogue_designations.py`
and `test_catalogue_collisions.py`; this module proves the HTTP adapter on
top of it - request/response shape, status codes, the exception-handler
mapping in `nptc.api.errors`, and authorisation (FR-44) - against the real
`create_app()`, following `test_api_catalogue_bindings.py`'s own shape
exactly.

The negative case is the point (CLAUDE.md): every domain refusal and every
authorisation refusal (no credential, no permission, no MFA step-up) has
its own test here, not just the happy path.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue.entries import create_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


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

_REASON = "Adding a synonym seen in the current SPIA edition."


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _seed_entry(
    api: ApiTestApp,
    *,
    preferred_term: str = "Full blood count",
    status: str = "draft",
) -> str:
    entry = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for issue #224 API test",
        status=status,
    )
    api.session.flush()
    return entry.business_key


def _token_with_role(api: ApiTestApp, *, subject: str, role: Role, with_mfa: bool = True) -> str:
    """Signs `subject` in, grants `role`, and returns a token - with an
    `acr` claim the realm maps to LoA-2 unless `with_mfa` is `False`,
    matching `test_api_catalogue_bindings.py::_admin_token`'s own shape,
    generalised over the role since this router's four routes are gated
    on two different permissions held by two different roles."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    return _token_with_role(api, subject=subject, role=Role.ADMINISTRATOR, with_mfa=with_mfa)


def _audit_event_count(api: ApiTestApp) -> int:
    return api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _add(api: ApiTestApp, business_key: str, token: str | None, **overrides: object) -> Any:
    body: dict[str, object] = {"terms": ["FBC"], "reason": _REASON}
    body.update(overrides)
    return api.post(f"/catalogue/entries/{business_key}/designations", token=token, json=body)


# --- happy paths -------------------------------------------------------


@pytest.mark.req("FR-04")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_add_designations_returns_201_with_the_batch_as_stored(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-add-happy")
    before = _audit_event_count(api)

    response = _add(api, business_key, token, terms=["FBC", "CBC"])

    assert response.status_code == 201, response.text
    body = response.json()
    terms = {d["term"] for d in body["designations"]}
    assert terms == {"FBC", "CBC"}
    assert all(d["use"] == "synonym" for d in body["designations"])
    assert all(d["status"] == "active" for d in body["designations"])
    assert body["warnings"] == []
    assert _audit_event_count(api) == before + 2


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_add_designations_location_header_points_at_a_route_that_actually_serves_it(
    api: ApiTestApp,
) -> None:
    business_key = _seed_entry(api, status="active")
    token = _admin_token(api, subject="sub-add-location")

    response = _add(api, business_key, token)

    assert response.status_code == 201, response.text
    location = response.headers["Location"]
    assert location == f"/api/v1/catalogue/entries/{business_key}"

    followed = api.client.get(location, headers={"Authorization": f"Bearer {token}"})
    assert followed.status_code == 200, followed.text
    terms = {d["term"] for d in followed.json()["designations"]}
    assert "FBC" in terms


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_add_a_non_en_au_preferred_designation(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-add-preferred")

    response = _add(
        api,
        business_key,
        token,
        terms=["Panui toto katoa"],
        use="preferred",
        language="mi-NZ",
    )

    assert response.status_code == 201, response.text
    (designation,) = response.json()["designations"]
    assert designation["use"] == "preferred"
    assert designation["language"] == "mi-NZ"


@pytest.mark.req("FR-04")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_amend_designation_edits_the_term_and_returns_it(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-amend-happy")
    _add(api, business_key, token)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/amendment",
        token=token,
        json={"term": "FBC", "new_term": "Full Blood Count", "reason": "Correcting the synonym"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["designation"]["term"] == "Full Blood Count"
    assert body["warnings"] == []
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_amend_resolves_a_case_and_punctuation_variant_of_the_stored_term(
    api: ApiTestApp,
) -> None:
    """Addressing is by comparison key, not the raw term
    (`load_active_designation`) - naming a variant of the stored term
    still resolves it."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-amend-variant")
    _add(api, business_key, token, terms=["17-OHP"])

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/amendment",
        token=token,
        json={
            "term": "17 ohp",
            "new_term": "17-Hydroxyprogesterone",
            "reason": "Expanding the abbreviation",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["designation"]["term"] == "17-Hydroxyprogesterone"


@pytest.mark.req("FR-36")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_retire_designation_requires_and_records_a_reason(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-happy")
    _add(api, business_key, token)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/retirement",
        token=token,
        json={"term": "FBC", "reason": "Superseded during SPIA edition update."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "retired"
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_add_returns_the_ada2_warning_and_it_stops_recurring_once_acknowledged(
    api: ApiTestApp,
) -> None:
    """PRD Appendix A.5's warning-severity fixture: `'ADA2'` attached to
    three adenosine deaminase entries, differing only by specimen. Warns
    on the third add, permits the save, and stops warning for that entry
    once acknowledged (FR-05)."""
    token = _admin_token(api, subject="sub-ada2")
    first_entry = _seed_entry(api, preferred_term="Adenosine deaminase")
    second_entry = _seed_entry(api, preferred_term="Adenosine deaminase CSF")
    third_entry = _seed_entry(api, preferred_term="Adenosine deaminase pleural fluid")
    _add(api, first_entry, token, terms=["ADA2"])
    _add(api, second_entry, token, terms=["ADA2"])

    response = _add(api, third_entry, token, terms=["ADA2"])

    assert response.status_code == 201, response.text
    warnings = response.json()["warnings"]
    assert {w["business_key"] for w in warnings} == {first_entry, second_entry}

    ack_response = api.post(
        f"/catalogue/entries/{third_entry}/designations/acknowledgement",
        token=token,
        json={"term": "ADA2", "reason": "Genuinely ambiguous, disambiguated by specimen."},
    )
    assert ack_response.status_code == 200, ack_response.text

    # Retiring and re-adding the same term on the third entry is what
    # re-triggers `warning_collisions` (the amendment route only checks
    # the term it just amended *to*) - simplest way to prove the
    # acknowledgement actually silenced it rather than just returning 200.
    api.post(
        f"/catalogue/entries/{third_entry}/designations/retirement",
        token=token,
        json={"term": "ADA2", "reason": "Retiring to re-add and re-check the warning"},
    )
    recheck = _add(api, third_entry, token, terms=["ADA2"])
    assert recheck.status_code == 201, recheck.text
    assert recheck.json()["warnings"] == []


# --- domain refusals -----------------------------------------------------


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_add_a_term_colliding_with_another_entrys_preferred_term_is_409_naming_it(
    api: ApiTestApp,
) -> None:
    """PRD Appendix A.5's error-severity fixture, and PRD SS17.2 item 5:
    the refusal names the colliding entry, not a bare 409."""
    token = _admin_token(api, subject="sub-collision")
    adrenal_ab_entry = _seed_entry(api, preferred_term="Adrenal Ab")
    other_entry = _seed_entry(api, preferred_term="21-Hydroxylase Ab")

    response = _add(api, other_entry, token, terms=["Adrenal Ab"])

    assert response.status_code == 409, response.text
    body = response.json()
    collisions = body["collisions"]
    assert collisions[0]["business_key"] == adrenal_ab_entry
    assert collisions[0]["preferred_term"] == "Adrenal Ab"
    assert collisions[0]["severity"] == "error"


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_add_a_duplicate_active_term_on_the_same_entry_is_409(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-duplicate")
    _add(api, business_key, token, terms=["FBC"])

    response = _add(api, business_key, token, terms=["FBC"])

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_a_second_active_preferred_term_in_one_language_is_409(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-second-preferred")
    _add(api, business_key, token, terms=["Panui toto katoa"], use="preferred", language="mi-NZ")

    response = _add(
        api, business_key, token, terms=["Tetahi atu kupu"], use="preferred", language="mi-NZ"
    )

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_adding_an_en_au_preferred_designation_is_422_not_500(api: ApiTestApp) -> None:
    """`ck_designation_no_en_au_preferred` (ADR-0022) - refused as a
    pydantic 422 before the request ever reaches the ORM, not an unmapped
    `IntegrityError`."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-en-au-preferred")

    response = _add(api, business_key, token, terms=["Full blood count"], use="preferred")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_adding_a_lowercase_en_au_preferred_designation_is_422_not_201(api: ApiTestApp) -> None:
    """`en-au` must be caught by the same ADR-0022 exclusion as `en-AU` -
    the request's `language` field is canonicalised before this check runs
    (`_WithLanguage`), so a caller cannot bypass it with a differently-cased
    tag (issue #224 review finding 2)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-en-au-preferred-lowercase")

    response = _add(
        api, business_key, token, terms=["Full blood count"], use="preferred", language="en-au"
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_adding_more_than_one_preferred_term_at_once_is_422(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-preferred-batch")

    response = _add(
        api,
        business_key,
        token,
        terms=["Panui toto katoa", "Tetahi atu kupu"],
        use="preferred",
        language="mi-NZ",
    )

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_add_an_unrecognised_use_is_422_not_500(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-bad-use")

    response = _add(api, business_key, token, use="not-a-real-use")

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_add_a_malformed_language_tag_is_422_not_500(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-bad-language")

    response = _add(api, business_key, token, language="not a bcp47 tag")

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_add_a_blank_term_is_422_not_500(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-blank-term")

    response = _add(api, business_key, token, terms=["   "])

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_adding_more_than_the_batch_limit_of_terms_is_422(api: ApiTestApp) -> None:
    """Each term in the batch holds a `pg_advisory_xact_lock` until commit
    (`add_synonyms`'s own docstring) - an unbounded batch is unbounded lock
    contention for one request (issue #224 review finding 4)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-batch-limit")

    response = _add(api, business_key, token, terms=[f"Term {i}" for i in range(101)])

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_amending_a_term_that_is_not_currently_active_is_404_not_409(api: ApiTestApp) -> None:
    """Every route below addresses a designation by its currently-*active*
    term - a term never added, or already retired, is simply not
    addressable this way any more, matching `CodeBindingNotFoundError`'s
    own reasoning."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-amend-not-found")

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/amendment",
        token=token,
        json={"term": "No such term", "new_term": "Something else", "reason": _REASON},
    )

    assert response.status_code == 404, response.text


@pytest.mark.integration
def test_retiring_an_already_retired_term_is_404_not_409(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-twice")
    _add(api, business_key, token)
    api.post(
        f"/catalogue/entries/{business_key}/designations/retirement",
        token=token,
        json={"term": "FBC", "reason": "First retirement"},
    )

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/retirement",
        token=token,
        json={"term": "FBC", "reason": "Second retirement attempt"},
    )

    assert response.status_code == 404, response.text


@pytest.mark.integration
def test_no_entry_for_the_given_business_key_is_404(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-no-entry")

    response = _add(api, "NPTC-999999", token)

    assert response.status_code == 404, response.text


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_add_with_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)

    response = _add(api, business_key, None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_add_authenticated_without_the_permission_is_403_with_no_challenge(
    api: ApiTestApp,
) -> None:
    business_key = _seed_entry(api)
    token = api.token(subject="sub-no-permission")

    response = _add(api, business_key, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_add_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-admin-no-mfa", with_mfa=False)

    response = _add(api, business_key, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_acknowledge_with_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/acknowledgement",
        token=None,
        json={"term": "ADA2", "reason": _REASON},
    )

    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_acknowledge_authenticated_without_the_permission_is_403_with_no_challenge(
    api: ApiTestApp,
) -> None:
    """`validation.acknowledge` is held by Reviewer *and* Administrator -
    unlike `catalogue.edit_published` it is not Administrator-only, so it
    is not in `MFA_REQUIRED_PERMISSIONS` and never carries a step-up
    challenge, for any role."""
    business_key = _seed_entry(api)
    token = api.token(subject="sub-no-ack-permission")

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/acknowledgement",
        token=token,
        json={"term": "ADA2", "reason": _REASON},
    )

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_reviewer_can_acknowledge_a_collision(api: ApiTestApp) -> None:
    """`Permission.VALIDATION_ACKNOWLEDGE` is held by `Role.REVIEWER`, not
    only `Role.ADMINISTRATOR` - the other three routes on this router are
    Administrator-only (`catalogue.edit_published`)."""
    business_key = _seed_entry(api)
    admin_token = _admin_token(api, subject="sub-reviewer-setup")
    _add(api, business_key, admin_token, terms=["ADA2"])
    reviewer_token = _token_with_role(api, subject="sub-reviewer", role=Role.REVIEWER)

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/acknowledgement",
        token=reviewer_token,
        json={"term": "ADA2", "reason": "Ambiguous but disambiguated by specimen."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True


@pytest.mark.req("FR-05")
@pytest.mark.integration
def test_acknowledging_the_same_collision_twice_returns_created_false_the_second_time(
    api: ApiTestApp,
) -> None:
    """Distinguishes "this call recorded it" from "already recorded" - a
    caller re-submitting the same acknowledgement should not read a `200`
    as proof its own note was kept (issue #224 review finding 5)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-ack-repeat")
    _add(api, business_key, token, terms=["ADA2"])
    ack_url = f"/catalogue/entries/{business_key}/designations/acknowledgement"

    first = api.post(ack_url, token=token, json={"term": "ADA2", "reason": _REASON})
    second = api.post(
        ack_url, token=token, json={"term": "ADA2", "reason": "A different reason entirely"}
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["reason"] == _REASON


@pytest.mark.req("FR-04")
@pytest.mark.integration
def test_acknowledge_with_a_malformed_language_tag_is_422_not_500(api: ApiTestApp) -> None:
    """Unlike `Designation`, `designation_collision_acknowledgement` has no
    `@validates("language")` hook - without the request-level check this
    reached the table's `CHECK` constraint as an unmapped `IntegrityError`
    (issue #224 review finding 1)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-ack-bad-language")

    response = api.post(
        f"/catalogue/entries/{business_key}/designations/acknowledgement",
        token=token,
        json={"term": "ADA2", "reason": _REASON, "language": "not a bcp47 tag"},
    )

    assert response.status_code == 422, response.text


# --- response hygiene (NFR-04, NFR-26) ------------------------------------


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_write_responses_contain_no_internal_identifier(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-hygiene")

    add_response = _add(api, business_key, token, terms=["FBC"])
    assert add_response.status_code == 201, add_response.text
    for designation in add_response.json()["designations"]:
        assert "id" not in designation
        assert "entry_id" not in designation

    ack_response = api.post(
        f"/catalogue/entries/{business_key}/designations/acknowledgement",
        token=token,
        json={"term": "FBC", "reason": "Just recording an acknowledgement for the hygiene test."},
    )
    assert ack_response.status_code == 200, ack_response.text
    ack_body = ack_response.json()
    assert "id" not in ack_body
    assert "entry_id" not in ack_body
    assert "acknowledged_by_user_id" not in ack_body
