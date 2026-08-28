"""The PropertyDefinition admin router (issue #55, FR-09, FR-11, FR-12).

Follows `routers/catalogue_bindings.py`'s house style exactly: the
return-type annotation drives the response model (never `response_model=`
on the decorator), `ConfigDict(frozen=True)` on every request/response
model, module-level `Final` `_RESPONSES` dicts naming only the statuses a
route can actually produce (issue #223 review finding 10: each route gets
its own precise dict, not a shared one that advertises a status it cannot
return), and no try/except in a route body - every domain exception
carries `http_status` and is mapped centrally by `nptc.api.errors`.

**Every mutating route (`POST`/`PATCH`/`POST .../deprecation`/`DELETE`) is
gated on `Permission.REGISTRY_MANAGE`** (FR-44) - never a role name. **Both
`GET` routes are gated on `Permission.REGISTRY_READ`** instead (issue #223
review round-1 finding 6, corrected in round 2 per ADR-0028):
`REGISTRY_MANAGE` is administrator-tier, and gating a read route on it made
`DefinitionAudience.DATA_ENTRY` unreachable by the very audience it is
named for - a member filling in a submission form could not call `GET
/registry/properties` to learn which properties to offer. Gating on
`Permission.CATALOGUE_BROWSE` instead (round 1's fix) over-corrected: that
permission is held by `Role.ANON` too, so the registry ended up fully
public. `Permission.REGISTRY_READ` is the member-tier permission ADR-0028
introduces for exactly this gap - held from `Role.MEMBER` up, never by
`Role.ANON`, `Role.OBSERVER` or `Role.PROVISIONAL`.

**`DELETE` never deletes.** `delete_property_definition` always raises
`PropertyDefinitionDeleteRefusedError` (409) - `property_definition` has no
`DELETE` grant at the database layer at all (issue #51); this route exists
so a client's `DELETE` gets an actionable 409 naming deprecation as the
available action, rather than an unhandled `42501` surfacing as a 500.

**`GET /registry/properties?include_deprecated=` resolves the audience.**
`false` (the default) is the data-entry audience - active properties only,
what a submission/maintenance form should offer. `true` is the export
audience - every status, including deprecated, since a historical value
recorded against a since-deprecated property must still resolve through a
listing that includes it (issue #55's export-resolver-only scope decision;
see the plan comment on issue #55 for why the byte-level "re-generated
historical export" half is out of scope here).

**`PATCH` carries no `key` field at all** (FR-12) - `AmendPropertyDefinitionRequest`
is `ConfigDict(frozen=True, extra="forbid")`, so a request body naming `key`
is refused with a pydantic 422 before `nptc.db.definitions.amend_definition`
is ever called - that function's own `PropertyKeyImmutableError` is the
belt-and-braces layer for a caller of the service function directly.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    AuditContextDep,
    get_datatype_registry,
    get_session,
    permission_dep,
)
from nptc.api.routers.auth import ErrorResponse
from nptc.auth.permissions import Permission
from nptc.db.definitions import (
    amend_definition,
    create_definition,
    deprecate_definition,
    list_definitions,
    load_definition,
)
from nptc.db.models.property_definition import (
    BindingStrength,
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyScope,
)
from nptc.registry.definitions import DefinitionAudience, PropertyDefinitionDeleteRefusedError
from nptc.registry.handlers import DatatypeRegistry

router = APIRouter(prefix="/registry", tags=["registry"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403_READ: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The caller is authenticated but does not hold `registry.read`.",
}
_RESPONSE_403_MANAGE: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The caller is authenticated but does not hold `registry.manage`.",
}
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No property definition matches the given key.",
}
_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The request conflicts with the current state of the system - a duplicate `key`, "
        "an attempt to change `key`, a stale `expected_row_version`, a second deprecation, "
        "deprecating a system property, or (on `DELETE`) any request at all."
    ),
}
#: Round-2 review, minor finding 2: a route below can produce *two*
#: genuinely different 422 body shapes, not one - a typed domain error
#: (`ErrorResponse`, `{"detail": "<string>"}`, e.g.
#: `PropertyDatatypeUnknownError`/`PropertyConstraintsInvalidError`) and a
#: pydantic validation failure that never reaches the route body at all
#: (FastAPI's own `HTTPValidationError`,
#: `{"detail": [{"loc": ..., "msg": ...}, ...]}` - the explicit-`null` and
#: invalid-enum-value paths both exercise this one). The previous
#: `"model": ErrorResponse` only documented the first, silently suppressing
#: FastAPI's automatic `HTTPValidationError` entry it would otherwise add
#: for any route with a request body. Declared here as `anyOf` over both
#: component schemas, referenced the same way FastAPI names them
#: (`ErrorResponse`, `HTTPValidationError`) rather than overriding one away.
_RESPONSE_422: Final[dict[str, Any]] = {
    "description": (
        "A field failed validation, the request body named a `key` field, or an explicit "
        "`null` was given for a field that is otherwise omittable. Two distinct body shapes "
        "occur here: a typed domain error (`ErrorResponse`) or a pydantic validation failure "
        "(FastAPI's own `HTTPValidationError`)."
    ),
    "content": {
        "application/json": {
            "schema": {
                "anyOf": [
                    {"$ref": "#/components/schemas/ErrorResponse"},
                    {"$ref": "#/components/schemas/HTTPValidationError"},
                ]
            }
        }
    },
}

#: Issue #223 review finding 10: each route below gets its own dict naming
#: only the statuses it can actually produce - no route reuses a shared
#: dict that advertises a status it cannot return any more.
_RESPONSES_LIST: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_READ,
}
_RESPONSES_GET_ONE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_READ,
    404: _RESPONSE_404,
}
_RESPONSES_CREATE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}
_RESPONSES_PATCH: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}
_RESPONSES_DEPRECATE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}
_RESPONSES_DELETE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    409: _RESPONSE_409,
}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_MANAGE = Depends(permission_dep(Permission.REGISTRY_MANAGE))
_READ = Depends(permission_dep(Permission.REGISTRY_READ))


class PropertyDefinitionResponse(BaseModel):
    """One `property_definition` row, on the wire. No internal id (NFR-04) -
    `key` is the one public identifier this resource is ever addressed by."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    datatype: str
    cardinality: str
    scope: str
    required_for_submission: bool
    required_for_publication: bool
    binding_target: str | None
    value_set_uri: str | None
    strength: str | None
    edition: str | None
    local_code_system_key: str | None
    filterable: bool
    origin: str
    status: str
    display_order: int
    constraints: dict[str, Any]
    row_version: int


