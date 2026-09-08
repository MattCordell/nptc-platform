"""The public, unauthenticated read API over the approved catalogue
(issue #142, FR-20).

FR-20's audience is LIS and PMS vendors, not this platform's own SPA, so
this module is a *contract* first: `docs/api/openapi.json` is generated from
the response models below and committed (`backend/tests/
test_openapi_document.py`), and `docs/architecture/public-api.md` documents
the paging and status rules a vendor has to build against.

Following `routers/auth.py` exactly: response models declared here in the
router module, `ConfigDict(frozen=True)`, the return-type annotation drives
the response model (never a `response_model=` argument that can drift from
the annotation), and module-level error-responses dicts so the generated
document carries the refusals as well as the happy path. Three dicts rather
than one shared one: a route must declare the statuses it can actually
produce and no others, because #147 generates a client from this document
and a status that never occurs is a branch that can never be tested.

**Every route requires `Permission.CATALOGUE_BROWSE`, and that is not
theatre.** `Role.ANON` holds it, so an anonymous caller gets a 200 - which
is FR-20's whole point. What the dependency buys is that the check is a
permission check (FR-44), so the day a deployment decides the public API
needs a credential, it is a change to the permission matrix rather than a
new `if` in six route bodies. A *bad* token still 401s, because
`current_principal` raises rather than degrading to anonymous.

**What is deliberately absent from every response model here.** No `id`, no
`entry_id`, no `*_binding_id`. `business_key` is the only
identifier a caller ever sees (PRD SS6.2), the same boundary
`nptc.auth.identity.UserRef` draws around `app_user.id`.

`nptc.catalogue.queries` resolves `replaced_by_binding_id` to the
successor's code before this module ever sees a row, so there is no
internal id in scope here to leak by accident.
`backend/tests/test_api_public_response_hygiene.py` asserts this over the
raw response *text* of every route under this prefix, whole-body, rather
than field by field on a parsed model - the point being to catch a field
nobody thought to write an assertion for.

The one thing this rule does *not* exclude is `EntryDetail.row_version`
(issue #227). The ban is on internal **identifiers** - values that name a
row, and so let a caller address or correlate one outside the public
vocabulary. FR-38's version counter names nothing; it is opaque to a
read-only consumer and meaningful only as the value handed straight back on
the next write. It is on `EntryDetail` and not `EntrySummary`, so it appears
on the two detail routes rather than on every row of every page - see that
model's own field comment. Keeping it on the shared `EntryDetail` rather
than an admin-only subclass preserves issue #228's invariant that the public
and admin detail routes serve one shape.

**Codes are strings (FR-06).** `Binding.code` is `str`, and so is every
value that reaches it. This is the defect class the platform exists to
eliminate, and the same hygiene test asserts no unquoted six-or-more-digit
number appears anywhere in any body.

**No `switch` on datatype (FR-77, ADR-0013).** A property value is rendered
by `registry.get(row.datatype).serialise(...)` and nothing else - no
`match`, no datatype literal, no dict keyed on datatype. The handler
package is the only place that dispatch is allowed to exist
(`backend/tests/test_datatype_dispatch.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_datatype_registry, get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    BindingList,
    BusinessKeyPath,
    CodePath,
    CursorQuery,
    DesignationList,
    EntryCursorQuery,
    EntryDetail,
    EntryPage,
    Facet,
    FacetBucket,
    FilterRequest,
    LimitQuery,
    PropertyValue,
    SearchHit,
    SearchPage,
    SystemTokenPath,
    binding_from_row,
    build_entry_detail,
    designation_from_row,
    entry_summary_fields,
    filter_parameter,
    property_value_from_row,
    summary_from_entry,
)
from nptc.auth.permissions import Permission
from nptc.auth.principal import Principal
from nptc.catalogue import code_systems, history, queries
from nptc.catalogue.facets import load_facet_context, parse_filters
from nptc.catalogue.search import search_entries, search_facets
from nptc.catalogue.term_hygiene import preferred_term_length
from nptc.registry.handlers import DatatypeRegistry

router = APIRouter(prefix="/catalogue", tags=["catalogue"])

#: 401 is here even though these endpoints are public: presenting an
#: *unreadable* credential is refused rather than downgraded to the
#: anonymous view, so a client that models only 200 and 404 will mis-handle
#: its own expired token.
_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A credential was presented and could not be verified. Sending no "
        "credential at all is not an error on these endpoints."
    ),
}

_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "No entry in the published catalogue has this business key. An entry "
        "that exists but is not published (draft, deprecated or withdrawn) is "
        "reported identically, on purpose - a distinguishable response would "
        "confirm the key exists."
    ),
}

_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query or path parameter was unprocessable - a business key that is "
        "not `NPTC-nnnnnn`, a blank search query, a cursor this API did not "
        "issue (including one issued for a different `q` or a different filter "
        "set), a `limit` outside its range, or a `filter.*` parameter naming a "
        "facet this endpoint does not offer, an operator the facet does not "
        "support, or a value the property cannot hold. A filter is never "
        "silently ignored."
    ),
}

#: The collection routes. Deliberately no 404: neither `/catalogue/entries`
#: nor `/catalogue/search` can produce one - an unmatched query is an empty
#: page, not a missing resource - and a documented status that never occurs
#: gives #147's generated client a branch it can never exercise.
PUBLIC_COLLECTION_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    422: _RESPONSE_422,
}

#: The by-business-key routes, where a 404 *is* reachable.
PUBLIC_ENTRY_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    404: _RESPONSE_404,
    422: _RESPONSE_422,
}

#: FR-17, issue #140: the exact-code lookup routes' own 404 - distinct
#: from `_RESPONSE_404` above because the cause it describes is different
#: (an unregistered system_token/URI, or a registered one matching no
#: published entry, both on the identical fixed sentence - see
#: `nptc.catalogue.code_systems`'s own module docstring).
_RESPONSE_404_CODE_LOOKUP: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "No published entry matches this system and code - because the "
        "system_token (or, on `/lookup`, the system URI) is not registered, "
        "or because no active or retired binding for this code names a "
        "published entry. Both causes return the identical fixed sentence, "
        "which names each registered system as both its token and its URI, "
        "so a caller cannot use response text to tell them apart."
    ),
}

#: The two exact-code lookup routes. Same FR-98/issue #144 reasoning as
#: `PUBLIC_ENTRY_ERROR_RESPONSES` above (the by-business-key routes that
#: also serve bindings, which have no dedicated constant of their own -
#: FR-98/issue #144 removed the read path's only `display_term` rendering
#: call site, leaving nothing on either route that can 500 this way, so
#: there is no longer a real difference for a second constant to name):
#: no 500, nothing on this read path renders a `display_term` any more.
PUBLIC_CODE_LOOKUP_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    404: _RESPONSE_404_CODE_LOOKUP,
    422: _RESPONSE_422,
}

#: `/catalogue/entries/{business_key}/history` pages on the audit log's own
#: `sequence` - a globally monotonic identity column, so a plain digit
#: string makes a total order with no possible tie. Exclusive: the next
#: page is every event *older* than this one (the endpoint serves most
#: recent first).
#:
#: `max_length=19`: `AuditEvent.sequence` is `BigInteger` (signed 64-bit,
#: max `9223372036854775807`, 19 digits) - bounding the digit count here is
#: what stops a pathologically long cursor from ever reaching `int(before)`
#: in `read_history` (PR #278 review). Not every 19-digit string is itself
#: in range (`9999999999999999999` is not); `history.load_history` raises
#: `MalformedHistoryCursorError` (422, matching `MalformedSearchCursorError`'s
#: own precedent) for a value that survives this bound but is still too
#: large - this bound only rules out the unbounded case.
HistoryCursorQuery = Annotated[
    str | None,
    Query(
        pattern=r"^[0-9]+$",
        max_length=19,
        description=(
            "The `next_cursor` from the previous page. Pass it back unmodified, "
            "and do not construct one."
        ),
    ),
]

#: Both collection routes accept filters; only `/catalogue/search` returns
#: facets (ADR-0032).
_FILTER_OPENAPI: Final[dict[str, Any]] = {"parameters": [filter_parameter("/catalogue/search")]}


def _filter_request(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    registry: Annotated[DatatypeRegistry, Depends(get_datatype_registry)],
) -> FilterRequest:
    """Reads `filter.*` straight off the query string.

    `request.query_params.multi_items()` rather than `.items()`: the
    latter keeps only the last value of a repeated key, which would turn
    `?filter.discipline=chemistry&filter.discipline=haematology` into a
    search for haematology alone - a wrong answer with nothing in the
    response to reveal it.

    The facet list is loaded per request and never cached. That is not an
    oversight to optimise away later: a cached list is precisely the
    "needs a restart before the new facet appears" behaviour FR-09 exists
    to forbid, and `test_api_public_search.py` flips the flag through the
    real registry route on a running app to prove it.
    """
    context = load_facet_context(session, registry, status_values=queries.PUBLIC_STATUSES)
    return FilterRequest(
        context=context,
        selections=parse_filters(request.query_params.multi_items(), context),
    )


FiltersDep = Annotated[FilterRequest, Depends(_filter_request)]


class PropertyList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[PropertyValue]


class HistoryEvent(BaseModel):
    """One change to the entry or one of its designations, code bindings
    or property values (FR-19).

    Never the raw diff: `changed_fields` names what changed, not the
    values themselves - a withheld field's name still appears (that a
    field changed is not the secret), but no value from any audit event
    is ever serialised here, changed or not.
    """

    model_config = ConfigDict(frozen=True)

    occurred_at: datetime
    action: str = Field(description="The internal action name, e.g. `catalogue_entry.updated`.")
    changed_by: str | None = Field(
        description="The administrator's display name, or `null` for a system-initiated "
        "change, an account since pseudonymised on closure, or an anonymous caller "
        "(PR #278 review, NFR-26) - sign in to see who made a change."
    )
    changed_fields: list[str] = Field(description="Which fields changed at this event.")
    note: str | None = Field(description="The changelog note supplied for this write (FR-37).")
    release: None = Field(
        default=None,
        description="Always `null` in P1 - the defined slot P4's release membership fills "
        "once releases exist (FR-19).",
    )


class HistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[HistoryEvent]
    next_cursor: str | None


# --- routes ---------------------------------------------------------------
#
# `entry_summary_fields`, `property_value_from_row` and `summary_from_entry`
# live in `catalogue_shared.py` (issues #228, #266) - `catalogue_admin.py`
# needs them too, and duplicating them here would let the routers' shapes
# drift apart by accident.

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_BROWSE = Depends(permission_dep(Permission.CATALOGUE_BROWSE))


@router.get(
    "/entries",
    summary="One page of published catalogue entries",
    responses=PUBLIC_COLLECTION_ERROR_RESPONSES,
    dependencies=[_BROWSE],
    openapi_extra=_FILTER_OPENAPI,
)
def list_entries(
    session: SessionDep,
    filters: FiltersDep,
    limit: LimitQuery = 50,
    after: EntryCursorQuery = None,
) -> EntryPage:
    """Keyset paging on `business_key`, ascending. Pass the response's
    `next_cursor` back as `after` for the following page; a `null`
    `next_cursor` means there is no following page.

    There is no `offset` and no total count, on purpose (ADR-0024): an
    offset re-reads and re-skips every earlier row on every page, and drops
    or repeats rows outright when a concurrent insert shifts the window
    mid-scan.

    `filter.*` parameters are accepted here and behave exactly as they do on
    `/catalogue/search`. Facets are **not** returned: computing counts on
    every page of a browse costs something no caller has asked for, and
    `GET /catalogue/search` is where the facet list with counts lives
    (ADR-0032).
    """
    page = queries.list_entries(session, limit=limit, after=after, filters=filters.selections)
    open_findings = queries.open_finding_business_keys(
        session, (entry.business_key for entry in page.entries)
    )
    return EntryPage(
        items=[
            summary_from_entry(entry, entry.business_key in open_findings) for entry in page.entries
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/search",
    summary="Search the published catalogue by term",
    responses=PUBLIC_COLLECTION_ERROR_RESPONSES,
    dependencies=[_BROWSE],
    openapi_extra=_FILTER_OPENAPI,
)
def search(
    session: SessionDep,
    filters: FiltersDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            description=(
                "Free text, or a SNOMED CT code. Matched against the entry's "
                "preferred term, its active synonyms, and the fully specified "
                "name, AU preferred term and code of its active binding "
                "(FR-14) - insensitive to case and to diacritics, tolerant of "
                "typographical error and of word order (FR-15). A code is "
                "matched exactly; everything else is matched by similarity and "
                "by full-text search, whichever scores the entry higher. The "
                "full-text half accepts web-search syntax - a quoted phrase, "
                "`or` between alternatives, and a leading `-` to exclude a "
                "word - which applies to that half only. A query that can be "
                "satisfied by exclusion alone is dropped from that half rather "
                "than matching the whole catalogue, and is still searched by "
                "similarity as the literal text it is."
            ),
        ),
    ],
    limit: LimitQuery = 50,
    after: CursorQuery = None,
) -> SearchPage:
    """Best match first, ties broken by `business_key` so the order is
    total and paging cannot drop or repeat a row inside a tie.

    A query below the similarity threshold returns an empty page rather
    than a broadened match. That is the intended behaviour: a search that
    quietly matches everything is worse than one that matches nothing,
    because the caller cannot tell it from a working search over a catalogue
    that genuinely has nothing to offer.

    `facets` is the facet list for this search, with counts over the whole
    result set. It is derived from the property registry on every request
    (FR-16), so it is not a fixed set a client may hard-code: a property an
    administrator marks filterable appears here on the very next request.
    """
    page = search_entries(session, q=q, limit=limit, after=after, filters=filters.selections)
    facets = search_facets(session, q=q, context=filters.context, filters=filters.selections)
    open_findings = queries.open_finding_business_keys(
        session, (hit.business_key for hit in page.hits)
    )
    return SearchPage(
        items=[
            SearchHit(
                **entry_summary_fields(
                    hit.business_key,
                    hit.preferred_term,
                    # The same FR-85 computation `CatalogueEntry.length`
                    # applies, called directly because a search hit is a
                    # projection, not a loaded entity - never `len(...)`,
                    # which would disagree with the detail endpoint's own
                    # figure for a term carrying a non-breaking space.
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
    "/entries/{business_key}",
    summary="One published catalogue entry, with everything attached to it",
    responses=PUBLIC_ENTRY_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_entry(
    session: SessionDep,
    registry: RegistryDep,
    business_key: BusinessKeyPath,
) -> EntryDetail:
    entry = queries.get_entry(session, business_key)
    return build_entry_detail(session, registry, entry)


@router.get(
    "/code/{system_token}/{code}",
    summary="One published catalogue entry, resolved by an exact code (FR-17)",
    responses=PUBLIC_CODE_LOOKUP_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_entry_by_code(
    session: SessionDep,
    registry: RegistryDep,
    system_token: SystemTokenPath,
    code: CodePath,
) -> EntryDetail:
    """Resolves the same entry `GET /catalogue/entries/{business_key}` and
    `GET /catalogue/lookup?system=...&code=...` would for the same code -
    all three serve byte-identical bodies for one entry (FR-17's
    unambiguous-lookup acceptance criterion).

    `system_token` is a short alias registered in
    `nptc.catalogue.code_systems` - `sct` for `http://snomed.info/sct`
    today. `code` is never validated against a code shape here: an
    unrecognised code and a malformed one both resolve to nothing and get
    the identical 404 (see `PUBLIC_CODE_LOOKUP_ERROR_RESPONSES`)."""
    system = code_systems.system_for_token(system_token)
    entry = queries.get_entry_by_code(session, system, code)
    return build_entry_detail(session, registry, entry)


@router.get(
    "/lookup",
    summary="One published catalogue entry, resolved by system URI and exact code (FR-17)",
    responses=PUBLIC_CODE_LOOKUP_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_entry_by_system_and_code(
    session: SessionDep,
    registry: RegistryDep,
    system: Annotated[
        str,
        Query(
            min_length=1,
            description=(
                "The code system's full URI, e.g. `http://snomed.info/sct` "
                "(FR-17) - for a caller holding the URI rather than the "
                "short `system_token` alias `GET /catalogue/code/{system_token}"
                "/{code}` takes."
            ),
        ),
    ],
    code: Annotated[str, Query(min_length=1, description="The exact code to resolve.")],
) -> EntryDetail:
    """The full-URI sibling of `GET /catalogue/code/{system_token}/{code}` -
    see that route's docstring for the shared-body and shared-404
    guarantees. `system` must be one of `nptc.catalogue.code_systems.
    SYSTEM_TOKENS`' registered URIs; an unregistered one is a 404 on the
    identical sentence an unregistered `system_token` gets."""
    registered_system = code_systems.require_registered_system(system)
    entry = queries.get_entry_by_code(session, registered_system, code)
    return build_entry_detail(session, registry, entry)


@router.get(
    "/entries/{business_key}/designations",
    summary="An entry's active synonyms and non-en-AU preferred variants",
    responses=PUBLIC_ENTRY_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_designations(
    session: SessionDep,
    business_key: BusinessKeyPath,
) -> DesignationList:
    entry = queries.get_entry(session, business_key)
    return DesignationList(
        items=[designation_from_row(row) for row in queries.load_designations(session, (entry.id,))]
    )


@router.get(
    "/entries/{business_key}/bindings",
    summary="An entry's SNOMED CT code bindings, including retired ones",
    responses=PUBLIC_ENTRY_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_bindings(
    session: SessionDep,
    business_key: BusinessKeyPath,
) -> BindingList:
    """Retired bindings are included (FR-08): a client holding a code that
    has been inactivated learns so here, together with the reason and any
    successor code."""
    entry = queries.get_entry(session, business_key)
    return BindingList(
        items=[binding_from_row(row) for row in queries.load_bindings(session, (entry.id,))]
    )


@router.get(
    "/entries/{business_key}/properties",
    summary="An entry's recorded property values",
    responses=PUBLIC_ENTRY_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_properties(
    session: SessionDep,
    registry: RegistryDep,
    business_key: BusinessKeyPath,
) -> PropertyList:
    entry = queries.get_entry(session, business_key)
    return PropertyList(
        items=[
            property_value_from_row(row, registry)
            for row in queries.load_property_values(session, (entry.id,))
        ]
    )


@router.get(
    "/entries/{business_key}/history",
    summary="An entry's change history, most recent first (FR-19)",
    responses=PUBLIC_ENTRY_ERROR_RESPONSES,
)
def read_history(
    session: SessionDep,
    principal: Annotated[Principal, _BROWSE],
    business_key: BusinessKeyPath,
    limit: LimitQuery = 50,
    before: HistoryCursorQuery = None,
) -> HistoryPage:
    """Every audit event against this entry or one of its designations,
    code bindings or property values (FR-19) - what changed, when, who by
    (a display name, never an internal id), and the changelog note. Never
    empty-errors: an entry never edited since seeding returns `200` with
    an empty `items` list.

    `changed_by` is populated only for an authenticated caller (PR #278
    review, NFR-26): the endpoint itself stays fully public
    (`Permission.CATALOGUE_BROWSE`, held by `Role.ANON`), but an anonymous
    request gets `null` on every event regardless of who actually made the
    change - naming an identifiable RCPA-QAP staff member to anyone on the
    internet was never a considered part of ADR-0034's "history is public"
    argument. `principal` is captured here (rather than left in
    `dependencies=`, this route's own previous shape) specifically to read
    `principal.user_id`; every other route in this module has no use for
    the resolved principal itself.

    `release` is always `null` on every item in P1 - FR-19 asks for
    "every published release in which it appeared" too, and releases do
    not exist until P4. This is the defined slot P4 fills; it is not
    dropped from the shape in the meantime.
    """
    entry = queries.get_entry(session, business_key)
    page = history.load_history(
        session,
        entry,
        limit=limit,
        before=int(before) if before is not None else None,
        include_changed_by=principal.user_id is not None,
    )
    return HistoryPage(
        items=[
            HistoryEvent(
                occurred_at=event.occurred_at,
                action=event.action,
                changed_by=event.changed_by,
                changed_fields=list(event.changed_fields),
                note=event.note,
            )
            for event in page.events
        ],
        next_cursor=page.next_cursor,
    )
