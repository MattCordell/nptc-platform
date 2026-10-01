"""The entry-level core-column write route: `status` and `specimen_unconstrained` (FR-36,
FR-37, FR-38, FR-89).

`catalogue_entry` has four auditable core columns. `business_key` is immutable (FR-03) and
`preferred_term` writes through `POST .../designations/amendment` (ADR-0022). This route
covers the other two.

The HTTP adapter over `nptc.catalogue.entries.save_entry`; it re-implements no domain rule.
A domain exception carries `http_status` and `nptc.api.errors` maps it, so no route body has
a try/except. `docs/architecture/catalogue-write-api.md` ("Entry core columns") records the
reasoning behind the points below.

**One `PATCH`, not two sub-resources.** Both fields are core columns of one row under one
`row_version`, and `save_entry` applies them in one `EntryChanges` and one audit event. Two
routes would mean two lock tokens and two audit events for what an editor experiences as
one save. `PATCH` semantics (an absent field means no change) match `EntryChanges`'
`None`-means-unchanged contract, including `specimen_unconstrained=False`, which
`EntryChanges.as_dict()` keeps because `False is not None`.

**A separate router module**, since it owns `CatalogueEntry`'s own core columns, a different
write surface from a property's values or a designation row.

**It shares its path with the public `GET`**, not `catalogue_admin.py`'s `/admin/` prefix, so
it sits in the same `/catalogue/entries/{business_key}` write family as the other write
routes. The OpenAPI path item then holds a public `get` (tag `catalogue`) and an admin
`patch` (tag `catalogue-admin`). `catalogue_admin.py`'s `/admin/entries/{business_key}` is a
different route, not this one's read counterpart.

**No status transition rules.** `save_entry` does a bare `setattr` and the PRD defines no
state machine for `status`. The wire type is `CatalogueEntryStatus`, so a value outside the
four statuses is a 422, and the table's `CHECK` constraint is the backstop.

**Authorisation:** `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44), in
`MFA_REQUIRED_PERMISSIONS` (NFR-06).

**A no-op resubmission is a `200`, not a `422`.** `save_entry` short-circuits when a named
field already holds the submitted value: the response is a `200` with the unchanged
`row_version`, no audit event is written, and the `reason` is discarded. A body naming neither
field is a `422`, because that is ambiguous between a no-op and a forgotten field.
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

#: A stale `expected_row_version` raises `save_entry`'s `EntryVersionConflictError`,
#: as in `catalogue_properties.py`.
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

#: Three 422 body shapes reach a caller, as in `catalogue_properties.py`: a typed
#: domain error (`ErrorResponse`, a rejected changelog note), the field-level body
#: (`PropertyValidationResponse`, FR-89's specimen conflict, which
#: `assert_specimen_flag_allowed` raises as `save_property_values` does), or a
#: pydantic failure that never reaches the route body (an unrecognised `status`, or
#: a body naming neither field: `HTTPValidationError`).
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
    refused rather than silently treated as a no-op write.
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
    """The entry's core columns after the write, plus its new `row_version` -
    mirroring `catalogue_properties.PropertyValuesWriteResult`, so an
    editing client never has to re-fetch the entry just to learn its next
    lock token. `status` is typed `CatalogueEntryStatus`, matching the
    request field it round-trips into (`entry.status` is a plain `str` at
    the ORM layer - see `catalogue_entry.py`'s note on why the column
    itself is `TEXT`, not a native `ENUM` - but the wire contract stays a
    closed union both ways, so a caller can feed this response straight
    back into its next PATCH body without a cast)."""

    model_config = ConfigDict(frozen=True)

    status: CatalogueEntryStatus
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
