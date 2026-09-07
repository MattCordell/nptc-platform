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

latest_audit_event = _load("audit_support").latest_audit_event

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


#: `save_property_values` keys its audit event on `f"{entry.id}:
#: {property_key}"` under `entity_type="property_value_set"` (see that
#: function's own `record_snapshot_change` call) - not on the entry alone,
#: since one entry can hold many properties.
_PROPERTY_VALUE_SET_ENTITY_TYPE = "property_value_set"


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


def _post_bulk_values(
    api: ApiTestApp,
    token: str | None,
    *,
    property_key: str,
    values: list[dict[str, object]],
    entries: list[dict[str, object]],
    reason: str = _REASON,
) -> Any:
    return api.request(
        "POST",
        f"/catalogue/entries/bulk/properties/{property_key}",
        token=token,
        json={"values": values, "reason": reason, "entries": entries},
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
@pytest.mark.req("NFR-08")
@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_save_property_values_first_write_audits_a_whole_set_snapshot_with_reason(
    api: ApiTestApp,
) -> None:
    """The parent-level acceptance criterion #61 owns for property values -
    with the shape this path actually produces flagged, not assumed:
    `save_property_values` diffs via `record_snapshot_change` over the
    *whole value set*, not a field-level diff of one changed value (see
    that function's own `before_payload`/`after_payload` construction). For
    a first write, `existing` is empty so `before` is `None` (the
    `before=before_payload if existing else None` branch) and `after` is
    the one value just stored. The changelog note supplied still reaches
    `AuditEvent.reason` verbatim."""
    token = _admin_token(api, subject="sub-save-audit")
    key = _unique_key("save_audit")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api)
    reason = "Recording the value for issue #61 audit coverage."

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "first value"}],
        expected_row_version=entry.row_version,
        reason=reason,
    )

    assert response.status_code == 200, response.text
    event = latest_audit_event(
        api.session, entity_type=_PROPERTY_VALUE_SET_ENTITY_TYPE, entity_id=f"{entry.id}:{key}"
    )
    assert event.action == "property_value.set"
    assert event.before is None
    assert event.after == {
        "values": [{"ordinal": 0, "value": "first value", "justification": None}]
    }
    assert event.reason == reason


@pytest.mark.req("FR-09")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_save_property_values_second_write_audits_before_and_after_the_replacement(
    api: ApiTestApp,
) -> None:
    """The other half of the snapshot shape: once a value already exists,
    `before` is the whole prior set (`_value_payload` reading the stored
    row back, not the request's own submitted shape), not `None`."""
    token = _admin_token(api, subject="sub-save-replace-audit")
    key = _unique_key("save_replace_audit")
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
    reason = "Replacing the value for issue #61 audit coverage."

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key=key,
        values=[{"value": "new"}],
        expected_row_version=first.json()["row_version"],
        reason=reason,
    )

    assert response.status_code == 200, response.text
    event = latest_audit_event(
        api.session, entity_type=_PROPERTY_VALUE_SET_ENTITY_TYPE, entity_id=f"{entry.id}:{key}"
    )
    assert event.action == "property_value.set"
    assert event.before == {"values": [{"ordinal": 0, "value": "old", "justification": None}]}
    assert event.after == {"values": [{"ordinal": 0, "value": "new", "justification": None}]}
    assert event.reason == reason


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


# --- 404: unknown business_key / unknown property key ---------------------


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_unknown_business_key_is_404(api: ApiTestApp) -> None:
    """Round-2 review: `_RESPONSE_404` names two distinct causes (an
    unknown `business_key` and an unknown property `key`), and neither had
    a test - mirroring `test_api_catalogue_bindings.py`'s own coverage of
    both causes for the identical response shape."""
    token = _admin_token(api, subject="sub-save-404-entry")
    key = _unique_key("save_404_entry")
    _create_string_property(api, token, key=key)

    response = _put_values(
        api,
        token,
        business_key="NPTC-999999",
        property_key=key,
        values=[{"value": "a value"}],
        expected_row_version=1,
    )

    assert response.status_code == 404, response.text


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_save_property_values_unknown_property_key_is_404(api: ApiTestApp) -> None:
    """See `test_save_property_values_unknown_business_key_is_404`'s own
    docstring - the other of `_RESPONSE_404`'s two causes."""
    token = _admin_token(api, subject="sub-save-404-property")
    entry = _new_entry(api)

    response = _put_values(
        api,
        token,
        business_key=entry.business_key,
        property_key="no_such_property_key",
        values=[{"value": "a value"}],
        expected_row_version=entry.row_version,
    )

    assert response.status_code == 404, response.text


