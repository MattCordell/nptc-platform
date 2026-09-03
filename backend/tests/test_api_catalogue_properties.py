"""HTTP tests for `nptc.api.routers.catalogue_properties` (issue #248,
FR-09, FR-10, FR-11, FR-37, FR-38, FR-44, FR-88, FR-89, NFR-08).

Follows `test_api_catalogue_bindings.py`'s own precedent exactly: the
service layer already has its own unit tests (`test_catalogue_property_
values.py`); this module proves the HTTP adapter - request/response shape,
status codes, the exception-handler mapping in `nptc.api.errors`, and
authorisation - against the real `create_app()`.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
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
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.property_value import PropertyValue
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc_shared.terminology.models import Edition, ValidationResult

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

_REASON = "Created for issue #248 property-value write route test."
_SPECIMEN_VALUE_SET_URI = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"
_SPECIMEN_EDITION = Edition(module_id="au", label="au")
_SPECIMEN_SYSTEM = "http://example.org/specimen-test"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
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


def _audit_event_count(api: ApiTestApp) -> int:
    return api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()


def _property_value_count(api: ApiTestApp, *, entry_id: uuid.UUID, property_key: str) -> int:
    return api.session.execute(
        select(func.count())
        .select_from(PropertyValue)
        .where(PropertyValue.entry_id == entry_id, PropertyValue.property_key == property_key)
    ).scalar_one()


def _unique_key(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _create_string_property(
    api: ApiTestApp, token: str, *, key: str, max_length: int | None = None
) -> Any:
    constraints: dict[str, object] = {"maxLength": max_length} if max_length is not None else {}
    response = api.post(
        "/registry/properties",
        token=token,
        json={
            "key": key,
            "label": key.replace("_", " ").title(),
            "datatype": "string",
            "cardinality": "0..1",
            "scope": "both",
            "display_order": 0,
            "constraints": constraints,
            "reason": _REASON,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _new_entry(
    api: ApiTestApp, preferred_term: str = "FR-248 property write entry"
) -> CatalogueEntry:
    entry = create_entry(
        api.session, AuditContext.system(), preferred_term=preferred_term, reason=_REASON
    )
    api.session.flush()
    return entry


def _put_values(
    api: ApiTestApp,
    token: str | None,
    *,
    business_key: str,
    property_key: str,
    values: list[dict[str, object]],
    expected_row_version: int,
    reason: str = _REASON,
) -> Any:
    return api.request(
        "PUT",
        f"/catalogue/entries/{business_key}/properties/{property_key}",
        token=token,
        json={"values": values, "reason": reason, "expected_row_version": expected_row_version},
    )


# --- happy path --------------------------------------------------------


@pytest.mark.req("FR-09")
@pytest.mark.req("FR-38")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_save_property_values_replaces_whole_set_bumps_row_version_one_audit_event(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-save-happy")
    key = _unique_key("save_happy")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api)
    before = _audit_event_count(api)
    # Captured before the PUT: `entry` is the same identity-mapped ORM
    # instance the route's own session mutates, so `entry.row_version`
    # already reflects the post-write value once the call returns.
    starting_row_version = entry.row_version

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "first value"}],
        expected_row_version=starting_row_version,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["value"] for item in body["values"]] == ["first value"]
    assert body["values"][0]["status"] == "active"
    assert body["row_version"] == starting_row_version + 1
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_second_write_replaces_the_first(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-save-replace")
    key = _unique_key("save_replace")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api)

    first = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "old"}],
        expected_row_version=entry.row_version,
    )
    assert first.status_code == 200, first.text

    second = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "new"}],
        expected_row_version=first.json()["row_version"],
    )

    assert second.status_code == 200, second.text
    assert [item["value"] for item in second.json()["values"]] == ["new"]


# --- FR-38: optimistic locking ------------------------------------------


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_save_property_values_with_a_stale_row_version_is_409_with_conflict_body(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-save-stale")
    key = _unique_key("save_stale")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "a value"}],
        expected_row_version=entry.row_version + 1,
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["business_key"] == entry.business_key
    assert body["current_row_version"] == entry.row_version


# --- FR-37: changelog note ------------------------------------------------


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_save_property_values_with_no_reason_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-save-no-reason")
    key = _unique_key("save_no_reason")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "a value"}],
        expected_row_version=entry.row_version,
        reason="",
    )

    assert response.status_code == 422, response.text


# --- FR-09/FR-10: field-level validation, typed 422 -----------------------


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_schema_violation_is_422_with_named_issue(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-save-bad-value")
    key = _unique_key("save_bad_value")
    created = _create_string_property(api, token, key=key, max_length=5)
    entry = _new_entry(api)
    before = _audit_event_count(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "this value is far too long"}],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert len(body["issues"]) == 1
    issue = body["issues"][0]
    assert issue["property_key"] == key
    assert issue["label"] == created["label"]
    assert issue["ordinal"] == 0
    # The `maxLength` constraint is enforced by the generic JSON Schema
    # check `nptc.registry.schema.validate_values` runs before a value ever
    # reaches the handler's own `validate()` - see that function's own
    # docstring - so the issue code is the generic `schema-violation`, not
    # `StringHandler.validate`'s own `max-length-exceeded`.
    assert issue["code"] == "schema-violation"
    # FR-09's own acceptance criterion: a rejected write leaves no partial
    # `property_value` state and no audit event.
    assert _property_value_count(api, entry_id=entry.id, property_key=key) == 0
    assert _audit_event_count(api) == before


# --- FR-11: deprecated property refusal -----------------------------------


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_save_property_values_against_a_deprecated_property_is_422_untouched(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-save-deprecated")
    key = _unique_key("save_deprecated")
    created = _create_string_property(api, token, key=key)
    entry = _new_entry(api)
    first = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "recorded before deprecation"}],
        expected_row_version=entry.row_version,
    )
    assert first.status_code == 200, first.text

    deprecate_response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    assert deprecate_response.status_code == 200, deprecate_response.text
    before = _audit_event_count(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "should be refused"}],
        expected_row_version=first.json()["row_version"],
    )

    assert response.status_code == 422, response.text
    # FR-11's own acceptance criterion: the value recorded before
    # deprecation is untouched, and the rejected write leaves no audit
    # event.
    stored_value = api.session.execute(
        select(PropertyValue.value).where(
            PropertyValue.entry_id == entry.id, PropertyValue.property_key == key
        )
    ).scalar_one()
    assert stored_value == "recorded before deprecation"
    assert _audit_event_count(api) == before


# --- FR-88/FR-89: Specimen -------------------------------------------------


@pytest.mark.req("FR-88")
@pytest.mark.integration
def test_specimen_accepts_the_samples_seven_specimen_worst_case(api: ApiTestApp) -> None:
    from nptc.db.bootstrap import seed_system_properties

    token = _admin_token(api, subject="sub-specimen-seven")
    seed_system_properties(api.session)
    api.session.flush()
    entry = _new_entry(api)
    codes = [f"specimen-{n}" for n in range(7)]
    for code in codes:
        api.terminology.seed_validate_code(
            code,
            ValidationResult(code=code, result=True),
            value_set_url=_SPECIMEN_VALUE_SET_URI,
            edition=_SPECIMEN_EDITION,
        )

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key="specimen",
        values=[{"value": {"system": _SPECIMEN_SYSTEM, "code": code}} for code in codes],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["values"]) == 7


@pytest.mark.req("FR-89")
@pytest.mark.integration
def test_specimen_rejects_the_literal_value_any(api: ApiTestApp) -> None:
    from nptc.db.bootstrap import seed_system_properties

    token = _admin_token(api, subject="sub-specimen-any")
    seed_system_properties(api.session)
    api.session.flush()
    entry = _new_entry(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key="specimen",
        values=[{"value": {"system": _SPECIMEN_SYSTEM, "code": "Any"}}],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 422, response.text
    assert response.json()["issues"][0]["code"] == "forbidden-code"


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_save_property_values_no_credential_is_401(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-save-setup-401")
    key = _unique_key("save_no_cred")
    _create_string_property(api, admin_token, key=key)
    entry = _new_entry(api)

    response = _put_values(
        api,
        None,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "a value"}],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_save_property_values_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-save-setup-403")
    key = _unique_key("save_no_permission")
    _create_string_property(api, admin_token, key=key)
    entry = _new_entry(api)
    token = api.token(subject="sub-save-no-permission")

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "a value"}],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_save_property_values_administrator_without_mfa_gets_step_up_challenge(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-save-no-mfa", with_mfa=False)
    key = _unique_key("save_no_mfa")
    admin_token = _admin_token(api, subject="sub-save-no-mfa-setup")
    _create_string_property(api, admin_token, key=key)
    entry = _new_entry(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "a value"}],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]
