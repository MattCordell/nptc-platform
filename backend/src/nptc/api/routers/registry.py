"""The PropertyDefinition admin router (FR-09, FR-11, FR-12).

Domain exceptions carry `http_status` and `nptc.api.errors` maps them, so no route body has
a try/except. Each route has its own `_RESPONSES_*` dict naming only the statuses it can
produce.

**Mutating routes (`POST`, `PATCH`, `POST .../deprecation`, `DELETE`) require
`Permission.REGISTRY_MANAGE`, and both `GET` routes require `Permission.REGISTRY_READ`**
(FR-44). ADR-0028 records why the reads use neither `REGISTRY_MANAGE` nor
`CATALOGUE_BROWSE`.

**`DELETE` never deletes.** It always raises `PropertyDefinitionDeleteRefusedError` (409),
because `property_definition` has no `DELETE` grant at the database layer. The route exists
so a client gets an actionable 409 naming deprecation, not an unhandled `42501` 500.

**`GET /registry/properties?include_deprecated=` picks the audience.** `false` (the default)
is `DefinitionAudience.DATA_ENTRY`: active properties only. `true` is `EXPORT`: every
status, so a value recorded against a since-deprecated property still resolves
(`docs/architecture/data-model.md`).

**`PATCH` carries no `key` field** (FR-12). `AmendPropertyDefinitionRequest` is
`extra="forbid"`, so a body naming `key` is a pydantic 422 before
`nptc.db.definitions.amend_definition` runs. That function's `PropertyKeyImmutableError`
backs this up for direct callers.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    AuditContextDep,
    get_datatype_registry,
    get_session,
    get_terminology_client,
    permission_dep,
)
from nptc.api.routers.auth import ErrorResponse
from nptc.auth.permissions import Permission
from nptc.catalogue.property_value_sources import (
    DEFAULT_PAGE_SIZE,
    PropertyValueSelectionConflictError,
    list_property_values,
    resolve_property_values,
)
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
from nptc.db.property_specs import spec_for
from nptc.registry.definitions import DefinitionAudience, PropertyDefinitionDeleteRefusedError
from nptc.registry.handlers import ControlKind, DatatypeRegistry
from nptc_shared.terminology import TerminologyClient

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
#: A route can produce two 422 body shapes: a typed domain error (`ErrorResponse`,
#: e.g. `PropertyDatatypeUnknownError`) and a pydantic failure that never reaches
#: the route body (`HTTPValidationError`, e.g. an explicit `null` or an invalid
#: enum value). Declaring only `"model": ErrorResponse` would suppress FastAPI's
#: automatic `HTTPValidationError` entry, so this is an `anyOf` over both
#: component schemas.
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

#: `_to_response` resolves a definition's `datatype` against the live
#: `DatatypeRegistry` for its `form_control`. A row whose `datatype` no longer
#: matches a registered handler is stored-data drift, not a caller mistake: a 500,
#: worded as for `_RESPONSE_500_VALUES` on the `/values` route.
_RESPONSE_500_DATATYPE: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The definition's own stored `datatype` no longer matches a registered "
        "handler - a data integrity fault in the definition, not a caller mistake. "
        "Not produced by anything a well-formed request can trigger on its own."
    ),
}

#: Each route gets its own dict naming only the statuses it can produce.
_RESPONSES_LIST: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_READ,
    500: _RESPONSE_500_DATATYPE,
}
_RESPONSES_GET_ONE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_READ,
    404: _RESPONSE_404,
    500: _RESPONSE_500_DATATYPE,
}
_RESPONSES_CREATE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
    500: _RESPONSE_500_DATATYPE,
}
_RESPONSES_PATCH: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
    500: _RESPONSE_500_DATATYPE,
}
_RESPONSES_DEPRECATE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
    500: _RESPONSE_500_DATATYPE,
}
_RESPONSES_DELETE: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_MANAGE,
    409: _RESPONSE_409,
}

#: The `/values` route's response family, separate from `_RESPONSES_GET_ONE`
#: because it adds the value-source statuses (422's second cause, 500, 502, 503).
#: The 502 and 503 wording follows `routers/terminology.py`: the same
#: `TerminologyClient` failures, reached through `$expand` here and `$lookup` there.
_RESPONSE_422_VALUES: Final[dict[str, Any]] = {
    "description": (
        "The property named by `key` is not a coded property (it has no bound value "
        "source); the `offset`/`count`/`code` query parameters failed validation (`code` "
        "accepts at most 200 values, matching `count`'s own ceiling); or `code` was "
        "combined with `filter`, `offset`, or `count`, which this route refuses rather "
        "than defining an order between the two selection modes."
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
_RESPONSE_500_VALUES: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The property's own stored value_set_uri could not be interpreted - a data "
        "integrity fault in the definition, not a caller mistake. Not produced by "
        "anything a well-formed request can trigger on its own."
    ),
}
_RESPONSE_502_VALUES: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The terminology server's response could not be used - an unparseable body, the "
        'wrong resource type, or a 4xx that was not itself an answer to "does this value '
        'set exist". Only reachable for a `value_set`-bound property. Names no URL, '
        "variable or upstream host."
    ),
}
_RESPONSE_503_VALUES: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The terminology server could not be reached, or a rate limit persisted through "
        "retries. Only reachable for a `value_set`-bound property; a `local_code_system` "
        "binding never calls the terminology server at all. May carry a `Retry-After` "
        "header."
    ),
}
_RESPONSES_VALUES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403_READ,
    404: _RESPONSE_404,
    422: _RESPONSE_422_VALUES,
    500: _RESPONSE_500_VALUES,
    502: _RESPONSE_502_VALUES,
    503: _RESPONSE_503_VALUES,
}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
TerminologyClientDep = Annotated[TerminologyClient, Depends(get_terminology_client)]
_MANAGE = Depends(permission_dep(Permission.REGISTRY_MANAGE))
_READ = Depends(permission_dep(Permission.REGISTRY_READ))


class FormControl(BaseModel):
    """`registry.handlers.FormControlDescriptor`, on the wire (ADR-0013 SS3,
    FR-77). `control` is typed against `ControlKind` - a closed enum
    ADR-0013 sanctions precisely because it does not grow when a datatype
    is added - so OpenAPI emits a union a generated client can switch over
    exhaustively, rather than the bare `datatype` string FR-77 forbids
    branching a form on."""

    model_config = ConfigDict(frozen=True)

    control: ControlKind
    params: dict[str, Any]


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
    form_control: FormControl


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
    constraints close over - an invalid value is a pydantic 422 before the
    request reaches the ORM, not a database `CHECK` violation surfacing as
    a 500. `datatype` stays a bare `str` deliberately - FR-77's own
    extension point, so admitting a new datatype never touches this router -
    and is instead validated by `create_definition` itself, against the live
    `DatatypeRegistry`, where `UnknownDatatypeError` becomes a typed 422
    rather than a broken row that only misbehaves at the first value write."""

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

    **An explicit `null` on a known field is refused, not a silent no-op.**
    None of these fields is a nullable domain value, so a client sending
    `{"label": null, ...}` almost certainly meant to omit the field, not
    clear it - `_reject_explicit_null` below distinguishes "omitted" from
    "provided as null" via `model_fields_set`, which `changes()` cannot do
    once every field has collapsed to `None`."""

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


class PropertyValueItem(BaseModel):
    """One offerable value for a coded property - identical in shape whether
    it came from a SNOMED value set or a local code system; nothing here
    names `binding_target`."""

    model_config = ConfigDict(frozen=True)

    code: str
    display: str | None


class PropertyValuePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[PropertyValueItem]
    total: int


def _to_response(
    definition: PropertyDefinition, registry: DatatypeRegistry
) -> PropertyDefinitionResponse:
    handler = registry.get(definition.datatype)
    descriptor = handler.form_control(spec_for(definition))
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
        form_control=FormControl(control=descriptor.control, params=dict(descriptor.params)),
    )


@router.get(
    "/properties",
    summary="List property definitions",
    responses=_RESPONSES_LIST,
    dependencies=[_READ],
)
def list_properties(
    session: SessionDep,
    registry: RegistryDep,
    include_deprecated: bool = False,
    scope: PropertyScope | None = None,
) -> PropertyDefinitionList:
    """`scope` is inclusive of `PropertyScope.BOTH`: `?scope=submission`
    returns `submission` and `both` properties, `?scope=maintenance`
    returns `maintenance` and `both`, and omitting it returns everything -
    a submission form should not have to also ask for `both` to see a
    property meant for both screens."""
    audience = DefinitionAudience.EXPORT if include_deprecated else DefinitionAudience.DATA_ENTRY
    definitions = list_definitions(session, audience=audience, scope=scope)
    return PropertyDefinitionList(items=[_to_response(d, registry) for d in definitions])


@router.get(
    "/properties/{key}",
    summary="Get one property definition",
    responses=_RESPONSES_GET_ONE,
    dependencies=[_READ],
)
def get_property(
    session: SessionDep, registry: RegistryDep, key: str
) -> PropertyDefinitionResponse:
    definition = load_definition(session, key)
    return _to_response(definition, registry)


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
    return _to_response(definition, registry)


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
    return _to_response(amended, registry)


@router.post(
    "/properties/{key}/deprecation",
    summary="Deprecate a property definition",
    responses=_RESPONSES_DEPRECATE,
    dependencies=[_MANAGE],
)
def deprecate_property(
    session: SessionDep,
    ctx: AuditContextDep,
    registry: RegistryDep,
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
    return _to_response(deprecated, registry)


@router.delete(
    "/properties/{key}",
    summary="Delete a property definition (always refused)",
    status_code=409,
    responses=_RESPONSES_DELETE,
    dependencies=[_MANAGE],
)
def delete_property(key: str) -> None:
    """Always refuses (FR-11) - `property_definition` has no `DELETE` grant
    at the database layer at all. `key` is accepted (and, for a
    genuinely unknown key, still refused the same way, not 404'd) so the
    response is uniform regardless of whether the key exists - the caller's
    mistake either way is asking to delete at all, not naming the wrong
    key."""
    raise PropertyDefinitionDeleteRefusedError(
        f"property_definition {key!r} cannot be deleted; deprecate it instead (FR-11)"
    )


@router.get(
    "/properties/{key}/values",
    summary="List a coded property's offerable values",
    responses=_RESPONSES_VALUES,
    dependencies=[_READ],
)
def list_property_value_options(
    session: SessionDep,
    client: TerminologyClientDep,
    key: str,
    filter: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    count: Annotated[int, Query(ge=1, le=200)] = DEFAULT_PAGE_SIZE,
    code: Annotated[list[str] | None, Query(max_length=200)] = None,
) -> PropertyValuePage:
    """FR-10's concept-picker data source, plus a resolve-by-code lookup
    for a value beyond the picker page's own `DEFAULT_PAGE_SIZE` ceiling.
    Resolves `key`'s own binding and answers from Ontoserver or the
    `LocalCode` table - see
    `nptc.catalogue.property_value_sources.list_property_values`/
    `resolve_property_values` for the only places that branch on
    `binding_target`; this route and `PropertyValuePage` never see it.

    `code` is a second, mutually exclusive selection mode, never combined
    with `filter`/`offset`/`count` - `offset`/`count` default to values a
    caller resolving by `code` would not need to change, so a genuine
    attempt to combine them is what this refuses, not every request that
    happens to also carry those defaults.
    """
    if code is not None:
        if filter is not None or offset != 0 or count != DEFAULT_PAGE_SIZE:
            raise PropertyValueSelectionConflictError(
                "`code` cannot be combined with `filter`, `offset`, or `count`"
            )
        page = resolve_property_values(session, client, key=key, codes=code)
    else:
        page = list_property_values(
            session, client, key=key, filter=filter, offset=offset, count=count
        )
    return PropertyValuePage(
        items=[PropertyValueItem(code=item.code, display=item.display) for item in page.items],
        total=page.total,
    )
