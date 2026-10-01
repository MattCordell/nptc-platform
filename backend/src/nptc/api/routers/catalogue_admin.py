"""The authenticated admin read API over the catalogue, any status (FR-17, FR-36, FR-14,
FR-15, FR-16, FR-44).

The public detail route resolves through `nptc.catalogue.queries.get_entry`, which filters
to `PUBLIC_STATUSES`, so it 404s a `draft` exactly as it does a key never minted. That is
FR-20's deliberate contract. These routes give an edit screen an authenticated read of an
entry of any status. `docs/architecture/catalogue-write-api.md` describes them.

**A separate router from `catalogue.py`.** `test_api_public_status_filter.py` and
`test_api_public_response_hygiene.py` derive what they scan from that module's route
table. A permission-gated route there would need an exception in both guard tests. One URL
per audience also lets a reviewer permission-audit each route on its own.

**Gated on `Permission.CATALOGUE_EDIT_PUBLISHED`, not a new read permission.** The
audience that loads an entry to edit it is the audience that saves the edit, so it needs
one credential posture, MFA step-up included. A narrower `catalogue.read_unpublished` was
rejected: `test_permission_matrix.py` asserts `ROLE_PERMISSIONS` cell by cell against the
PRD table, so a new permission needs a PRD change.

**Entries resolve through `load_entry_for_update`, not a new query function.**
`nptc.catalogue.queries` applies `PUBLIC_STATUSES` as its only status filter, so an
unfiltered getter does not belong there. The collection routes follow the same rule
through `maintenance.list_entries_any_status`.

**The detail route serves the public `EntryDetail` shape**, assembled from the same
loaders and `nptc.catalogue.search` ranking, with `status` on the wire. See
`catalogue_shared.py` for why the model and its helpers live there. The two collection
routes serve `AdminEntryPage` and `AdminSearchPage`: the public shape plus `row_version`
per row (see `AdminEntrySummary`). Neither has a 404, matching `catalogue.py`'s
`PUBLIC_COLLECTION_ERROR_RESPONSES`: an unmatched query is an empty page.

**The length report counts entries of every status, on purpose.** The
`nptc.catalogue.length_report` module docstring gives the reason.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import ApiSettingsDep, get_datatype_registry, get_session, permission_dep
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
from nptc.catalogue.length_report import compute_length_distribution
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
#: The same generic wording as `catalogue.py`'s `_RESPONSE_404`: a business key
#: that was never minted is absent, not "found, but you can't have it".
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No catalogue entry, of any status, has this business key.",
}
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "The business key is not `NPTC-nnnnnn`.",
}
#: No 500: FR-98 removed the read path's only rendering call site, so nothing
#: here can fail that way.
_RESPONSES_ADMIN_READ: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    422: _RESPONSE_422,
}

#: `GET /catalogue/admin/preferred-term-length-distribution` (FR-87) takes no
#: parameter, so it cannot 404 or 422.
_RESPONSES_ADMIN_LENGTH_REPORT: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
}

#: `GET /catalogue/admin/entries`' own 422. It takes no `q`, so it has no
#: blank-query refusal; each route names only the causes it can produce.
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

#: The two all-status collection routes. No 404; see the module docstring.
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

#: `filter_parameter` takes the search path so its description points a caller at
#: this surface's own facet-and-counts route, not the public one.
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
    """The admin counterpart of `summary_from_entry`, over a `maintenance.ListingRow`
    because the listing statement selects explicit columns, not a mapped entity."""
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
    """`catalogue.py`'s `_filter_request`, scoped to `maintenance.MAINTENANCE_STATUSES`
    instead of `queries.PUBLIC_STATUSES`, so `?filter.status=draft` is a real filter here
    and not the empty page it gives on the public surface (see `_core_facets` in
    `nptc.catalogue.facets`).
    """
    context = load_facet_context(session, registry, status_values=maintenance.MAINTENANCE_STATUSES)
    return FilterRequest(
        context=context,
        selections=parse_filters(request.query_params.multi_items(), context),
    )


AdminFiltersDep = Annotated[FilterRequest, Depends(_admin_filter_request)]

#: `?sort=`. A `Literal`, so FastAPI publishes the enum in the OpenAPI document and
#: 422s an unrecognised value before the handler runs.
SortQuery = Annotated[
    SortName,
    Query(
        description=(
            "How to order the page: `business_key` (the default, and the pre-#287 "
            "behaviour), `preferred_term`, `updated_at`, or `status`. `status` orders by "
            "lifecycle (`draft`, `active`, `deprecated`, `withdrawn`), not alphabetically. "
            "Changing `sort` invalidates any `after` cursor from a different sort - pass "
            "`after=null` (omit it) when changing sort, matching a changed filter set."
        )
    ),
]

#: This route's cursor is `"<sort value>:<digest>:<business key>"` for every
#: `sort`, `business_key` included, so it is not the bare business-key string of
#: `catalogue_shared.EntryCursorQuery`. `maintenance.list_entries_any_status`
#: validates it rather than a `Query(pattern=...)`, as `CursorQuery` does for the
#: search cursor.
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
    settings: ApiSettingsDep,
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
        bindings=[
            binding_from_row(row, settings) for row in queries.load_bindings(session, entry_ids)
        ],
        properties=[
            property_value_from_row(row, registry)
            for row in queries.load_property_values(session, entry_ids)
        ],
    )


class LengthDistributionBucket(BaseModel):
    """Every entry whose preferred term is exactly `length` characters long,
    plus how many entries a maximum set to `length` would warn on (FR-86
    warns when a term's length *exceeds* the configured maximum, so this
    counts strictly greater - `nptc.catalogue.length_report.
    LengthBucket.entries_exceeding`'s own comparison).

    One shape carrying both figures, rather than two parallel lists a caller
    would have to zip back together by `length` themselves - FR-87 asks for
    exactly this pairing: "the count of entries affected at each candidate
    threshold", and every observed `length` is a candidate threshold."""

    model_config = ConfigDict(frozen=True)

    length: int
    count: int
    entries_exceeding: int


class LengthDistributionReport(BaseModel):
    """FR-87: the whole report an administrator needs to nominate a maximum
    preferred-term length (FR-86, PRD open item OI-1) - the histogram plus
    its maximum, with no query to run by hand."""

    model_config = ConfigDict(frozen=True)

    buckets: tuple[LengthDistributionBucket, ...]
    maximum: int | None = Field(
        description="The longest preferred term in the catalogue, or null when the catalogue is empty."
    )


@router.get(
    "/admin/preferred-term-length-distribution",
    summary="The distribution of preferred-term lengths across the catalogue",
    responses=_RESPONSES_ADMIN_LENGTH_REPORT,
    dependencies=[_EDIT],
)
def preferred_term_length_distribution(session: SessionDep) -> LengthDistributionReport:
    """FR-87. Reachable by an administrator with no query to write by hand -
    the acceptance criterion this route exists to satisfy.

    Gated on `Permission.CATALOGUE_EDIT_PUBLISHED`, the same permission every
    other route in this module uses, rather than a new read-only permission -
    see the module docstring for why: `ROLE_PERMISSIONS` is asserted
    cell-by-cell against the PRD's own table, so minting one would need a PRD
    change this issue does not ask for.

    One statement (`nptc.catalogue.length_report.compute_length_distribution`,
    issue #275's precedent) regardless of the catalogue's size - FR-87's own
    acceptance criterion that this runs against the 20,000-entry design
    ceiling without timing out.
    """
    distribution = compute_length_distribution(session)
    return LengthDistributionReport(
        buckets=tuple(
            LengthDistributionBucket(
                length=bucket.length,
                count=bucket.count,
                entries_exceeding=bucket.entries_exceeding,
            )
            for bucket in distribution.buckets
        ),
        maximum=distribution.maximum,
    )
