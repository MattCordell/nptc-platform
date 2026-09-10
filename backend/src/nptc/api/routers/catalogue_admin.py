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

**Serves the identical `EntryDetail` shape the public detail route does**,
assembled from the same loaders and the same `nptc.catalogue.search`
ranking - an edit screen consuming this route today gets the same fields a
public consumer of the same entry, once published, would see, just with
every status in scope and `status` on the wire so a caller can tell a draft
from an active entry (issue #266's own acceptance criterion). See
`catalogue_shared.py`'s own docstring for why this model and its assembly
helpers live there rather than being duplicated here.

**The two collection routes serve `AdminEntryPage`/`AdminSearchPage`, not
`EntryPage`/`SearchPage`** (issue #267): the same shape as their public
counterparts, plus `row_version` per row. Defined in this module, not
`catalogue_shared.py` - see `AdminEntrySummary`'s own docstring for why.

**The two collection routes have no 404**, matching
`catalogue.py`'s own `PUBLIC_COLLECTION_ERROR_RESPONSES`: an unmatched
query or an empty catalogue is an empty page, not a missing resource.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_datatype_registry, get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    BusinessKeyPath,
    CursorQuery,
    EntryDetail,
    EntrySummary,
    Facet,
    FacetBucket,
    FilterRequest,
    LimitQuery,
    binding_from_row,
    designation_from_row,
    entry_summary_fields,
    filter_parameter,
    property_value_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import maintenance, queries, search
from nptc.catalogue.entries import load_entry_for_update
from nptc.catalogue.facets import load_facet_context, parse_filters
from nptc.catalogue.maintenance import SortName
from nptc.catalogue.term_hygiene import preferred_term_length
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

#: `GET /catalogue/admin/entries`' own 422 - it takes no `q`, so it cannot
#: produce a blank-search-query refusal the way the search route can (issue
#: #266 review: a shared response description that named the wrong cause for
#: a given route is worse than two short ones). Issue #287 adds the cursor/
#: sort mismatch case, mirroring the search route's own cursor-mismatch
#: wording below.
_RESPONSE_422_LISTING: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query parameter was unprocessable - a cursor this API did not issue "
        "(including one issued for a different `sort` or filter set), an unrecognised "
        "`sort` value, a `limit` outside its range, or a `filter.*` parameter naming a "
        "facet this endpoint does not offer, an operator the facet does not support, or "
        "a value the property cannot hold. A filter is never silently ignored."
    ),
}

#: `GET /catalogue/admin/search`'s own 422.
_RESPONSE_422_SEARCH: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query parameter was unprocessable - a blank search query, a cursor this "
        "API did not issue (including one issued for a different `q`, filter set, or "
        "status scope - a cursor from `GET /catalogue/search` is refused here, and "
        "vice versa), a `limit` outside its range, or a `filter.*` parameter naming a "
        "facet this endpoint does not offer, an operator the facet does not support, "
        "or a value the property cannot hold. A filter is never silently ignored."
    ),
}

#: The two all-status collection routes (issue #266). No 404, matching
#: `catalogue.py`'s own `PUBLIC_COLLECTION_ERROR_RESPONSES` - see the module
#: docstring.
_RESPONSES_ADMIN_LISTING: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _RESPONSE_422_LISTING,
}
_RESPONSES_ADMIN_SEARCH: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _RESPONSE_422_SEARCH,
}

#: `filter_parameter` (`catalogue_shared.py`) takes the search path so its
#: description points a caller at *this* surface's own facet-and-counts
#: route rather than the public one's (issue #266 review).
_ADMIN_FILTER_OPENAPI: Final[dict[str, Any]] = {
    "parameters": [filter_parameter("/catalogue/admin/search")]
}

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_EDIT = Depends(permission_dep(Permission.CATALOGUE_EDIT_PUBLISHED))


