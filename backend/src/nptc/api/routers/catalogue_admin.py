"""The authenticated admin read API over the catalogue, any status (issues
#228, #266, FR-17, FR-36, FR-14, FR-15, FR-16, FR-44).

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

**The path is `/catalogue/admin/...`, not a widened `/catalogue/...`.** One
URL per audience: a vendor integration and an authenticated edit screen
have different failure contracts (compare `_RESPONSE_404` below, which
names no detail, against `catalogue.py`'s own, which explains *why* it
names none) and are safest kept as separate routes a reviewer can
permission-audit independently, rather than one route whose behaviour
depends on who is asking.

**Gated on `Permission.CATALOGUE_EDIT_PUBLISHED`, not a new read
permission.** The audience for these routes is exactly the audience for the
#224 write routes - an edit screen has to be able to load what it is about
to save, and issue #266's listing/search are how that screen finds the
entry in the first place - so reusing the write permission means that
audience needs one credential posture, not two, and needing MFA step-up for
a read that only exists to feed a write is the same posture PRD SS4.7
already assigns Administrator's write capability. A narrower
`catalogue.read_unpublished` permission was considered and rejected:
`ROLE_PERMISSIONS` is asserted cell-by-cell against the PRD's own table by
`test_permission_matrix.py`, so minting one would mean a PRD change this
issue does not ask for.

**Resolves the entry via `load_entry_for_update`, not a new query
function.** `nptc.catalogue.queries`' own module docstring makes
`PUBLIC_STATUSES` "the only status filter" it applies, so an
unfiltered getter does not belong there. `load_entry_for_update` already
is that unfiltered getter - the #224 write routes prove it resolves a
`draft` correctly - and is `public` (not `_load_for_update`) precisely so
another part of the write/admin surface can share it rather than
re-querying `CatalogueEntry` by hand. The all-status *collection* routes
(issue #266) use the same reasoning one level up: `nptc.catalogue.
maintenance.list_entries_any_status` is `queries.list_entries` with the
status tuple widened, kept as a separate function for the identical reason
- `queries.py`'s rule one is that `PUBLIC_STATUSES` is the *only* filter it
ever applies, not merely the default one.

**Serves the identical `EntryDetail`/`EntryPage`/`SearchPage` shapes the
public routes do**, assembled from the same loaders and the same
`nptc.catalogue.search` ranking - an edit screen consuming these routes
today gets the same fields a public consumer of the same entry, once
published, would see, just with every status in scope and `status` on the
wire so a caller can tell a draft from an active entry (issue #266's own
acceptance criterion). See `catalogue_shared.py`'s own docstring for why
these models and their assembly helpers live there rather than being
duplicated here.

**The two collection routes have no 404**, matching
`catalogue.py`'s own `PUBLIC_COLLECTION_ERROR_RESPONSES`: an unmatched
query or an empty catalogue is an empty page, not a missing resource.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_datatype_registry, get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue import (
    FILTER_PARAMETER,
    CursorQuery,
    EntryCursorQuery,
    FilterRequest,
    LimitQuery,
)
from nptc.api.routers.catalogue_shared import (
    BusinessKeyPath,
    EntryDetail,
    EntryPage,
    EntrySummary,
    Facet,
    FacetBucket,
    SearchHit,
    SearchPage,
    binding_from_row,
    designation_from_row,
    entry_summary_fields,
    property_value_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import maintenance, queries, search
from nptc.catalogue.entries import load_entry_for_update
from nptc.catalogue.facets import load_facet_context, parse_filters
from nptc.catalogue.term_hygiene import preferred_term_length
from nptc.db.models.catalogue_entry import CatalogueEntry
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
#: No 500: this route used to render a `display_term`, matching
#: `catalogue.py`'s own equivalent route, but FR-98 (issue #144) removed
#: the read path's only rendering call site - there is nothing left here
#: that can fail this way.
_RESPONSES_ADMIN_READ: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    422: _RESPONSE_422,
}

#: Issue #266's collection routes' own 422 - broader than `_RESPONSE_422`
#: above (which names only the business-key path parameter's shape),
#: matching `catalogue.py`'s own `_RESPONSE_422` for its collection routes.
_RESPONSE_422_COLLECTION: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query parameter was unprocessable - a blank search query, a cursor this "
        "API did not issue (including one issued for a different `q` or a different "
        "filter set), a `limit` outside its range, or a `filter.*` parameter naming a "
        "facet this endpoint does not offer, an operator the facet does not support, "
        "or a value the property cannot hold. A filter is never silently ignored."
    ),
}

#: The two all-status collection routes (issue #266). No 404, matching
#: `catalogue.py`'s own `PUBLIC_COLLECTION_ERROR_RESPONSES` - see the module
#: docstring.
_RESPONSES_ADMIN_COLLECTION: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _RESPONSE_422_COLLECTION,
}

#: `catalogue.py`'s own `_FILTER_OPENAPI`, rebuilt here rather than
#: imported: that name carries a leading underscore there, marking it
#: private to that module (ADR-0032's by-hand `filter.*` parameter has no
#: other way to reach the generated document - see `FILTER_PARAMETER`'s own
#: docstring), and the parameter itself (no underscore) is this surface's
#: real, shared dependency.
_ADMIN_FILTER_OPENAPI: Final[dict[str, Any]] = {"parameters": [FILTER_PARAMETER]}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


def _admin_filter_request(
    request: Request,
    session: SessionDep,
    registry: RegistryDep,
) -> FilterRequest:
    """`catalogue.py`'s own `_filter_request`, scoped to
    `maintenance.MAINTENANCE_STATUSES` instead of `queries.PUBLIC_STATUSES`
    (issue #266) - so `?filter.status=draft` is a real filter here rather
    than the well-formed-but-empty-page refusal it would be on the public
    surface (see `_core_facets`' own docstring in `nptc.catalogue.facets`).
    `FilterRequest` itself is reused unchanged: the shape (a facet context
    plus a parsed selection) does not depend on which statuses that context
    permits.
    """
    context = load_facet_context(session, registry, status_values=maintenance.MAINTENANCE_STATUSES)
    return FilterRequest(
        context=context,
        selections=parse_filters(request.query_params.multi_items(), context),
    )


AdminFiltersDep = Annotated[FilterRequest, Depends(_admin_filter_request)]


def _summary(entry: CatalogueEntry, has_open_finding: bool) -> EntrySummary:
    """`catalogue.py`'s own `_summary` (private there, so rebuilt here
    rather than imported - see `_ADMIN_FILTER_OPENAPI`'s own note)."""
    return EntrySummary(
        **entry_summary_fields(
            entry.business_key,
            entry.preferred_term,
            entry.length,
            entry.status,
            entry.specimen_unconstrained,
            entry.updated_at,
            has_open_finding,
        )
    )


@router.get(
    "/admin/entries",
    summary="One page of catalogue entries, any status (issue #266)",
    responses=_RESPONSES_ADMIN_COLLECTION,
    dependencies=[_EDIT],
    openapi_extra=_ADMIN_FILTER_OPENAPI,
)
def list_entries_any_status(
    session: SessionDep,
    filters: AdminFiltersDep,
    limit: LimitQuery = 50,
    after: EntryCursorQuery = None,
) -> EntryPage:
    """The `catalogue.edit_published`-gated counterpart to `catalogue.py`'s
    public `list_entries`: identical keyset paging on `business_key`, every
    status in scope rather than `PUBLIC_STATUSES` alone, and `status` on
    each row so a caller can tell a draft from an active entry.

    `filter.*` parameters behave as they do on the public surface, except
    `?filter.status=` now accepts any `CatalogueEntryStatus` value rather
    than only `active` - `AdminFiltersDep` builds its facet context from
    `maintenance.MAINTENANCE_STATUSES`. Facets are not returned here for the
    same reason they are not on `/catalogue/entries`: `GET
    /catalogue/admin/search` is where the facet list with counts lives.
    """
    page = maintenance.list_entries_any_status(
        session, limit=limit, after=after, filters=filters.selections
    )
    open_findings = queries.open_finding_business_keys(
        session, (entry.business_key for entry in page.entries)
    )
    return EntryPage(
        items=[_summary(entry, entry.business_key in open_findings) for entry in page.entries],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/admin/search",
    summary="Search catalogue entries by term, any status (issue #266)",
    responses=_RESPONSES_ADMIN_COLLECTION,
    dependencies=[_EDIT],
    openapi_extra=_ADMIN_FILTER_OPENAPI,
)
def search_any_status(
    session: SessionDep,
    filters: AdminFiltersDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            description=(
                "Free text, or a SNOMED CT code - identical matching and ranking to "
                "`GET /catalogue/search` (FR-14, FR-15), over entries of any status."
            ),
        ),
    ],
    limit: LimitQuery = 50,
    after: CursorQuery = None,
) -> SearchPage:
    """The `catalogue.edit_published`-gated counterpart to `catalogue.py`'s
    public `search`: identical ranking, threshold and keyset paging
    (`nptc.catalogue.search`, called with `statuses=maintenance.
    MAINTENANCE_STATUSES` instead of the default `PUBLIC_STATUSES`), so a
    draft, deprecated or withdrawn entry is findable by an administrator the
    same way an active one is findable by anyone.

    `facets` is derived the same way `GET /catalogue/search`'s own is, over
    the same `?filter.*` parameters - including `status`, whose bucket list
    is non-degenerate here (every status an administrator might filter by),
    unlike the public surface's single-bucket `active` facet.
    """
    page = search.search_entries(
        session,
        q=q,
        limit=limit,
        after=after,
        filters=filters.selections,
        statuses=maintenance.MAINTENANCE_STATUSES,
    )
    facets = search.search_facets(
        session,
        q=q,
        context=filters.context,
        filters=filters.selections,
        statuses=maintenance.MAINTENANCE_STATUSES,
    )
    open_findings = queries.open_finding_business_keys(
        session, (hit.business_key for hit in page.hits)
    )
    return SearchPage(
        items=[
            SearchHit(
                **entry_summary_fields(
                    hit.business_key,
                    hit.preferred_term,
                    preferred_term_length(hit.preferred_term),
                    hit.status,
                    hit.specimen_unconstrained,
                    hit.updated_at,
                    hit.business_key in open_findings,
                ),
                score=hit.score,
            )
            for hit in page.hits
        ],
        next_cursor=page.next_cursor,
        facets=[
            Facet(
                key=facet.key,
                label=facet.label,
                facetable=facet.facetable,
                truncated=facet.truncated,
                buckets=[
                    FacetBucket(value=bucket.value, label=bucket.label, count=bucket.count)
                    for bucket in facet.buckets
                ],
            )
            for facet in facets
        ],
    )


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
    has_open_finding = queries.has_open_finding(session, entry.business_key)
    return EntryDetail(
        **entry_summary_fields(
            entry.business_key,
            entry.preferred_term,
            entry.length,
            entry.status,
            entry.specimen_unconstrained,
            entry.updated_at,
            has_open_finding,
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
