"""The property-value write route (issue #248, FR-09, FR-10, FR-11, FR-36,
FR-37, FR-38, FR-77, FR-88, FR-89).

Everything below HTTP already exists and is already tested as a library -
`nptc.catalogue.property_values.save_property_values` (issue #52) - so
nothing here re-implements a domain rule; this module is purely the HTTP
adapter, following `catalogue_bindings.py`'s own house style exactly:
response models declared here, `ConfigDict(frozen=True)`, the return-type
annotation drives the response model (never `response_model=`),
module-level `Final` error-response dicts naming only the statuses a route
can actually produce, and no try/except in a route body - a domain
exception carries `http_status` and is mapped centrally by `nptc.api.errors`.

**A separate router from `registry.py`, on purpose.** That module owns
`PropertyDefinition` - what a property *is*. This one owns `PropertyValue` -
what an entry *holds* for it - the same `catalogue`/`registry` split
`catalogue_bindings.py` and `catalogue_designations.py` already draw between
an entry's own sub-resources and the reference data they point at.

**Whole-property replace, one route.** `save_property_values` replaces the
entire value set for `(entry, property_key)` in one call - see that
function's own module docstring for why this is a `PUT`, not a `POST` that
appends. There is no route for a single value in isolation.

**The response carries the new `row_version`**, mirroring
`catalogue_designations.AmendDesignationResult` - so an editing client never
has to re-fetch the entry just to learn its next lock token.

**Authorisation:** `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44) - the same
permission every other catalogue write route already uses, not a new one
and not `Permission.REGISTRY_MANAGE`: this writes a catalogue entry's own
values, not a definition. Also in `MFA_REQUIRED_PERMISSIONS`, so the NFR-06
step-up comes free, matching `catalogue_bindings.py`/`catalogue_designations.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    AuditContextDep,
    get_datatype_registry,
    get_session,
    permission_dep,
)
from nptc.api.errors import PropertyValidationResponse
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    BusinessKeyPath,
    PropertyValue,
    property_value_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import queries
from nptc.catalogue.entries import load_entry_for_update
from nptc.catalogue.property_values import PropertyValueInput, save_property_values
from nptc.registry.handlers import DatatypeRegistry

router = APIRouter(prefix="/catalogue", tags=["catalogue-admin"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}

_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `catalogue.edit_published`, "
        "or holds it but has not completed the MFA step-up this permission requires "
        "(the response then also carries a `WWW-Authenticate` step-up challenge)."
    ),
}

_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No catalogue entry, or no property definition, matches the given identifier.",
}

_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The submitted `expected_row_version` no longer matches the entry's current "
        "`row_version` (FR-38) - someone else changed this entry since it was loaded."
    ),
}

#: Three genuinely different 422 body shapes reach a caller of this route
#: (matching `routers/registry.py`'s own `_RESPONSE_422` note): a typed
#: domain error (`ErrorResponse`, e.g. a rejected changelog note or a write
#: against a deprecated property), the typed field-level validation body
#: (`PropertyValidationResponse`), or a pydantic validation failure that
#: never reaches the route body at all (FastAPI's own `HTTPValidationError`).
#:
#: `model` takes the first two as a union so *both* are registered in
#: `components/schemas` and FastAPI's own schema-merge concatenates their
#: `anyOf` with the `HTTPValidationError` `$ref` already in `content` below
#: (issue #248's plan: `PropertyValidationResponse` has no other route that
#: references it as a `model`, so without this it would be `$ref`'d here but
#: never actually registered anywhere in the document - the same trap
#: `catalogue_designations._RESPONSE_409_AMENDMENT`'s own comment names).
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse | PropertyValidationResponse,
    "description": (
        "The `reason` is missing or low-information (FR-37), the write targets a "
        "deprecated property (FR-11), or one or more submitted values fail their "
        "property's JSON Schema, cardinality bound, or FR-89's specimen cross-field "
        "check - `issues[]` then names the `property_key`, `label` and `ordinal` of "
        "each failing value."
    ),
    "content": {
        "application/json": {
            "schema": {"anyOf": [{"$ref": "#/components/schemas/HTTPValidationError"}]}
        }
    },
}

PROPERTY_VALUES_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}


class PropertyValueItemRequest(BaseModel):
    """One value to save, paired with its own `justification` - FR-10's
    extensible-strength case needs both together (see
    `nptc.catalogue.property_values.PropertyValueInput`'s own docstring)."""

    model_config = ConfigDict(frozen=True)

    value: Any
    justification: str | None = None


class SavePropertyValuesRequest(BaseModel):
    """The body of `PUT /catalogue/entries/{business_key}/properties/{key}`.

    `values` is the complete, intended value set for this property after
    the write - `save_property_values` replaces the whole set, never a
    diff (see that function's own module docstring)."""

    model_config = ConfigDict(frozen=True)

    values: list[PropertyValueItemRequest]
    reason: str
    expected_row_version: int


class PropertyValuesWriteResult(BaseModel):
    """The property's values after the write, plus the entry's new
    `row_version` - mirroring `catalogue_designations.
    AmendDesignationResult`, so an editing client never has to re-fetch the
    entry just to learn its next lock token."""

    model_config = ConfigDict(frozen=True)

    values: list[PropertyValue]
    row_version: int


SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


@router.put(
    "/entries/{business_key}/properties/{key}",
    summary="Replace a property's recorded values on a catalogue entry",
    responses=PROPERTY_VALUES_WRITE_RESPONSES,
    dependencies=[_EDIT],
)
def save_property(
    session: SessionDep,
    ctx: AuditContextDep,
    registry: RegistryDep,
    business_key: BusinessKeyPath,
    key: str,
    body: Annotated[SavePropertyValuesRequest, Body()],
) -> PropertyValuesWriteResult:
    entry = load_entry_for_update(session, business_key)
    save_property_values(
        session,
        ctx,
        entry=entry,
        property_key=key,
        values=[
            PropertyValueInput(value=item.value, justification=item.justification)
            for item in body.values
        ],
        reason=body.reason,
        registry=registry,
        expected_row_version=body.expected_row_version,
    )
    session.flush()
    return PropertyValuesWriteResult(
        values=_row_to_property_values(
            session, entry_id=entry.id, property_key=key, registry=registry
        ),
        row_version=entry.row_version,
    )


def _row_to_property_values(
    session: Session, *, entry_id: uuid.UUID, property_key: str, registry: DatatypeRegistry
) -> list[PropertyValue]:
    """Re-reads the just-written rows through `nptc.catalogue.queries.
    load_property_values` rather than building `PropertyValue` from
    `save_property_values`' own return value directly - that is what
    resolves the definition's `label`/`cardinality`/`status` and renders
    `value` via the same `property_value_from_row` the read routes use
    (`catalogue_bindings.py`'s `_row_to_binding` is the identical
    precedent), so a value renders identically whether it was just written
    or freshly read."""
    return [
        property_value_from_row(row, registry)
        for row in queries.load_property_values(session, (entry_id,))
        if row.property_key == property_key
    ]
