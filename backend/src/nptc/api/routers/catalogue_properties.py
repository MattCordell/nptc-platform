"""The property-value write routes (FR-09, FR-10, FR-11, FR-36, FR-37, FR-38, FR-39, FR-77,
FR-88, FR-89).

The HTTP adapter over `nptc.catalogue.property_values`; it re-implements no domain rule. A
domain exception carries `http_status` and `nptc.api.errors` maps it, so no route body has a
try/except. `docs/architecture/catalogue-write-api.md` ("Property values") records the
reasoning behind the points below.

**A separate router from `registry.py`.** That module owns `PropertyDefinition`, what a
property *is*. This one owns `PropertyValue`, what an entry *holds* for it.

**Whole-property replace.** `save_property_values` replaces the entire value set for
`(entry, property_key)`, which is why the route is a `PUT`. No route writes a single value.

**The response carries the new `row_version`**, so an editing client never re-fetches the
entry to learn its next lock token.

**Authorisation:** `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44), not `REGISTRY_MANAGE`:
the route writes an entry's values, not a definition. The permission is in
`MFA_REQUIRED_PERMISSIONS`, so the NFR-06 step-up applies.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from nptc.api.dependencies import (
    AuditContextDep,
    get_datatype_registry,
    get_session,
    permission_dep,
)
from nptc.api.errors import (
    PropertyValidationResponse,
    VersionConflictResponse,
    version_conflict_response,
)
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    BusinessKeyPath,
    PropertyValue,
    property_value_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import queries
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN, load_entry_for_update
from nptc.catalogue.property_values import (
    EntryPropertyTarget,
    PropertyValueInput,
    save_property_values,
    save_property_values_for_entries,
    tally_bulk_outcomes,
)
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

#: A stale `expected_row_version` raises the `EntryVersionConflictError` that
#: `save_entry` raises, and `nptc.api.errors` always builds its body as
#: `VersionConflictResponse`. `model` takes the union so that schema lands in
#: `components/schemas`.
_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse | VersionConflictResponse,
    "description": (
        "The submitted `expected_row_version` no longer matches the entry's current "
        "`row_version` (FR-38) - someone else changed this entry since it was loaded. "
        "Carries `business_key`, `expected_row_version`, `current_row_version`, "
        "`conflicts[]` (each with `field`, `submitted` and `current`) and "
        "`changed_by`/`changed_at`, so the caller can reconcile rather than retry blind."
    ),
}

#: Three 422 body shapes reach a caller: a typed domain error (`ErrorResponse`,
#: e.g. a rejected changelog note or a write against a deprecated property), the
#: typed field-level body (`PropertyValidationResponse`), or a pydantic failure
#: that never reaches the route body (`HTTPValidationError`).
#:
#: `model` takes the first two as a union so both register in
#: `components/schemas`, and FastAPI merges their `anyOf` with the
#: `HTTPValidationError` `$ref` in `content` below. `PropertyValidationResponse`
#: is referenced by no other route, so without the union it would be `$ref`'d but
#: never registered.
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

#: `save_property_values` and the re-read below resolve `key`'s stored `datatype`
#: against the live `DatatypeRegistry`, as `registry.py`'s `_to_response` does. A
#: `datatype` that is no longer registered is a data integrity fault, not a caller
#: mistake; the wording matches `registry.py`'s `_RESPONSE_500_DATATYPE`.
_RESPONSE_500: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The definition's own stored `datatype` no longer matches a registered "
        "handler - a data integrity fault in the definition, not a caller mistake. "
        "Not produced by anything a well-formed request can trigger on its own."
    ),
}

PROPERTY_VALUES_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
    500: _RESPONSE_500,
}

#: No 409: a stale `expected_row_version` is a per-entry `conflict` outcome in the
#: 200 body, never a whole-request refusal (see `BulkSavePropertyValuesResult`).
_RESPONSE_404_BULK: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "No property definition matches `key`. An unknown `business_key` "
        "among `entries` is a per-entry `not-found` outcome in the 200 "
        "response, never a 404 for the whole request."
    ),
}

BULK_PROPERTY_VALUES_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404_BULK,
    422: _RESPONSE_422,
    500: _RESPONSE_500,
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


#: Each entry's write holds a `pg_advisory_xact_lock` until commit
#: (`nptc.audit.writer.append_audit_event`, ADR-0017), so the cap bounds lock
#: contention for one request. ADR-0035 records the cap.
_MAX_BULK_ENTRIES: Final[int] = 100


class BulkPropertyEntryTarget(BaseModel):
    """One `(business_key, expected_row_version)` selection for the bulk
    write - the version this entry held when the caller selected it, not
    resolved server-side (see `nptc.catalogue.property_values.
    EntryPropertyTarget`'s own docstring for why a filter expression could
    never do this instead, ADR-0035).

    `business_key`'s shape is validated here, in the request body, the same
    pattern `BusinessKeyPath` enforces on the singular route's path segment
    - derivable from the request alone, so it belongs on the whole-request
    side of the split (see `BulkSavePropertyValuesRequest`'s own docstring):
    a malformed key is a 422 for the whole batch, not a `not-found` outcome
    indistinguishable from a well-formed key that simply does not exist."""

    model_config = ConfigDict(frozen=True)

    business_key: str = Field(pattern=BUSINESS_KEY_PATTERN.pattern)
    expected_row_version: int


class BulkSavePropertyValuesRequest(BaseModel):
    """The body of `POST /catalogue/entries/bulk/properties/{key}`
    (issue #265, FR-39). `values` is the one set every named entry ends up
    holding - a whole-set replace, identical to the singular route's own
    semantics, applied across `entries` rather than one."""

    model_config = ConfigDict(frozen=True)

    values: list[PropertyValueItemRequest]
    reason: str
    entries: list[BulkPropertyEntryTarget] = Field(min_length=1, max_length=_MAX_BULK_ENTRIES)

    @model_validator(mode="after")
    def _reject_duplicate_business_keys(self) -> BulkSavePropertyValuesRequest:
        keys = [entry.business_key for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "entries must not repeat a business_key - a duplicate would carry "
                "the same expected_row_version twice, and the second occurrence is "
                "guaranteed to conflict against a version the first one just wrote"
            )
        return self


class BulkPropertyOutcomeItem(BaseModel):
    """One target's result, in request order - see `nptc.catalogue.
    property_values.BulkPropertyOutcome`'s own docstring for what each
    `status` means and when `row_version`/`conflict` are populated. No
    `values` echo: the batch wrote one set every caller already has."""

    model_config = ConfigDict(frozen=True)

    business_key: str
    status: Literal["applied", "unchanged", "conflict", "not-found"]
    row_version: int | None
    conflict: VersionConflictResponse | None = None


class BulkSavePropertyValuesResult(BaseModel):
    """The per-entry outcome list, plus its own tallies. Always a 200: the
    request was authorised, well-formed, and fully processed, and the
    outcomes *are* the representation - including a batch where every
    entry conflicted (issue #265's plan: a whole-request 409 would have to
    discard the applied entries' new `row_version`s, the one thing a
    retrying client needs)."""

    model_config = ConfigDict(frozen=True)

    outcomes: list[BulkPropertyOutcomeItem]
    applied: int
    unchanged: int
    conflict: int
    not_found: int


@router.post(
    "/entries/bulk/properties/{key}",
    summary="Replace a property's recorded values across many catalogue entries",
    responses=BULK_PROPERTY_VALUES_WRITE_RESPONSES,
    dependencies=[_EDIT],
)
def save_property_bulk(
    session: SessionDep,
    ctx: AuditContextDep,
    registry: RegistryDep,
    key: str,
    body: Annotated[BulkSavePropertyValuesRequest, Body()],
) -> BulkSavePropertyValuesResult:
    outcomes = save_property_values_for_entries(
        session,
        ctx,
        targets=[
            EntryPropertyTarget(
                business_key=target.business_key,
                expected_row_version=target.expected_row_version,
            )
            for target in body.entries
        ],
        property_key=key,
        values=[
            PropertyValueInput(value=item.value, justification=item.justification)
            for item in body.values
        ],
        reason=body.reason,
        registry=registry,
    )
    # This response and the audit header's tallies both come from
    # `tally_bulk_outcomes`, so they cannot disagree about one batch.
    tallies = tally_bulk_outcomes(outcomes)
    return BulkSavePropertyValuesResult(
        outcomes=[
            BulkPropertyOutcomeItem(
                business_key=outcome.business_key,
                status=outcome.status,
                row_version=outcome.row_version,
                conflict=(
                    version_conflict_response(outcome.conflict)
                    if outcome.conflict is not None
                    else None
                ),
            )
            for outcome in outcomes
        ],
        applied=tallies["applied"],
        unchanged=tallies["unchanged"],
        conflict=tallies["conflict"],
        not_found=tallies["not-found"],
    )


def _row_to_property_values(
    session: Session, *, entry_id: uuid.UUID, property_key: str, registry: DatatypeRegistry
) -> list[PropertyValue]:
    """Re-reads the just-written rows through `nptc.catalogue.queries.load_property_values`,
    not from `save_property_values`' return value. That resolves the definition's
    `label`, `cardinality` and `status` and renders `value` through
    `property_value_from_row`, so a value renders the same whether just written or
    freshly read (as `catalogue_bindings.py`'s `_row_to_binding` does).

    Scoped to `property_keys=(property_key,)`: the route writes one property, so
    fetching the entry's others only to filter them out would scale with how many
    it carries, not with the one write."""
    return [
        property_value_from_row(row, registry)
        for row in queries.load_property_values(session, (entry_id,), property_keys=(property_key,))
    ]