# --- FR-37: changelog note ------------------------------------------------


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_save_property_values_with_no_reason_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-save-no-reason")
    key = _unique_key("save_no_reason")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api)
    before = _audit_event_count(api)

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
    # Round-2 review, minor finding: `reason: str` accepts `""` at the
    # pydantic layer, so a status-only assertion would pass whether FR-37's
    # server-side gate fired or a future `min_length=1` on the request
    # model short-circuited it first. Tying the assertion to `detail` (the
    # changelog-note refusal, not a pydantic validation error) and to the
    # write's own acceptance criterion - no row, no audit event - pins it to
    # the gate this test claims to cover.
    assert "changelog note" in response.json()["detail"].lower()
    assert _property_value_count(api, entry_id=entry.id, property_key=key) == 0
    assert _audit_event_count(api) == before


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


# --- issue #265: bulk property-value write route (FR-39) -------------------

_PROPERTY_VALUE_BULK_ENTITY_TYPE = "property_value_bulk"


@pytest.mark.req("FR-39")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_bulk_save_applies_across_entries_and_emits_one_batch_event(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-happy")
    key = _unique_key("bulk_happy")
    _create_string_property(api, token, key=key)
    entry_a = _new_entry(api, "Bulk HTTP entry A")
    entry_b = _new_entry(api, "Bulk HTTP entry B")
    before = _audit_event_count(api)
    # Captured before the POST: `entry_a`/`entry_b` are the same identity-
    # mapped ORM instances the route's own session mutates, so their
    # `row_version` already reflects the post-write value once the call
    # returns (see the singular route's own happy-path test above).
    starting_version_a = entry_a.row_version
    starting_version_b = entry_b.row_version

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[
            {"business_key": entry_a.business_key, "expected_row_version": starting_version_a},
            {"business_key": entry_b.business_key, "expected_row_version": starting_version_b},
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == 2
    assert body["unchanged"] == 0
    assert body["conflict"] == 0
    assert body["not_found"] == 0
    outcomes = {outcome["business_key"]: outcome for outcome in body["outcomes"]}
    assert outcomes[entry_a.business_key]["status"] == "applied"
    assert outcomes[entry_a.business_key]["row_version"] == starting_version_a + 1
    assert outcomes[entry_b.business_key]["status"] == "applied"
    assert _property_value_count(api, entry_id=entry_a.id, property_key=key) == 1
    assert _property_value_count(api, entry_id=entry_b.id, property_key=key) == 1
    # Two per-entry events plus one batch header.
    assert _audit_event_count(api) == before + 3
    bulk_event = latest_audit_event(
        api.session, entity_type=_PROPERTY_VALUE_BULK_ENTITY_TYPE, entity_id=key
    )
    assert bulk_event.action == "property_value.bulk_set"
    assert bulk_event.before is None
    assert bulk_event.after is None


@pytest.mark.req("FR-39")
@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_bulk_save_a_stale_entry_is_a_conflict_outcome_in_a_200_not_a_409(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-bulk-conflict")
    key = _unique_key("bulk_conflict")
    _create_string_property(api, token, key=key)
    entry_stale = _new_entry(api, "Bulk HTTP stale entry")
    entry_fresh = _new_entry(api, "Bulk HTTP fresh entry")
    stale_version = entry_stale.row_version
    first = _put_values(
        api,
        token,
        business_key=entry_stale.business_key,
        property_key=key,
        values=[{"value": "already-set"}],
        expected_row_version=stale_version,
    )
    assert first.status_code == 200, first.text

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[
            {"business_key": entry_stale.business_key, "expected_row_version": stale_version},
            {
                "business_key": entry_fresh.business_key,
                "expected_row_version": entry_fresh.row_version,
            },
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    outcomes = {outcome["business_key"]: outcome for outcome in body["outcomes"]}
    stale_outcome = outcomes[entry_stale.business_key]
    assert stale_outcome["status"] == "conflict"
    assert stale_outcome["conflict"]["current_row_version"] == first.json()["row_version"]
    assert outcomes[entry_fresh.business_key]["status"] == "applied"


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_save_a_missing_business_key_is_a_not_found_outcome(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-not-found")
    key = _unique_key("bulk_not_found")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api, "Bulk HTTP present entry")

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[
            {"business_key": "NPTC-999998", "expected_row_version": 1},
            {"business_key": entry.business_key, "expected_row_version": entry.row_version},
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    outcomes = {outcome["business_key"]: outcome for outcome in body["outcomes"]}
    assert outcomes["NPTC-999998"]["status"] == "not-found"
    assert outcomes["NPTC-999998"]["row_version"] is None
    assert outcomes[entry.business_key]["status"] == "applied"


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_save_a_malformed_business_key_is_422_not_a_not_found_outcome(
    api: ApiTestApp,
) -> None:
    """`business_key` shape is derivable from the request alone, so it is a
    whole-request 422 (matching the singular route's own `BusinessKeyPath`
    422 on a malformed path segment) - never a `not-found` outcome, which
    would make a typo indistinguishable from a well-formed key that simply
    does not exist."""
    token = _admin_token(api, subject="sub-bulk-malformed-key")
    key = _unique_key("bulk_malformed_key")
    _create_string_property(api, token, key=key)

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[{"business_key": "not-a-business-key", "expected_row_version": 1}],
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_save_unknown_property_key_is_404(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-404")
    entry = _new_entry(api, "Bulk HTTP 404 entry")

    response = _post_bulk_values(
        api,
        token,
        property_key="no_such_property_key",
        values=[{"value": "bulk value"}],
        entries=[{"business_key": entry.business_key, "expected_row_version": entry.row_version}],
    )

    assert response.status_code == 404, response.text


@pytest.mark.req("FR-37")
@pytest.mark.integration
def test_bulk_save_with_no_reason_is_422_before_touching_any_entry(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-no-reason")
    key = _unique_key("bulk_no_reason")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api, "Bulk HTTP no-reason entry")
    before = _audit_event_count(api)

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[{"business_key": entry.business_key, "expected_row_version": entry.row_version}],
        reason="",
    )

    assert response.status_code == 422, response.text
    assert _property_value_count(api, entry_id=entry.id, property_key=key) == 0
    assert _audit_event_count(api) == before


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_save_duplicate_business_key_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-duplicate")
    key = _unique_key("bulk_duplicate")
    _create_string_property(api, token, key=key)
    entry = _new_entry(api, "Bulk HTTP duplicate entry")

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[
            {"business_key": entry.business_key, "expected_row_version": entry.row_version},
            {"business_key": entry.business_key, "expected_row_version": entry.row_version},
        ],
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_save_more_than_the_batch_cap_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-cap")
    key = _unique_key("bulk_cap")
    _create_string_property(api, token, key=key)

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "bulk value"}],
        entries=[{"business_key": f"NPTC-{n:06d}", "expected_row_version": 1} for n in range(101)],
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_save_a_schema_violation_writes_nothing_for_any_entry(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-schema")
    key = _unique_key("bulk_schema")
    _create_string_property(api, token, key=key, max_length=3)
    entry_a = _new_entry(api, "Bulk HTTP schema A")
    entry_b = _new_entry(api, "Bulk HTTP schema B")
    before = _audit_event_count(api)

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "this value is far too long"}],
        entries=[
            {"business_key": entry_a.business_key, "expected_row_version": entry_a.row_version},
            {"business_key": entry_b.business_key, "expected_row_version": entry_b.row_version},
        ],
    )

    assert response.status_code == 422, response.text
    assert _property_value_count(api, entry_id=entry_a.id, property_key=key) == 0
    assert _property_value_count(api, entry_id=entry_b.id, property_key=key) == 0
    assert _audit_event_count(api) == before


@pytest.mark.req("FR-89")
@pytest.mark.req("FR-39")
@pytest.mark.integration
def test_bulk_specimen_conflict_rolls_back_an_earlier_entry_already_applied_in_the_same_request(
    api: ApiTestApp,
) -> None:
    """The request-granularity half of "a batch that fails partway leaves
    no entry half-applied" (ADR-0035): unlike a per-entry conflict (caught
    by that entry's own savepoint, the rest of the batch still commits),
    FR-89's specimen check aborts the whole request - `get_session`'s own
    `session_scope` rolls back everything, including `entry_ok`'s write,
    which reached (and committed) its own per-entry savepoint *before* the
    loop ever reached the entry that triggers the abort. The service-layer
    equivalent of this test cannot prove this half on its own - it never
    goes through `session_scope`, only through this route."""
    from nptc.db.bootstrap import seed_system_properties

    token = _admin_token(api, subject="sub-bulk-specimen-abort")
    key = _unique_key("bulk_specimen_abort")
    _create_string_property(api, token, key=key)
    entry_ok = _new_entry(api, "Bulk HTTP specimen ok")
    entry_unconstrained = _new_entry(api, "Bulk HTTP specimen unconstrained")
    seed_system_properties(api.session)
    api.session.flush()
    api.terminology.seed_validate_code(
        "specimen-1",
        ValidationResult(code="specimen-1", result=True),
        value_set_url=_SPECIMEN_VALUE_SET_URI,
        edition=_SPECIMEN_EDITION,
    )
    patch_unconstrained = api.request(
        "PATCH",
        f"/catalogue/entries/{entry_unconstrained.business_key}",
        token=token,
        json={
            "specimen_unconstrained": True,
            "reason": _REASON,
            "expected_row_version": entry_unconstrained.row_version,
        },
    )
    assert patch_unconstrained.status_code == 200, patch_unconstrained.text
    before = _audit_event_count(api)

    response = _post_bulk_values(
        api,
        token,
        property_key="specimen",
        values=[{"value": {"system": _SPECIMEN_SYSTEM, "code": "specimen-1"}}],
        entries=[
            {"business_key": entry_ok.business_key, "expected_row_version": entry_ok.row_version},
            {
                "business_key": entry_unconstrained.business_key,
                "expected_row_version": patch_unconstrained.json()["row_version"],
            },
        ],
    )

    assert response.status_code == 422, response.text
    assert any(
        issue["code"] == "specimen-unconstrained-conflict" for issue in response.json()["issues"]
    )
    # entry_ok's own write reached and committed its per-entry savepoint
    # before the loop reached entry_unconstrained - proving this requires
    # a fresh read, since api.session's own identity map would otherwise
    # show the pre-rollback in-memory state.
    api.session.expire_all()
    assert _property_value_count(api, entry_id=entry_ok.id, property_key="specimen") == 0
    assert _audit_event_count(api) == before


# --- authorisation (FR-44, NFR-06) ------------------------------------------


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_bulk_save_no_credential_is_401(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-bulk-setup-401")
    key = _unique_key("bulk_no_cred")
    _create_string_property(api, admin_token, key=key)
    entry = _new_entry(api, "Bulk HTTP 401 entry")

    response = _post_bulk_values(
        api,
        None,
        property_key=key,
        values=[{"value": "a value"}],
        entries=[{"business_key": entry.business_key, "expected_row_version": entry.row_version}],
    )

    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_bulk_save_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-bulk-setup-403")
    key = _unique_key("bulk_no_permission")
    _create_string_property(api, admin_token, key=key)
    entry = _new_entry(api, "Bulk HTTP 403 entry")
    token = api.token(subject="sub-bulk-no-permission")

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "a value"}],
        entries=[{"business_key": entry.business_key, "expected_row_version": entry.row_version}],
    )

    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_bulk_save_administrator_without_mfa_gets_step_up_challenge(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-bulk-no-mfa", with_mfa=False)
    key = _unique_key("bulk_no_mfa")
    admin_token = _admin_token(api, subject="sub-bulk-no-mfa-setup")
    _create_string_property(api, admin_token, key=key)
    entry = _new_entry(api, "Bulk HTTP no-mfa entry")

    response = _post_bulk_values(
        api,
        token,
        property_key=key,
        values=[{"value": "a value"}],
        entries=[{"business_key": entry.business_key, "expected_row_version": entry.row_version}],
    )

    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]
