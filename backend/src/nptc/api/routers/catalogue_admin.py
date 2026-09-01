"""The authenticated admin read API over the catalogue, any status (issue
#228, FR-17, FR-36).

Every catalogue entry is born `draft` (`create_entry`'s own default
status), and the #219/#224 write routes already resolve a `draft` entry
fine for writing - `load_entry_for_update` carries no status filter, on
purpose (see its own docstring). What was missing is a read route an edit
screen (#149) can call first to render the form: `routers/catalogue.py`'s
public detail route resolves the entry through `nptc.catalogue.queries.
get_entry`, which filters to `PUBLIC_STATUSES` (`active` only) and 404s a
`draft` identically to a `business_key` that was never minted - and that
indistinguishability is FR-20's own deliberate contract, not a gap to
close there.

**A separate router from `catalogue.py`, on purpose** - same reasoning as
`catalogue_bindings.py`/`catalogue_designations.py`'s own module
docstrings: `catalogue.py` is the public, unauthenticated surface, and
`test_api_public_status_filter.py`/`test_api_public_response_hygiene.py`
both derive what they scan from its route table. Folding a permission-gated
branch into that module's existing route would mean carving an exception
into both of those guard tests for the one route in the file that is not
actually public; a second router with its own tag needs neither.

**The path is `/catalogue/admin/entries/{business_key}`, not a widened
`/catalogue/entries/{business_key}`.** One URL per audience: a vendor
integration and an authenticated edit screen have different failure
contracts (compare `_RESPONSE_404` below, which names no detail, against
`catalogue.py`'s own, which explains *why* it names none) and are safest
kept as two routes a reviewer can permission-audit independently, rather
than one route whose behaviour depends on who is asking.

**Gated on `Permission.CATALOGUE_EDIT_PUBLISHED`, not a new read
permission.** The audience for this route is exactly the audience for the
#224 write routes - an edit screen has to be able to load what it is about
to save - so reusing the write permission means that audience needs one
credential posture, not two, and needing MFA step-up for a read that only
exists to feed a write is the same posture PRD SS4.7 already assigns
Administrator's write capability. A narrower `catalogue.read_unpublished`
permission was considered and rejected: `ROLE_PERMISSIONS` is asserted
cell-by-cell against the PRD's own table by `test_permission_matrix.py`,
so minting one would mean a PRD change this issue does not ask for.

**Resolves the entry via `load_entry_for_update`, not a new query
function.** `nptc.catalogue.queries`' own module docstring makes
`PUBLIC_STATUSES` "the only status filter" it applies, so an
unfiltered getter does not belong there. `load_entry_for_update` already
is that unfiltered getter - the #224 write routes prove it resolves a
`draft` correctly - and is `public` (not `_load_for_update`) precisely so
another part of the write/admin surface can share it rather than
re-querying `CatalogueEntry` by hand.

**Serves the identical `EntryDetail` shape the public detail route
does**, assembled from the same three loaders
(`queries.load_designations`, `queries.load_bindings`,
`queries.load_property_values`) - an edit screen consuming this route
today gets the same fields a public consumer of the same entry, once
published, would see. See `catalogue_shared.py`'s own docstring for why
`EntryDetail` and its assembly helpers live there rather than being
duplicated here.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_datatype_registry, get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    BusinessKeyPath,
    EntryDetail,
    binding_from_row,
    designation_from_row,
    entry_summary_fields,
    property_value_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import queries
from nptc.catalogue.entries import load_entry_for_update
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
#: Deliberately the same generic wording as `catalogue.py`'s own
#: `_RESPONSE_404`, and for the same reason on this route as on that one:
#: this route exists so an *authenticated* caller can see a `draft`, but a
#: business key that was never minted is still just absent, not a
#: distinguishable "found, but you can't have it".
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No catalogue entry, of any status, has this business key.",
}
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The business key is not `NPTC-nnnnnn`.",
}
#: Documented (not merely a possible framework error) because the route
#: genuinely can produce it: it renders a `display_term`, matching
#: `catalogue.py`'s own `_RESPONSE_500_DISPLAY_TERM` on its equivalent
#: route - a published binding whose stored FSN is not in the form the
#: terminology server serves is a server-side data fault, not a fault in
#: the request.
_RESPONSE_500_DISPLAY_TERM: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A code binding's stored Fully Specified Name is not in the form the "
        "terminology server serves, so its display term cannot be rendered. This "
        "is a data fault in the catalogue, not a fault in the request; it needs "
        "an administrator, and retrying will not clear it."
    ),
}

_RESPONSES_ADMIN_READ: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    422: _RESPONSE_422,
    500: _RESPONSE_500_DISPLAY_TERM,
}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


@router.get(
    "/admin/entries/{business_key}",
    summary="One catalogue entry, any status, with everything attached to it",
    responses=_RESPONSES_ADMIN_READ,
    dependencies=[_EDIT],
)
def read_entry_any_status(
    session: SessionDep,
    registry: RegistryDep,
    business_key: BusinessKeyPath,
) -> EntryDetail:
    """The `catalogue.edit_published`-gated counterpart to `catalogue.py`'s
    public `read_entry`: identical assembly, no status filter. An edit
    screen (#149) calls this to load a `draft` entry's current state before
    the #224 write routes save changes to it."""
    entry = load_entry_for_update(session, business_key)
    entry_ids = (entry.id,)
    return EntryDetail(
        **entry_summary_fields(
            entry.business_key,
            entry.preferred_term,
            entry.length,
            entry.status,
            entry.specimen_unconstrained,
            entry.updated_at,
        ),
        row_version=entry.row_version,
        designations=[
            designation_from_row(row) for row in queries.load_designations(session, entry_ids)
        ],
        bindings=[binding_from_row(row) for row in queries.load_bindings(session, entry_ids)],
        properties=[
            property_value_from_row(row, registry)
            for row in queries.load_property_values(session, entry_ids)
        ],
    )
