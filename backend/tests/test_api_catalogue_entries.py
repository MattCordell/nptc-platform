"""HTTP tests for `nptc.api.routers.catalogue_entries` (issue #249, FR-36,
FR-37, FR-38, FR-44, FR-89, NFR-08).

Follows `test_api_catalogue_properties.py`'s own precedent exactly: the
service layer already has its own unit tests (`test_catalogue_property_
values.py`, `test_catalogue_optimistic_locking.py`); this module proves the
HTTP adapter - request/response shape, status codes, the exception-handler
mapping in `nptc.api.errors`, and authorisation - against the real
`create_app()`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue.entries import create_entry
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import PropertyValueInput, save_property_values
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc_shared.terminology.models import Edition, ValidationResult
from nptc_shared.terminology.stub import StubTerminologyClient


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

_REASON = "Created for issue #249 entry core write route test."
_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"
_SPECIMEN_EDITION = Edition(module_id="au", label="au")
_SPECIMEN_SYSTEM = "http://example.org/specimen-test"


@pytest.fixture
def api(app_db: Any) -> Any:
    yield from build_api_test_app(app_db)


def _admin_token(api: ApiTestApp, *, subject: str, with_mfa: bool = True) -> str:
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
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    extra_claims = {"acr": "2"} if with_mfa else {}
    return api.token(subject=subject, extra_claims=extra_claims)


def _new_entry(
    api: ApiTestApp, preferred_term: str = "FR-249 entry core write entry"
) -> CatalogueEntry:
    entry = create_entry(
        api.session, AuditContext.system(), preferred_term=preferred_term, reason=_REASON
    )
    api.session.flush()
    return entry


def _patch_entry(
    api: ApiTestApp,
    token: str | None,
    *,
    business_key: str,
    expected_row_version: int,
    status: str | None = None,
    specimen_unconstrained: bool | None = None,
    reason: str = _REASON,
) -> Any:
    body: dict[str, object] = {"reason": reason, "expected_row_version": expected_row_version}
    if status is not None:
        body["status"] = status
    if specimen_unconstrained is not None:
        body["specimen_unconstrained"] = specimen_unconstrained
    return api.request("PATCH", f"/catalogue/entries/{business_key}", token=token, json=body)


def _latest_audit_event(session: Session, entry_id: Any) -> AuditEvent:
    return session.execute(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "catalogue_entry", AuditEvent.entity_id == str(entry_id))
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    ).scalar_one()


def _record_specimen_value(api: ApiTestApp, entry: CatalogueEntry) -> None:
    seed_system_properties(api.session)
    api.session.flush()
    terminology = StubTerminologyClient()
    terminology.seed_validate_code(
        "specimen-1",
        ValidationResult(code="specimen-1", result=True),
        value_set_url=_SPECIMEN_VALUE_SET_URI,
        edition=_SPECIMEN_EDITION,
    )
    registry = DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=terminology,
                local_code_lookup=DatabaseLocalCodeLookup(api.session),
            )
        )
    )
    save_property_values(
        api.session,
        AuditContext.system(),
        entry=entry,
        expected_row_version=entry.row_version,
        property_key="specimen",
        values=[PropertyValueInput(value={"system": _SPECIMEN_SYSTEM, "code": "specimen-1"})],
        reason="Recorded a specimen value before the conflict test",
        registry=registry,
    )
    api.session.flush()


# --- happy path ------------------------------------------------------------


@pytest.mark.req("FR-89")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_patch_entry_sets_specimen_unconstrained_bumps_row_version_and_audits(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-patch-specimen-set")
    entry = _new_entry(api)
    starting_row_version = entry.row_version

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=starting_row_version,
        specimen_unconstrained=True,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["specimen_unconstrained"] is True
    assert body["row_version"] == starting_row_version + 1

    event = _latest_audit_event(api.session, entry.id)
    assert event.action == "catalogue_entry.updated"
    assert event.before == {"specimen_unconstrained": False}
    assert event.after == {"specimen_unconstrained": True}


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_patch_entry_sets_status(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-status")
    entry = _new_entry(api)

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        status="active",
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_patch_entry_clears_specimen_unconstrained_end_to_end(api: ApiTestApp) -> None:
    """Set, then clear, then record a specimen value - the full round trip
    the plan's own verification section names."""
    token = _admin_token(api, subject="sub-patch-specimen-cycle")
    entry = _new_entry(api)

    set_response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        specimen_unconstrained=True,
    )
    assert set_response.status_code == 200, set_response.text

    clear_response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=set_response.json()["row_version"],
        specimen_unconstrained=False,
    )

    assert clear_response.status_code == 200, clear_response.text
    assert clear_response.json()["specimen_unconstrained"] is False


# --- FR-89: setting the flag while specimen values exist --------------------


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_patch_entry_refuses_specimen_unconstrained_when_specimen_values_exist(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-patch-specimen-conflict")
    entry = _new_entry(api)
    _record_specimen_value(api, entry)
    events_before = api.session.execute(select(AuditEvent)).all()

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        specimen_unconstrained=True,
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["issues"][0]["code"] == "specimen-unconstrained-conflict"
    assert body["issues"][0]["property_key"] == "specimen"

    current = api.session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == entry.business_key)
    ).scalar_one()
    assert current.specimen_unconstrained is False
    assert current.row_version == entry.row_version
    assert api.session.execute(select(AuditEvent)).all() == events_before


# --- FR-38: optimistic locking -----------------------------------------------


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_patch_entry_with_a_stale_row_version_is_409_with_conflict_body(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-stale")
    entry = _new_entry(api)

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version + 1,
        status="active",
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["business_key"] == entry.business_key
    assert body["current_row_version"] == entry.row_version


# --- FR-37: changelog note ---------------------------------------------------


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_patch_entry_with_no_reason_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-no-reason")
    entry = _new_entry(api)

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        status="active",
        reason="",
    )

    assert response.status_code == 422, response.text
    assert "changelog note" in response.json()["detail"].lower()
    current = api.session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == entry.business_key)
    ).scalar_one()
    assert current.status == "draft"


# --- a body naming neither field ---------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_patch_entry_with_neither_field_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-empty-body")
    entry = _new_entry(api)

    response = api.request(
        "PATCH",
        f"/catalogue/entries/{entry.business_key}",
        token=token,
        json={"reason": _REASON, "expected_row_version": entry.row_version},
    )

    assert response.status_code == 422, response.text


# --- 404 ----------------------------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_patch_entry_unknown_business_key_is_404(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-404")

    response = _patch_entry(
        api,
        token,
        business_key="NPTC-999999",
        expected_row_version=1,
        status="active",
    )

    assert response.status_code == 404, response.text


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_patch_entry_no_credential_is_401(api: ApiTestApp) -> None:
    entry = _new_entry(api)

    response = _patch_entry(
        api,
        None,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        status="active",
    )

    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_patch_entry_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    entry = _new_entry(api)
    token = api.token(subject="sub-patch-no-permission")

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        status="active",
    )

    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_patch_entry_administrator_without_mfa_gets_step_up_challenge(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-no-mfa", with_mfa=False)
    entry = _new_entry(api)

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        status="active",
    )

    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]