class PropertyDefinitionList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[PropertyDefinitionResponse]


class CreatePropertyDefinitionRequest(BaseModel):
    """The body of `POST /registry/properties`. Every property created
    through this route is `origin = 'admin'` - there is no field to
    request otherwise; the four `origin = 'system'` rows are seeded once,
    at bootstrap, and never created through this API.

    `cardinality`/`scope`/`strength`/`binding_target` are typed against the
    exact `StrEnum`s `property_definition`'s own database `CHECK`
    constraints close over (issue #223 review finding 3) - an invalid value
    is now a pydantic 422 before the request ever reaches the ORM, rather
    than a `23514` `IntegrityError` that `create_definition`'s `except
    IntegrityError` used to re-raise unchanged, surfacing as an unhandled
    500. `datatype` stays a bare `str` deliberately - FR-77's own extension
    point, so admitting a new datatype never touches this router - and is
    instead validated by `create_definition` itself, against the live
    `DatatypeRegistry`, where `UnknownDatatypeError` becomes a typed 422
    rather than a broken row that only misbehaves at the first value
    write."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    datatype: str
    cardinality: PropertyCardinality
    scope: PropertyScope
    required_for_submission: bool = False
    required_for_publication: bool = False
    filterable: bool = False
    display_order: int = 0
    binding_target: BindingTarget | None = None
    value_set_uri: str | None = None
    strength: BindingStrength | None = None
    edition: str | None = None
    local_code_system_key: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    reason: str


class AmendPropertyDefinitionRequest(BaseModel):
    """The body of `PATCH /registry/properties/{key}`. Deliberately carries
    no `key` field (FR-12) and forbids any extra field a client might try to
    smuggle one in as - `extra="forbid"` turns a `key` in the body into a
    pydantic 422 before this ever reaches `nptc.db.definitions.
    amend_definition`.

    **An explicit `null` on a known field is refused, not a silent no-op**
    (issue #223 review finding 9). None of these fields is a nullable
    domain value, so a client sending `{"label": null, ...}` almost
    certainly meant to omit the field, not clear it - `_reject_explicit_null`
    below distinguishes "omitted" from "provided as null" via
    `model_fields_set`, which `changes()` cannot do once every field has
    collapsed to `None`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str | None = None
    required_for_submission: bool | None = None
    required_for_publication: bool | None = None
    filterable: bool | None = None
    display_order: int | None = None
    constraints: dict[str, Any] | None = None
    expected_row_version: int
    reason: str

    @model_validator(mode="after")
    def _reject_explicit_null(self) -> AmendPropertyDefinitionRequest:
        null_fields = sorted(
            name
            for name in self.model_fields_set
            if name not in {"expected_row_version", "reason"} and getattr(self, name) is None
        )
        if null_fields:
            raise ValueError(
                "the following fields were explicitly set to null, which is not a valid "
                f"value for any of them - omit a field instead of nulling it: {null_fields}"
            )
        return self

    def changes(self) -> dict[str, Any]:
        return {
            name: value
            for name, value in (
                ("label", self.label),
                ("required_for_submission", self.required_for_submission),
                ("required_for_publication", self.required_for_publication),
                ("filterable", self.filterable),
                ("display_order", self.display_order),
                ("constraints", self.constraints),
            )
            if value is not None
        }


class DeprecatePropertyDefinitionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_row_version: int
    reason: str


def _to_response(definition: PropertyDefinition) -> PropertyDefinitionResponse:
    return PropertyDefinitionResponse(
        key=definition.key,
        label=definition.label,
        datatype=definition.datatype,
        cardinality=definition.cardinality,
        scope=definition.scope,
        required_for_submission=definition.required_for_submission,
        required_for_publication=definition.required_for_publication,
        binding_target=definition.binding_target,
        value_set_uri=definition.value_set_uri,
        strength=definition.strength,
        edition=definition.edition,
        local_code_system_key=definition.local_code_system_key,
        filterable=definition.filterable,
        origin=definition.origin,
        status=definition.status,
        display_order=definition.display_order,
        constraints=dict(definition.constraints),
        row_version=definition.row_version,
    )


@router.get(
    "/properties",
    summary="List property definitions",
    responses=_RESPONSES_LIST,
    dependencies=[_READ],
)
def list_properties(
    session: SessionDep,
    include_deprecated: bool = False,
) -> PropertyDefinitionList:
    audience = DefinitionAudience.EXPORT if include_deprecated else DefinitionAudience.DATA_ENTRY
    definitions = list_definitions(session, audience=audience)
    return PropertyDefinitionList(items=[_to_response(d) for d in definitions])


@router.get(
    "/properties/{key}",
    summary="Get one property definition",
    responses=_RESPONSES_GET_ONE,
    dependencies=[_READ],
)
def get_property(session: SessionDep, key: str) -> PropertyDefinitionResponse:
    definition = load_definition(session, key)
    return _to_response(definition)


@router.post(
    "/properties",
    summary="Create a property definition",
    status_code=201,
    responses=_RESPONSES_CREATE,
    dependencies=[_MANAGE],
)
def create_property(
    session: SessionDep,
    ctx: AuditContextDep,
    registry: RegistryDep,
    body: Annotated[CreatePropertyDefinitionRequest, Body()],
) -> PropertyDefinitionResponse:
    definition = create_definition(
        session,
        ctx,
        registry=registry,
        key=body.key,
        label=body.label,
        datatype=body.datatype,
        cardinality=body.cardinality,
        scope=body.scope,
        required_for_submission=body.required_for_submission,
        required_for_publication=body.required_for_publication,
        binding_target=body.binding_target,
        value_set_uri=body.value_set_uri,
        strength=body.strength,
        edition=body.edition,
        local_code_system_key=body.local_code_system_key,
        filterable=body.filterable,
        display_order=body.display_order,
        constraints=body.constraints,
        reason=body.reason,
    )
    session.flush()
    return _to_response(definition)


@router.patch(
    "/properties/{key}",
    summary="Amend a property definition",
    responses=_RESPONSES_PATCH,
    dependencies=[_MANAGE],
)
def amend_property(
    session: SessionDep,
    ctx: AuditContextDep,
    registry: RegistryDep,
    key: str,
    body: Annotated[AmendPropertyDefinitionRequest, Body()],
) -> PropertyDefinitionResponse:
    definition = load_definition(session, key)
    amended = amend_definition(
        session,
        ctx,
        registry=registry,
        definition=definition,
        expected_row_version=body.expected_row_version,
        reason=body.reason,
        **body.changes(),
    )
    session.flush()
    return _to_response(amended)


@router.post(
    "/properties/{key}/deprecation",
    summary="Deprecate a property definition",
    responses=_RESPONSES_DEPRECATE,
    dependencies=[_MANAGE],
)
def deprecate_property(
    session: SessionDep,
    ctx: AuditContextDep,
    key: str,
    body: Annotated[DeprecatePropertyDefinitionRequest, Body()],
) -> PropertyDefinitionResponse:
    definition = load_definition(session, key)
    deprecated = deprecate_definition(
        session,
        ctx,
        definition=definition,
        expected_row_version=body.expected_row_version,
        reason=body.reason,
    )
    session.flush()
    return _to_response(deprecated)


@router.delete(
    "/properties/{key}",
    summary="Delete a property definition (always refused)",
    status_code=409,
    responses=_RESPONSES_DELETE,
    dependencies=[_MANAGE],
)
def delete_property(key: str) -> None:
    """Always refuses (FR-11) - `property_definition` has no `DELETE` grant
    at the database layer at all (issue #51). `key` is accepted (and, for a
    genuinely unknown key, still refused the same way, not 404'd) so the
    response is uniform regardless of whether the key exists - the caller's
    mistake either way is asking to delete at all, not naming the wrong
    key."""
    raise PropertyDefinitionDeleteRefusedError(
        f"property_definition {key!r} cannot be deleted; deprecate it instead (FR-11)"
    )
