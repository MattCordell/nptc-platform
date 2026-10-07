"""HTTP tests for `nptc.api.routers.catalogue_entries` (issue #249, FR-36,
FR-37, FR-38, FR-44, NFR-08).

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

from nptc.audit.writer import AuditContext
from nptc.catalogue.entries import create_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry


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

_REASON = "Created for issue #249 entry core write route test."


@pytest.fixture
def api(app_db: Any) -> Any:
    yield from build_api_test_app(app_db)


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
    reason: str = _REASON,
) -> Any:
    body: dict[str, object] = {"reason": reason, "expected_row_version": expected_row_version}
    if status is not None:
        body["status"] = status
    return api.request("PATCH", f"/catalogue/entries/{business_key}", token=token, json=body)


# --- happy path ------------------------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("status", ["draft", "active", "deprecated", "withdrawn"])
def test_patch_entry_sets_status_to_every_recognised_value(api: ApiTestApp, status: str) -> None:
    """A new entry starts `draft` (`create_entry`'s own default), so the
    `status="draft"` case of this parametrize is genuinely a no-op
    resubmission, not a transition - see `test_patch_entry_resubmitting_
    the_current_status_is_a_no_op` for that case asserted on its own
    terms. The other three are real transitions and get the NFR-08
    audit-event assertion the no-op case cannot give."""
    token = api.admin_token(subject=f"sub-patch-status-{status}")
    entry = _new_entry(api, preferred_term=f"FR-36 status entry {status}")
    starting_row_version = entry.row_version
    events_before = api.session.execute(select(AuditEvent)).all()

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=starting_row_version,
        status=status,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == status
    assert set(response.json()) == {"status", "row_version"}

    if status == "draft":
        assert response.json()["row_version"] == starting_row_version
        assert api.session.execute(select(AuditEvent)).all() == events_before
        return

    assert response.json()["row_version"] == starting_row_version + 1
    event = latest_audit_event(api.session, entity_type="catalogue_entry", entity_id=entry.id)
    assert event.action == "catalogue_entry.updated"
    assert event.before == {"status": "draft"}
    assert event.after == {"status": status}


@pytest.mark.req("FR-36")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_patch_entry_resubmitting_the_current_status_is_a_no_op(api: ApiTestApp) -> None:
    """`save_entry`'s own no-op short-circuit reaches this route: a body
    naming `status` but submitting the entry's already-current value
    returns `200` with an *unchanged* `row_version` and writes no audit
    event - a different outcome from a body naming no status, which is
    refused `422` (`test_patch_entry_with_no_status_is_422`)."""
    token = api.admin_token(subject="sub-patch-status-no-op")
    entry = _new_entry(api)
    events_before = api.session.execute(select(AuditEvent)).all()

    response = _patch_entry(
        api,
        token,
        business_key=entry.business_key,
        expected_row_version=entry.row_version,
        status="draft",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft"
    assert body["row_version"] == entry.row_version
    assert api.session.execute(select(AuditEvent)).all() == events_before


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_patch_entry_with_an_unrecognised_status_is_422(api: ApiTestApp) -> None:
    token = api.admin_token(subject="sub-patch-bad-status")
    entry = _new_entry(api)

    response = api.request(
        "PATCH",
        f"/catalogue/entries/{entry.business_key}",
        token=token,
        json={
            "status": "published",
            "reason": _REASON,
            "expected_row_version": entry.row_version,
        },
    )

    assert response.status_code == 422, response.text


# --- FR-38: optimistic locking -----------------------------------------------


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_patch_entry_with_a_stale_row_version_is_409_with_conflict_body(api: ApiTestApp) -> None:
    token = api.admin_token(subject="sub-patch-stale")
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
    token = api.admin_token(subject="sub-patch-no-reason")
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


# --- a body naming no status, or the retired flag ------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_patch_entry_with_no_status_is_422(api: ApiTestApp) -> None:
    token = api.admin_token(subject="sub-patch-empty-body")
    entry = _new_entry(api)

    response = api.request(
        "PATCH",
        f"/catalogue/entries/{entry.business_key}",
        token=token,
        json={"reason": _REASON, "expected_row_version": entry.row_version},
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_a_stale_client_sending_the_retired_flag_is_refused_not_ignored(api: ApiTestApp) -> None:
    """The flag moved into the specimen value (ADR-0044). A client that still sends it must
    fail loudly: a silent 200 would tell it the entry now accepts any specimen."""
    token = api.admin_token(subject="sub-patch-retired-flag")
    entry = _new_entry(api)

    response = api.request(
        "PATCH",
        f"/catalogue/entries/{entry.business_key}",
        token=token,
        json={
            "status": "active",
            "specimen_unconstrained": True,
            "reason": _REASON,
            "expected_row_version": entry.row_version,
        },
    )

    assert response.status_code == 422, response.text
    current = api.session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == entry.business_key)
    ).scalar_one()
    assert current.status == "draft"


# --- 404 ----------------------------------------------------------------


@pytest.mark.req("FR-36")
@pytest.mark.integration
def test_patch_entry_unknown_business_key_is_404(api: ApiTestApp) -> None:
    token = api.admin_token(subject="sub-patch-404")

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
    token = api.admin_token(subject="sub-patch-no-mfa", with_mfa=False)
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
