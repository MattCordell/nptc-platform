"""The entry-level core-column write route: `status` and
`specimen_unconstrained` (issue #249, FR-36, FR-37, FR-38, FR-89).

`catalogue_entry` has four auditable core columns. `business_key` is
immutable by design (FR-03) and `preferred_term` writes through `POST
.../designations/amendment` (issue #227, ADR-0022's two storage homes) -
this route is what closes the remaining gap FR-36's own note in
`docs/requirements/requirements.yaml` named: "entry-level status/
specimen_unconstrained writes have no route".

Everything below HTTP already exists and is already tested as a library -
`nptc.catalogue.entries.save_entry` (issue #46) - so nothing here
re-implements a domain rule; this module is purely the HTTP adapter,
following `catalogue_properties.py`'s house style exactly: response models
declared here, `ConfigDict(frozen=True)`, the return-type annotation drives
the response model (never `response_model=`), module-level `Final`
error-response dicts naming only the statuses a route can actually
produce, and no try/except in a route body - a domain exception carries
`http_status` and is mapped centrally by `nptc.api.errors`.

**One `PATCH`, not two named sub-resources.** Both fields are core columns
of one row under one `row_version`, and `save_entry` already applies them
in one `EntryChanges`/one audit event - splitting them into two routes
would mean two lock tokens and two audit events for what an editor
experiences as one save. `PATCH` semantics (an absent field means no
change) map exactly onto `EntryChanges`' own `None`-means-unchanged
contract, including the `specimen_unconstrained=False` case, which
`EntryChanges.as_dict()` already keeps (`False is not None`).

**A new router module, not folded into `catalogue_properties.py` or
`catalogue_designations.py`.** This one owns `CatalogueEntry`'s own core
columns - a distinct write surface from a property's values or a
designation row, matching the one-router-per-thing-written pattern those
two modules (and `catalogue_bindings.py`) already establish.

**Shares its path with the public `GET`, not `catalogue_admin.py`'s
`/admin/` prefix.** `PATCH /catalogue/entries/{business_key}` sits in the
same write family every other write route lives in
(`catalogue_bindings.py`, `catalogue_designations.py`,
`catalogue_properties.py`), all under `/catalogue/entries/{business_key}`.
The OpenAPI document then carries one path item for that route holding a
public `get` (tag `catalogue`) and an admin `patch` (tag
`catalogue-admin`) - legal, and what makes the read/write pair share a
URL. `catalogue_admin.py`'s own `/admin/entries/{business_key}` `GET` is a
different route entirely (any status, gated), not this route's read
counterpart.

**No status transition rules.** `save_entry` does a bare `setattr` and the
PRD defines no state machine for `status`; the wire type is
`CatalogueEntryStatus`, so a value outside `draft|active|deprecated|
withdrawn` is a 422 before the route body ever runs, and the table's own
`CHECK` constraint remains the backstop.

**Authorisation:** `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44) - the same
permission every other catalogue write route already uses. Also in
`MFA_REQUIRED_PERMISSIONS`, so the NFR-06 step-up comes free, matching
`catalogue_properties.py`/`catalogue_designations.py`.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session

from nptc.api.dependencies import AuditContextDep, get_session, permission_dep
from nptc.api.errors import PropertyValidationResponse, VersionConflictResponse
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import BusinessKeyPath
from nptc.auth.permissions import Permission
from nptc.catalogue.entries import EntryChanges, save_entry
from nptc.db.models.catalogue_entry import CatalogueEntryStatus

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
    "description": "No catalogue entry matches the given business_key.",
}

#: The stale-`expected_row_version` refusal is `save_entry`'s own
#: `EntryVersionConflictError`/`ConflictReport` - matching
#: `catalogue_properties.py`'s identical precedent for the same exception.
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

#: Three genuinely different 422 body shapes reach a caller of this route,
#: matching `catalogue_properties.py`'s own `_RESPONSE_422` note: a typed
#: domain error (`ErrorResponse`, a rejected changelog note), the typed
#: field-level validation body (`PropertyValidationResponse`, FR-89's
#: specimen conflict - `nptc.catalogue.property_values.
#: assert_specimen_flag_allowed` raises the same `PropertyValidationError`
#: `save_property_values` does), or a pydantic validation failure that
#: never reaches the route body at all (an unrecognised `status`, or a body
#: naming neither field - FastAPI's own `HTTPValidationError`).
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse | PropertyValidationResponse,
    "description": (
        "The `reason` is missing or low-information (FR-37), the body names neither "
        "`status` nor `specimen_unconstrained`, `status` is not one of `draft`, "
        "`active`, `deprecated` or `withdrawn`, or setting `specimen_unconstrained` "
        "to `true` conflicts with one or more specimen values already recorded on "
        "this entry (FR-89) - `issues[]` then names each blocking value by `ordinal`."
    ),
    "content": {
        "application/json": {
            "schema": {"anyOf": [{"$ref": "#/components/schemas/HTTPValidationError"}]}
        }
    },
}

ENTRY_CORE_WRITE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}


class PatchEntryRequest(BaseModel):
    """The body of `PATCH /catalogue/entries/{business_key}`.

    `status`/`specimen_unconstrained` are both optional so a caller can set
    either or both in one save - `None` means "leave this field alone",
    matching `EntryChanges`' own contract - but a body naming neither is
    refused rather than silently treated as a no-op write (issue #249's own
    acceptance criterion).
    """

    model_config = ConfigDict(frozen=True)

    status: CatalogueEntryStatus | None = None
    specimen_unconstrained: bool | None = None
    reason: str
    expected_row_version: int

    @model_validator(mode="after")
    def _reject_empty_body(self) -> PatchEntryRequest:
        if self.status is None and self.specimen_unconstrained is None:
            raise ValueError("at least one of status or specimen_unconstrained must be given")
        return self


class EntryCoreWriteResult(BaseModel):
    """The entry's core columns after the write, plus its new `row_version`
    - mirroring `catalogue_properties.PropertyValuesWriteResult`, so an
    editing client never has to re-fetch the entry just to learn its next
    lock token."""

    model_config = ConfigDict(frozen=True)

    status: str
    specimen_unconstrained: bool
    row_version: int


SessionDep = Annotated[Session, Depends(get_session)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


@router.patch(
    "/entries/{business_key}",
    summary="Set a catalogue entry's status and/or specimen_unconstrained flag",
    responses=ENTRY_CORE_WRITE_RESPONSES,
    dependencies=[_EDIT],
)
def patch_entry(
    session: SessionDep,
    ctx: AuditContextDep,
    business_key: BusinessKeyPath,
    body: Annotated[PatchEntryRequest, Body()],
) -> EntryCoreWriteResult:
    entry = save_entry(
        session,
        ctx,
        business_key=business_key,
        expected_row_version=body.expected_row_version,
        changes=EntryChanges(
            status=str(body.status) if body.status is not None else None,
            specimen_unconstrained=body.specimen_unconstrained,
        ),
        reason=body.reason,
    )
    session.flush()
    return EntryCoreWriteResult(
        status=entry.status,
        specimen_unconstrained=entry.specimen_unconstrained,
        row_version=entry.row_version,
    )