class AdminEntrySummary(EntrySummary):
    """`EntrySummary` plus FR-38's optimistic-locking token (issue #267).

    Defined here, not in `catalogue_shared.py`: that module is imported by
    the public router, and a field the public surface must never carry
    (`EntryDetail`'s own docstring explains why `EntrySummary` does not
    carry it) has to live somewhere the public router cannot reach by
    construction, not merely by convention. `test_api_public_response_
    hygiene.py` asserts the public listing/search routes still omit it.

    The bulk reclassify route (FR-39, #63) locks on `(business_key,
    expected_row_version)`; this is what lets its selection surface (this
    issue) read a `row_version` per row instead of re-reading the entry
    once selected.
    """

    model_config = ConfigDict(frozen=True)

    row_version: int


class AdminEntryPage(BaseModel):
    """The admin counterpart to `catalogue_shared.EntryPage` (issue #267) -
    same shape, rows that additionally carry `row_version`. A standalone
    model rather than a subclass of `EntryPage`: overriding `items`' element
    type in a subclass is the field-covariance trap `mypy --strict` (and
    Liskov substitution generally) flags on a model a caller might still
    pass around as the parent type."""

    model_config = ConfigDict(frozen=True)

    items: list[AdminEntrySummary]
    next_cursor: str | None


