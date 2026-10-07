"""The entry-level core-column write route: `status` (FR-36, FR-37, FR-38).

`catalogue_entry` has three auditable core columns. `business_key` is immutable (FR-03) and
`preferred_term` writes through `POST .../designations/amendment` (ADR-0022). This route
covers the other one.

The HTTP adapter over `nptc.catalogue.entries.save_entry`; it re-implements no domain rule.
A domain exception carries `http_status` and `nptc.api.errors` maps it, so no route body has
a try/except. `docs/architecture/catalogue-write-api.md` ("Entry core columns") records the
reasoning behind the points below.

**`PATCH` on the entry.** `status` is a core column of one row under one `row_version`, and
`save_entry` applies it in one `EntryChanges` and one audit event.

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

**A no-op resubmission is a `200`, not a `422`.** `save_entry` short-circuits when `status`
already holds the submitted value: the response is a `200` with the unchanged `row_version`,
no audit event is written, and the `reason` is discarded.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from nptc.api.dependencies import AuditContextDep, get_session, permission_dep
from nptc.api.errors import VersionConflictResponse
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

#: Two 422 body shapes reach a caller: a typed domain error (`ErrorResponse`, a rejected
#: changelog note) or a pydantic failure that never reaches the route body (a missing or
#: unrecognised `status`: `HTTPValidationError`).
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The `reason` is missing or low-information (FR-37), or `status` is missing or not "
        "one of `draft`, `active`, `deprecated` or `withdrawn`."
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
    """The body of `PATCH /catalogue/entries/{business_key}`. Unknown fields are refused, so a
    client that still sends the retired `specimen_unconstrained` fails loudly (ADR-0044)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CatalogueEntryStatus
    reason: str
    expected_row_version: int


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
    row_version: int


SessionDep = Annotated[Session, Depends(get_session)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


@router.patch(
    "/entries/{business_key}",
    summary="Set a catalogue entry's status",
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
        changes=EntryChanges(status=str(body.status)),
        reason=body.reason,
    )
    session.flush()
    return EntryCoreWriteResult(status=entry.status, row_version=entry.row_version)
