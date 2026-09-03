"""HTTP tests for `nptc.api.routers.registry` (issue #55, FR-11, FR-12,
FR-38, FR-44, NFR-08).

Follows `test_api_catalogue_bindings.py`'s own precedent exactly: the
service layer already has its own unit tests
(`test_registry_definitions.py`); this module proves the HTTP adapter -
request/response shape, status codes, the exception-handler mapping in
`nptc.api.errors`, and authorisation - against the real `create_app()`.
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
from nptc.auth.grants import grant_role_unchecked, revoke_all_roles_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue.entries import create_entry
from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity

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

_REASON = "Created for issue #55 registry API test."


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


def _role_token(api: ApiTestApp, *, subject: str, role: Role) -> str:
    """Resolves `subject` to exactly `role` - no more, no less.

    `_create_user` (`nptc.auth.identity`) auto-grants every brand-new
    identity `Role.PROVISIONAL` on first sign-in, and roles are additive
    (`roles_for_user` returns the union of every grant, and permissions are
    the union over that set) - so granting a role on top of a fresh
    identity does not isolate that role's own permissions, it only adds to
    Provisional's. `revoke_all_roles_unchecked` clears that default grant
    first so the token in hand reflects `role` alone."""
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    revoke_all_roles_unchecked(api.session, target_user_id=user.id, audit=AuditContext.system())
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=role,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    return api.token(subject=subject)


def _unique_key(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _create_body(key: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "key": key,
        "label": key.replace("_", " ").title(),
        "datatype": "string",
        "cardinality": "0..1",
        "scope": "both",
        "display_order": 0,
        "reason": _REASON,
    }
    body.update(overrides)
    return body


def _create(api: ApiTestApp, token: str, key: str, **overrides: object) -> Any:
    return api.post("/registry/properties", token=token, json=_create_body(key, **overrides))


# --- happy paths -----------------------------------------------------------


@pytest.mark.req("FR-11")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_create_property_returns_201_and_records_one_audit_event(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-create-happy")
    before = _audit_event_count(api)
    key = _unique_key("create_happy")

    response = _create(api, token, key)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["key"] == key
    assert body["status"] == "active"
    assert body["origin"] == "admin"
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_patch_property_changes_label_key_unchanged(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-happy")
    key = _unique_key("patch_happy")
    created = _create(api, token, key).json()

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={
            "expected_row_version": created["row_version"],
            "reason": _REASON,
            "label": "A brand new label",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == key
    assert body["label"] == "A brand new label"


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_patch_property_with_a_key_field_in_the_body_is_422(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-key-forbidden")
    key = _unique_key("patch_key_forbidden")
    created = _create(api, token, key).json()

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={
            "expected_row_version": created["row_version"],
            "reason": _REASON,
            "key": "some_other_key",
        },
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_patch_property_with_a_stale_row_version_is_409(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-stale")
    key = _unique_key("patch_stale")
    created = _create(api, token, key).json()

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={
            "expected_row_version": created["row_version"] + 1,
            "reason": _REASON,
            "label": "Should not apply",
        },
    )

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_patch_property_with_an_explicit_null_is_422(api: ApiTestApp) -> None:
    """Issue #223 review finding 9: an explicit `null` on a known field is
    refused, not a silent no-op - `AmendPropertyDefinitionRequest` treats
    "field omitted" and "field explicitly set to null" as different
    things, and none of these fields is a nullable domain value."""
    token = _admin_token(api, subject="sub-patch-explicit-null")
    key = _unique_key("patch_explicit_null")
    created = _create(api, token, key).json()

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={
            "expected_row_version": created["row_version"],
            "reason": _REASON,
            "label": None,
        },
    )

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_deprecate_property_with_a_stale_row_version_is_409(api: ApiTestApp) -> None:
    """Issue #223 review finding 7: `deprecate_property`'s stale-
    `expected_row_version` branch had no HTTP-layer test, mirroring
    `test_patch_property_with_a_stale_row_version_is_409` above."""
    token = _admin_token(api, subject="sub-deprecate-stale")
    key = _unique_key("deprecate_stale")
    created = _create(api, token, key).json()

    response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"] + 1, "reason": _REASON},
    )

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_create_property_with_an_unknown_datatype_is_422(api: ApiTestApp) -> None:
    """Issue #223 review finding 3: `datatype` has no database `CHECK` at
    all (FR-77's own extension point), so an unrecognised value must be
    refused with a 422 before the row is ever written, not a `201`
    followed by a broken row."""
    token = _admin_token(api, subject="sub-create-bad-datatype")
    response = _create(api, token, _unique_key("bad_datatype"), datatype="banana")
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_create_property_with_an_invalid_cardinality_is_422(api: ApiTestApp) -> None:
    """Issue #223 review finding 3: `cardinality` is typed against the
    exact `CHECK`-backed enum, so an invalid value is a pydantic 422, not
    the `23514` `IntegrityError` `create_definition` used to re-raise
    unchanged as an unhandled 500."""
    token = _admin_token(api, subject="sub-create-bad-cardinality")
    response = _create(api, token, _unique_key("bad_cardinality"), cardinality="0..99")
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_create_property_with_constraints_invalid_for_the_datatype_is_422(
    api: ApiTestApp,
) -> None:
    """Issue #223 review finding 4: `constraints` is validated against the
    resolved datatype handler's own `constraints_schema()` at create time."""
    token = _admin_token(api, subject="sub-create-bad-constraints")
    response = _create(
        api,
        token,
        _unique_key("bad_constraints"),
        constraints={"maxLength": "not-an-integer"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-11")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_deprecate_property_returns_200_and_records_one_audit_event(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-deprecate-happy")
    key = _unique_key("deprecate_happy")
    created = _create(api, token, key).json()
    before = _audit_event_count(api)

    response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "deprecated"
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_list_properties_default_excludes_deprecated_include_deprecated_shows_it(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-list-audience")
    key = _unique_key("list_audience")
    created = _create(api, token, key).json()
    api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )

    default_response = api.get("/registry/properties", token=token)
    assert default_response.status_code == 200, default_response.text
    default_keys = {item["key"] for item in default_response.json()["items"]}
    assert key not in default_keys

    export_response = api.get(
        "/registry/properties", token=token, params={"include_deprecated": "true"}
    )
    assert export_response.status_code == 200, export_response.text
    export_keys = {item["key"] for item in export_response.json()["items"]}
    assert key in export_keys


# --- form_control (issue #248, FR-77) -------------------------------------


@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_get_property_returns_a_form_control(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-form-control")
    key = _unique_key("form_control")
    _create(api, token, key)

    response = api.get(f"/registry/properties/{key}", token=token)

    assert response.status_code == 200, response.text
    form_control = response.json()["form_control"]
    assert form_control == {"control": "text", "params": {}}


@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_list_properties_returns_a_synthetic_datatypes_own_form_control(api: ApiTestApp) -> None:
    """FR-77's own acceptance criterion, at the HTTP layer: a datatype
    registered nowhere in `nptc.registry.datatypes.BUILTIN_DATATYPES`
    still appears in `GET /registry/properties` with its own handler's
    `form_control` - no route or response model in `nptc.api.routers.
    registry` is edited to make this work, matching `test_synthetic_
    datatype.py`'s own service-layer proof of the same claim."""
    from nptc.api.dependencies import get_datatype_registry
    from nptc.registry import (
        ControlKind,
        DatatypeRegistry,
        FormControlDescriptor,
        HandlerDeps,
        PropertyDefinitionSpec,
        SerialisationTarget,
        ValidationIssue,
        build_builtin_handlers,
    )
    from nptc_shared.terminology.stub import StubTerminologyClient

    class _SyntheticColourHandler:
        """A wholly synthetic datatype - never one of PRD SS6.5's five and
        never registered as a builtin - existing only inside this test, to
        prove FR-77's extensibility claim without pre-registering a
        speculative real one (mirrors `test_synthetic_datatype.py`'s own
        `DurationHandler`, trimmed to what this route-level test needs)."""

        datatype = "synthetic_colour"

        def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> dict[str, object]:
            return {"type": "string"}

        def constraints_schema(self) -> dict[str, object]:
            return {"type": "object", "additionalProperties": False}

        def validate(self, value: object, spec: PropertyDefinitionSpec) -> list[ValidationIssue]:
            return []

        def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
            return FormControlDescriptor(
                control=ControlKind.CONCEPT_PICKER, params={"palette": "swatch"}
            )

        def serialise(self, value: object, target: SerialisationTarget) -> object:
            return value

        def index_shape(self, spec: PropertyDefinitionSpec) -> None:
            return None

        def supported_filter_ops(self) -> frozenset[object]:
            return frozenset()

        def filter_clause(self, op: object, value: object, column: object) -> object:
            raise AssertionError("not exercised by this test")

        def facet_expression(self, column: object) -> None:
            return None

    registry_with_synthetic = DatatypeRegistry(
        [
            *build_builtin_handlers(HandlerDeps(terminology_client=StubTerminologyClient())),
            _SyntheticColourHandler(),
        ]
    )
    api.app.dependency_overrides[get_datatype_registry] = lambda: registry_with_synthetic
    try:
        token = _admin_token(api, subject="sub-synthetic-datatype")
        key = _unique_key("synthetic_colour")
        response = _create(api, token, key, datatype="synthetic_colour")
        assert response.status_code == 201, response.text

        list_response = api.get("/registry/properties", token=token)
        assert list_response.status_code == 200, list_response.text
        items = {item["key"]: item for item in list_response.json()["items"]}
        assert items[key]["form_control"] == {
            "control": "concept_picker",
            "params": {"palette": "swatch"},
        }
    finally:
        del api.app.dependency_overrides[get_datatype_registry]


@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_get_property_returns_a_code_datatypes_form_control_from_its_binding(
    api: ApiTestApp,
) -> None:
    """`string`'s `form_control` (`test_get_property_returns_a_form_control`
    above) has empty `params` - the only shape the two existing tests cover.
    `code`'s `form_control` is the one #151 actually consumes
    (`valueSetUri`/`strength`/`edition`/`allowJustification`,
    `registry/datatypes/code.py::CodeHandler.form_control`), derived from
    the definition's own binding rather than a literal dict, so a synthetic
    handler cannot stand in for it - `specimen`
    (`nptc.db.bootstrap.seed_system_properties`) is a real `code` property
    with a real `value_set` binding, already used two tests over in
    `test_api_catalogue_properties.py`."""
    from nptc.db.bootstrap import seed_system_properties

    token = _admin_token(api, subject="sub-form-control-code")
    seed_system_properties(api.session)
    api.session.flush()

    response = api.get("/registry/properties/specimen", token=token)

    assert response.status_code == 200, response.text
    assert response.json()["form_control"] == {
        "control": "concept_picker",
        "params": {
            "valueSetUri": "http://snomed.info/sct?fhir_vs=ecl/%3C123038009",
            "strength": "required",
            "edition": "au",
            "allowJustification": False,
        },
    }


@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_list_properties_with_one_drifted_datatype_is_a_whole_list_500(
    api: ApiTestApp,
) -> None:
    """Round-2 review: `_to_response` resolves `registry.get(definition.
    datatype)` per definition with no per-item tolerance, so one definition
    row whose `datatype` no longer matches a registered handler (a stored
    row surviving a handler's removal - most plausible for a deprecated
    property, reached only via `?include_deprecated=true`) fails the whole
    `GET /registry/properties` response, not just that one item. Fail-loud
    may well be the right call under FR-16 (silently omitting a drifted
    property from an administrator's own registry listing hides exactly
    the drift they need to see), but nothing pinned it before this test -
    inserted directly via the ORM, bypassing `create_definition`'s own
    registry validation, since that validation is precisely what makes this
    row impossible to create through the API.

    Asserts `detail` against `_handle_unknown_datatype`'s own constant
    (round-2 review), not just the status code - a bare `500` would stay
    green for any unrelated server error the route happened to raise,
    including one that stopped exercising the drift this test exists to
    pin."""
    from nptc.api.errors import _DETAIL_SERVER_MISCONFIGURED
    from nptc.db.models.property_definition import PropertyDefinition

    token = _admin_token(api, subject="sub-list-drifted-datatype")
    key = _unique_key("drifted_datatype")
    api.session.add(
        PropertyDefinition(
            key=key,
            label="Drifted Datatype",
            datatype="no_longer_a_registered_datatype",
            cardinality="0..1",
            scope="both",
            required_for_submission=False,
            required_for_publication=False,
            filterable=False,
            origin="admin",
            display_order=0,
            constraints={},
        )
    )
    api.session.flush()

    response = api.get("/registry/properties", token=token)

    assert response.status_code == 500, response.text
    assert response.json()["detail"] == _DETAIL_SERVER_MISCONFIGURED


# --- scope filter (issue #248, re-adding issue #223 review finding 8) -----


@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_list_properties_scope_filter_is_inclusive_of_both(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-scope-filter")
    submission_key = _unique_key("scope_submission")
    maintenance_key = _unique_key("scope_maintenance")
    both_key = _unique_key("scope_both")
    _create(api, token, submission_key, scope="submission")
    _create(api, token, maintenance_key, scope="maintenance")
    _create(api, token, both_key, scope="both")

    submission_response = api.get(
        "/registry/properties", token=token, params={"scope": "submission"}
    )
    assert submission_response.status_code == 200, submission_response.text
    submission_keys = {item["key"] for item in submission_response.json()["items"]}
    assert submission_key in submission_keys
    assert both_key in submission_keys
    assert maintenance_key not in submission_keys

    maintenance_response = api.get(
        "/registry/properties", token=token, params={"scope": "maintenance"}
    )
    assert maintenance_response.status_code == 200, maintenance_response.text
    maintenance_keys = {item["key"] for item in maintenance_response.json()["items"]}
    assert maintenance_key in maintenance_keys
    assert both_key in maintenance_keys
    assert submission_key not in maintenance_keys

    unfiltered_response = api.get("/registry/properties", token=token)
    assert unfiltered_response.status_code == 200, unfiltered_response.text
    unfiltered_keys = {item["key"] for item in unfiltered_response.json()["items"]}
    assert {submission_key, maintenance_key, both_key} <= unfiltered_keys


# --- domain refusals ---------------------------------------------------


@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_create_property_with_a_duplicate_key_is_409(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-dup-key")
    key = _unique_key("dup_key")
    _create(api, token, key)

    response = _create(api, token, key)

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_deprecate_property_twice_is_409(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-double-deprecate")
    key = _unique_key("double_deprecate")
    created = _create(api, token, key).json()
    api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    refreshed = api.get(f"/registry/properties/{key}", token=token).json()

    response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": refreshed["row_version"], "reason": _REASON},
    )

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_delete_property_is_always_409(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-delete-refused")
    key = _unique_key("delete_refused")
    _create(api, token, key)

    response = api.request("DELETE", f"/registry/properties/{key}", token=token)

    assert response.status_code == 409, response.text
    assert "deprecat" in response.json()["detail"].lower()


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_deprecate_property_system_origin_is_409(api: ApiTestApp) -> None:
    """A built-in system property (`nptc.db.bootstrap.seed_system_properties`)
    must refuse deprecation at the HTTP layer too, not just at the service
    layer (`test_registry_definitions.py::
    test_deprecate_definition_refuses_a_system_property`)."""
    from nptc.db.bootstrap import seed_system_properties
    from nptc.db.definitions import load_definition

    token = _admin_token(api, subject="sub-deprecate-system")
    seed_system_properties(api.session)
    api.session.flush()
    definition = load_definition(api.session, "usage_guidance")

    response = api.post(
        f"/registry/properties/{definition.key}/deprecation",
        token=token,
        json={"expected_row_version": definition.row_version, "reason": _REASON},
    )

    assert response.status_code == 409, response.text


@pytest.mark.req("FR-11")
@pytest.mark.integration
def test_delete_property_unknown_key_is_still_409_not_404(api: ApiTestApp) -> None:
    """`DELETE` always refuses, uniformly, whether or not `key` names a real
    definition - see `delete_property`'s own docstring: the caller's
    mistake either way is asking to delete at all, not naming the wrong
    key, so this is a 409 rather than a 404 that would imply deleting a
    *real* definition might otherwise have worked."""
    token = _admin_token(api, subject="sub-delete-unknown")

    response = api.request("DELETE", "/registry/properties/no_such_property_key", token=token)

    assert response.status_code == 409, response.text


# A dedicated HTTP route for a property value write does not exist yet
# (out of this issue's scope - #151 owns the frontend, and its own write
# route is a follow-up). `test_registry_definitions.py::
# test_save_property_values_refuses_a_write_against_a_deprecated_property`
# is the direct service-layer proof of this guard; the end-to-end test at
# the bottom of this module exercises the same guard from an HTTP-created
# property and definition.


# --- authorisation (FR-44, NFR-06, NFR-20) --------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_list_properties_no_credential_is_401(api: ApiTestApp) -> None:
    """Issue #223 round-2 review, finding 1: `GET /registry/properties` was
    briefly gated on `Permission.CATALOGUE_BROWSE`, which `Role.ANON` also
    holds - making the route fully public, which was never the intent.
    ADR-0028's `Permission.REGISTRY_READ` is member-tier, never held by
    `Role.ANON`, so an anonymous caller is refused with 401 like every
    other authenticated-only route."""
    response = api.get("/registry/properties", token=None)
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_list_properties_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    """See `test_list_properties_no_credential_is_401`'s own docstring.
    `_create_user` auto-grants a brand-new identity `Role.PROVISIONAL`
    (`nptc.auth.identity`), and round-4 review (issue #223) gave
    `Role.PROVISIONAL` `Permission.REGISTRY_READ` too (FR-23 - the
    submission form Provisional can already create is generated from this
    registry), so a bare newly-seen subject is no longer the right
    "lacks `registry.read`" case. `Role.OBSERVER` is: FR-80 keeps it
    entirely read-only/non-contributing, with no submission form to
    generate, so it is the only authenticated role without
    `Permission.REGISTRY_READ` (ADR-0028) - this is a 403, not the 200
    round-1's over-correction produced."""
    token = _role_token(api, subject="sub-list-observer-role", role=Role.OBSERVER)
    response = api.get("/registry/properties", token=token)
    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_create_property_no_credential_is_401(api: ApiTestApp) -> None:
    response = _create(api, token=None, key=_unique_key("no_cred"))
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_create_property_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    token = api.token(subject="sub-create-no-permission")
    response = _create(api, token, _unique_key("no_permission"))
    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_create_property_administrator_without_mfa_gets_step_up_challenge(
    api: ApiTestApp,
) -> None:
    token = _admin_token(api, subject="sub-create-no-mfa", with_mfa=False)
    response = _create(api, token, _unique_key("no_mfa"))
    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_get_property_no_credential_is_401(api: ApiTestApp) -> None:
    """See `test_list_properties_no_credential_is_401`'s own docstring
    (ADR-0028) - `GET /registry/properties/{key}` is gated on the same
    `Permission.REGISTRY_READ`."""
    admin_token = _admin_token(api, subject="sub-get-setup")
    key = _unique_key("get_no_cred")
    _create(api, admin_token, key)

    response = api.get(f"/registry/properties/{key}", token=None)
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_get_property_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    """See `test_list_properties_authenticated_without_permission_is_403`'s
    own docstring - the single-property `GET` route did not previously have
    its own 403 test (round-2 review); it is gated identically."""
    admin_token = _admin_token(api, subject="sub-get-setup-no-permission")
    key = _unique_key("get_no_special_role")
    _create(api, admin_token, key)
    token = _role_token(api, subject="sub-get-observer-role", role=Role.OBSERVER)

    response = api.get(f"/registry/properties/{key}", token=token)
    assert response.status_code == 403, response.text


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_list_properties_provisional_role_is_200(api: ApiTestApp) -> None:
    """Issue #223 round-4 review: FR-23 names Provisional as one of the
    roles able to submit a proposed new test, and the submission form is
    generated from the property registry - so ADR-0028's
    `Permission.REGISTRY_READ` must not exclude `Role.PROVISIONAL`. A
    Provisional principal gets 200, not the 403 an ANON-tier caller gets
    in `test_list_properties_authenticated_without_permission_is_403`."""
    token = _role_token(api, subject="sub-list-provisional", role=Role.PROVISIONAL)
    response = api.get("/registry/properties", token=token)
    assert response.status_code == 200, response.text


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_patch_property_records_one_audit_event(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-audit")
    key = _unique_key("patch_audit")
    created = _create(api, token, key).json()
    before = _audit_event_count(api)

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={
            "expected_row_version": created["row_version"],
            "reason": _REASON,
            "label": "Audited label",
        },
    )

    assert response.status_code == 200, response.text
    assert _audit_event_count(api) == before + 1


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_patch_property_no_credential_is_401(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-patch-setup")
    key = _unique_key("patch_no_cred")
    created = _create(api, token, key).json()

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=None,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_patch_property_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-patch-setup-403")
    key = _unique_key("patch_no_permission")
    created = _create(api, admin_token, key).json()
    token = api.token(subject="sub-patch-no-permission")

    response = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_deprecate_property_no_credential_is_401(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-deprecate-setup")
    key = _unique_key("deprecate_no_cred")
    created = _create(api, admin_token, key).json()

    response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=None,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_deprecate_property_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-deprecate-setup-403")
    key = _unique_key("deprecate_no_permission")
    created = _create(api, admin_token, key).json()
    token = api.token(subject="sub-deprecate-no-permission")

    response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    assert response.status_code == 403, response.text


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_delete_property_no_credential_is_401(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-delete-setup")
    key = _unique_key("delete_no_cred")
    _create(api, admin_token, key)

    response = api.request("DELETE", f"/registry/properties/{key}", token=None)
    assert response.status_code == 401, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_delete_property_authenticated_without_permission_is_403(api: ApiTestApp) -> None:
    admin_token = _admin_token(api, subject="sub-delete-setup-403")
    key = _unique_key("delete_no_permission")
    _create(api, admin_token, key)
    token = api.token(subject="sub-delete-no-permission")

    response = api.request("DELETE", f"/registry/properties/{key}", token=token)
    assert response.status_code == 403, response.text


# --- end-to-end (issue #55's own worked scenario) -------------------------


@pytest.mark.req("FR-11")
@pytest.mark.req("FR-12")
@pytest.mark.integration
def test_end_to_end_create_record_value_deprecate_still_readable(api: ApiTestApp) -> None:
    token = _admin_token(api, subject="sub-e2e")
    key = _unique_key("e2e")
    created = _create(api, token, key).json()
    entry = create_entry(
        api.session, AuditContext.system(), preferred_term="FR-11 e2e entry", reason=_REASON
    )
    api.session.flush()

    from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
    from nptc.catalogue.property_values import PropertyValueInput, save_property_values
    from nptc.registry.datatypes import build_builtin_handlers
    from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
    from nptc_shared.terminology.stub import StubTerminologyClient

    registry = DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=StubTerminologyClient(),
                local_code_lookup=DatabaseLocalCodeLookup(api.session),
            )
        )
    )
    save_property_values(
        api.session,
        AuditContext.system(),
        entry=entry,
        property_key=key,
        values=[PropertyValueInput(value="a recorded value")],
        reason=_REASON,
        registry=registry,
        expected_row_version=entry.row_version,
    )
    api.session.flush()

    deprecate_response = api.post(
        f"/registry/properties/{key}/deprecation",
        token=token,
        json={"expected_row_version": created["row_version"], "reason": _REASON},
    )
    assert deprecate_response.status_code == 200, deprecate_response.text

    # Absent from the data-entry listing, present in the export listing.
    data_entry = api.get("/registry/properties", token=token)
    assert key not in {item["key"] for item in data_entry.json()["items"]}
    export = api.get("/registry/properties", token=token, params={"include_deprecated": "true"})
    assert key in {item["key"] for item in export.json()["items"]}

    # DELETE still refused.
    delete_response = api.request("DELETE", f"/registry/properties/{key}", token=token)
    assert delete_response.status_code == 409, delete_response.text

    # Further value write refused - proven at the service layer directly,
    # since no dedicated HTTP property-value write route exists yet.
    from nptc.registry.definitions import DeprecatedPropertyWriteError

    with pytest.raises(DeprecatedPropertyWriteError):
        save_property_values(
            api.session,
            AuditContext.system(),
            entry=entry,
            property_key=key,
            values=[PropertyValueInput(value="a second value")],
            reason=_REASON,
            registry=registry,
            expected_row_version=entry.row_version,
        )