class AdminSearchHit(AdminEntrySummary):
    """`AdminEntrySummary` plus a relevance score - the admin counterpart to
    `catalogue_shared.SearchHit`, matching that model's own field for field
    (issue #267)."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(description="Trigram similarity against `q`, between 0 and 1.")


class AdminSearchPage(BaseModel):
    """The admin counterpart to `catalogue_shared.SearchPage` (issue #267) -
    see `AdminEntryPage`'s own docstring for why this is a standalone model
    rather than a subclass."""

    model_config = ConfigDict(frozen=True)

    items: list[AdminSearchHit]
    next_cursor: str | None
    facets: list[Facet] = Field(
        description=(
            "Every facet available for this search, with counts over the whole "
            "result set rather than this page. A facet's own selection is "
            "excluded from its own counts, so a bucket you have not chosen "
            "still tells you how many entries it would give you."
        )
    )


def _admin_summary_from_row(
    row: maintenance.ListingRow, has_open_finding: bool
) -> AdminEntrySummary:
    """`summary_from_entry`'s admin counterpart, over a `maintenance.
    ListingRow` rather than a mapped `CatalogueEntry` (issue #287's sort
    made the listing statement select explicit columns, matching
    `nptc.catalogue.search.SearchHit`'s own precedent - see that module's
    docstring)."""
    return AdminEntrySummary(
        **entry_summary_fields(
            row.business_key,
            row.preferred_term,
            preferred_term_length(row.preferred_term),
            row.status,
            row.specimen_unconstrained,
            row.updated_at,
            has_open_finding,
        ),
        row_version=row.row_version,
    )


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

#: `?sort=` (issue #287). A `Literal`, not a hand-validated free string - see
#: `nptc.catalogue.maintenance.SortName`'s own docstring for why: FastAPI
#: puts the enum in `docs/api/openapi.json` for free and 422s an
#: unrecognised value before this handler ever runs.
SortQuery = Annotated[
    SortName,
    Query(
        description=(
            "How to order the page: `business_key` (the default, and the pre-#287 "
            "behaviour), `preferred_term`, `updated_at`, or `status`. Changing `sort` "
            "invalidates any `after` cursor from a different sort - pass `after=null` "
            "(omit it) when changing sort, matching a changed filter set."
        )
    ),
]

#: `GET /catalogue/admin/entries`'s own cursor type (issue #287) - forked
#: from `catalogue_shared.EntryCursorQuery`, which stays a bare
#: `business_key`-shaped string for the *public* `/catalogue/entries` route,
#: unchanged. This route's cursor is no longer always a `business_key` - it
#: is `"<sort value>:<digest>:<business key>"` for every `sort`, including
#: `business_key` itself (see `nptc.catalogue.maintenance`'s own module
#: docstring on why one shape is used uniformly) - so it is validated by
#: `nptc.catalogue.maintenance.list_entries_any_status` instead of by a
#: `Query(pattern=...)`, the same division of labour
#: `nptc.catalogue.search`'s `CursorQuery` already uses for its own
#: non-business-key cursor shape.
AdminEntryCursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "The `next_cursor` from the previous page. Opaque: pass it back "
            "unmodified, and do not construct one. It is bound to `sort` and the "
            "filter set - sending it back after changing either is a 422, not a "
            "meaningless page, because the keyset ordering means nothing against a "
            "different request."
        )
    ),
]


@router.get(
    "/admin/entries",
    summary="One page of catalogue entries, any status (issue #266)",
    responses=_RESPONSES_ADMIN_LISTING,
    dependencies=[_EDIT],
    openapi_extra=_ADMIN_FILTER_OPENAPI,
)
def list_entries_any_status(
    session: SessionDep,
    filters: AdminFiltersDep,
    sort: SortQuery = "business_key",
    limit: LimitQuery = 50,
    after: AdminEntryCursorQuery = None,
) -> AdminEntryPage:
    """The `catalogue.edit_published`-gated counterpart to `catalogue.py`'s
    public `list_entries`: keyset paging on `sort` then `business_key`
    (issue #287; `business_key` alone before it), every status in scope
    rather than `PUBLIC_STATUSES` alone, and `status` on each row so a
    caller can tell a draft from an active entry.

    `filter.*` parameters behave as they do on the public surface, except
    `?filter.status=` now accepts any `CatalogueEntryStatus` value rather
    than only `active` - `AdminFiltersDep` builds its facet context from
    `maintenance.MAINTENANCE_STATUSES`. Facets are not returned here for the
    same reason they are not on `/catalogue/entries`: `GET
    /catalogue/admin/search` is where the facet list with counts lives.

    Rows carry `row_version` (issue #267) - `AdminEntryPage`, not the public
    `EntryPage` - so the maintenance list screen's selection surface can
    carry FR-38's optimistic-locking token per row without a second read.
    """
    page = maintenance.list_entries_any_status(
        session, sort=sort, limit=limit, after=after, filters=filters.selections
    )
    open_findings = queries.open_finding_business_keys(
        session, (row.business_key for row in page.rows)
    )
    return AdminEntryPage(
        items=[
            _admin_summary_from_row(row, row.business_key in open_findings) for row in page.rows
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/admin/search",
    summary="Search catalogue entries by term, any status (issue #266)",
    responses=_RESPONSES_ADMIN_SEARCH,
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
) -> AdminSearchPage:
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

    Hits carry `row_version` (issue #267) - `nptc.catalogue.search.SearchHit`
    reads it straight off `scored`'s own join to `catalogue_entry`, see that
    module's docstring - so `AdminSearchHit`, not the public `SearchHit`,
    carries it onto the wire here.
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
    return AdminSearchPage(
        items=[
            AdminSearchHit(
                **entry_summary_fields(
                    hit.business_key,
                    hit.preferred_term,
                    preferred_term_length(hit.preferred_term),
                    hit.status,
                    hit.specimen_unconstrained,
                    hit.updated_at,
                    hit.business_key in open_findings,
                ),
                row_version=hit.row_version,
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
    public `read_entry`: no status filter on the entry itself, and - unlike
    the public route - `designations` includes retired rows too (issue
    #239). The other two sub-resources were already unfiltered here before
    this issue: `bindings` publishes retired rows on both routes (FR-08),
    and `properties` has no per-row status of its own. An edit screen (#149)
    calls this to load a `draft` entry's current state before the #224
    write routes save changes to it."""
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
            designation_from_row(row)
            for row in queries.load_designations_any_status(session, entry_ids)
        ],
        bindings=[binding_from_row(row) for row in queries.load_bindings(session, entry_ids)],
        properties=[
            property_value_from_row(row, registry)
            for row in queries.load_property_values(session, entry_ids)
        ],
    )
