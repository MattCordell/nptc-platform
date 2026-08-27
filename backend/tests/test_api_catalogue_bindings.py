"""HTTP tests for `nptc.api.routers.catalogue_bindings` (issue #219, FR-06,
FR-08, FR-36, NFR-08, NFR-20).

The service layer (`nptc.catalogue.bindings`) already has its own unit
tests in `test_catalogue_bindings.py`; this module proves the HTTP adapter
on top of it - request/response shape, status codes, the exception-handler
mapping in `nptc.api.errors`, and authorisation (FR-44) - against the real
`create_app()`, not a throwaway one.

The negative case is the point (CLAUDE.md): every domain refusal
(`CodeBinding*`) and every authorisation refusal (no credential, no
permission, no MFA step-up) has its own test here, not just the happy path.
"""

from __future__ import annotations

import importlib.util
import re
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

REPO_ROOT = Path(__file__).resolve().parents[2]


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

#: Real, Verhoeff-valid SCTIDs - the same two `test_catalogue_bindings.py`
#: and `public_catalogue_support.py` already use, plus one more for the
#: cross-entry conflict test, since that needs three distinct codes live at
#: once. Invented digits do not insert at all: `code`'s `CHECK` constraint
#: calls `nptc_sctid_is_valid`.
CODE_A = "391483001"
FSN_A = "Microscopy (acid fast bacilli) (procedure)"
AU_PREFERRED_A = "Microscopy (acid fast bacilli)"
CODE_B = "71388002"
FSN_B = "Procedure (procedure)"
CODE_C = "122192001"
FSN_C = "Injury of hip region (disorder)"

_REASON = "Bound during onboarding of the current SPIA release."


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _seed_entry(api: ApiTestApp, *, preferred_term: str = "Full blood count") -> str:
    entry = create_entry(
        api.session,
        AuditContext.system(),
        preferred_term=preferred_term,
        reason="Created for issue #219 API test",
    )
    api.session.flush()
    return entry.business_key


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
    """Signs `subject` in, grants `Role.ADMINISTRATOR`, and returns a token
    - with an `acr` claim the realm maps to LoA-2 unless `with_mfa` is
    `False`, matching `test_api_error_mapping.py`'s own MFA pair."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _audit_event_count(api: ApiTestApp) -> int:
    return api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _bind(api: ApiTestApp, business_key: str, token: str, **overrides: object) -> Any:
    body = {"code": CODE_A, "fsn": FSN_A, "au_preferred_term": AU_PREFERRED_A, "reason": _REASON}
    body.update(overrides)
    return api.post(f"/catalogue/entries/{business_key}/bindings", token=token, json=body)


# --- happy paths -------------------------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_bind_code_returns_201_with_the_binding_as_served(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-bind-happy")
    before = _audit_event_count(api)

    response = _bind(api, business_key, token)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == CODE_A
    assert isinstance(body["code"], str)
    assert body["fsn"] == FSN_A
    assert body["au_preferred_term"] == AU_PREFERRED_A
    assert body["status"] == "active"
    assert body["retirement_reason"] is None
    assert body["replaced_by_code"] is None
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-36")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_retire_binding_requires_and_records_a_reason(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-happy")
    _bind(api, business_key, token)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Superseded during SPIA edition update."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "retired"
    assert body["retirement_reason"] == "Superseded during SPIA edition update."
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-08")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_replace_binding_retires_creates_and_links_in_one_request(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-replace-happy")
    _bind(api, business_key, token)
    before = _audit_event_count(api)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/replacement",
        token=token,
        json={
            "successor": {"code": CODE_B, "fsn": FSN_B},
            "reason": "Replaced with the successor concept for this SPIA edition.",
        },
    )

    assert response.status_code == 200, response.text
    items = {item["code"]: item for item in response.json()["items"]}
    assert items[CODE_A]["status"] == "retired"
    assert items[CODE_A]["replaced_by_code"] == CODE_B
    assert items[CODE_B]["status"] == "active"
    # Three audit events: retired, created, replacement_linked - all in the
    # one request's transaction (the module docstring's whole point).
    assert _audit_event_count(api) == before + 3


# --- domain refusals -----------------------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_bind_malformed_sctid_is_422_not_500(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-malformed")

    response = _bind(api, business_key, token, code="not-a-code")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_bind_verhoeff_failing_sctid_is_422(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-verhoeff")

    # One digit off CODE_A's own valid check digit.
    response = _bind(api, business_key, token, code="391483002")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_second_active_binding_on_one_entry_is_409(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-second-active")
    _bind(api, business_key, token)

    response = _bind(api, business_key, token, code=CODE_C, fsn=FSN_C)

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_same_code_active_on_a_different_entry_is_409(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-cross-entry")
    first_entry = _seed_entry(api, preferred_term="Full blood count")
    second_entry = _seed_entry(api, preferred_term="Urine microscopy")
    _bind(api, first_entry, token)

    response = _bind(api, second_entry, token)

    assert response.status_code == 409, response.text


@pytest.mark.integration
def test_retire_without_a_reason_is_422(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-no-reason")
    _bind(api, business_key, token)

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": ""},
    )

    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_retiring_an_already_retired_binding_is_404_not_409(api: ApiTestApp) -> None:
    """`nptc.catalogue.bindings.retire_binding` itself raises
    `CodeBindingAlreadyRetiredError` (409) given an already-retired
    `CodeBinding` instance - but this route addresses a binding by `code`
    through `load_active_binding`, which is scoped to `status == 'active'`
    (see that function's own docstring). A second retirement attempt can
    therefore never reach `retire_binding` with a retired binding in hand:
    the code is simply no longer addressable this way once retired, so the
    honest answer is 404, and `CodeBindingAlreadyRetiredError` is
    unreachable from this particular route (it stays mapped in
    `nptc.api.errors` for whichever future write path holds an already-
    loaded `CodeBinding` rather than resolving one by code)."""
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-double-retire")
    _bind(api, business_key, token)
    api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "First retirement."},
    )

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Second retirement attempt."},
    )

    assert response.status_code == 404, response.text


@pytest.mark.integration
def test_retire_a_code_with_no_active_binding_is_404(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-retire-missing")

    response = api.post(
        f"/catalogue/entries/{business_key}/bindings/{CODE_A}/retirement",
        token=token,
        json={"reason": "Nothing to retire."},
    )

    assert response.status_code == 404, response.text


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)

    response = _bind(api, business_key, token=None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_without_the_permission_is_403_with_no_challenge(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = api.token(subject="sub-no-permission")

    response = _bind(api, business_key, token)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    business_key = _seed_entry(api)
    token = _admin_token(api, subject="sub-admin-no-mfa", with_mfa=False)

    response = _bind(api, business_key, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_conflict_response_names_no_internal_identifier(api: ApiTestApp) -> None:
    """`CodeBindingCodeAlreadyBoundError`'s exception message names the
    other entry's internal UUID (for the log); the response body must not
    (NFR-04/NFR-26) - the same convention every other handler in
    `nptc.api.errors` follows."""
    token = _admin_token(api, subject="sub-conflict-body")
    first_entry = _seed_entry(api, preferred_term="Full blood count")
    second_entry = _seed_entry(api, preferred_term="Urine microscopy")
    _bind(api, first_entry, token)

    response = _bind(api, second_entry, token)

    uuid_pattern = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    assert uuid_pattern.search(response.text) is None, response.text
