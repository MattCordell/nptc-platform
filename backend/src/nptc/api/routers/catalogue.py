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
`entry_id`, no `*_binding_id`, no `row_version`. `business_key` is the only
identifier a caller ever sees (PRD SS6.2), the same boundary
`nptc.auth.identity.UserRef` draws around `app_user.id`.
`nptc.catalogue.queries` resolves `replaced_by_binding_id` to the
successor's code before this module ever sees a row, so there is no
internal id in scope here to leak by accident.
`backend/tests/test_api_public_response_hygiene.py` asserts this over the
raw response *text* of every route under this prefix, whole-body, rather
than field by field on a parsed model - the point being to catch a field
nobody thought to write an assertion for.

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

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_datatype_registry, get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import (
    BindingList,
    BusinessKeyPath,
    DesignationList,
    EntryDetail,
    EntrySummary,
    PropertyValue,
    binding_from_row,
    designation_from_row,
    entry_summary_fields,
    property_value_from_row,
)
from nptc.auth.permissions import Permission
from nptc.catalogue import queries
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.catalogue.search import search_entries
from nptc.catalogue.term_hygiene import preferred_term_length
from nptc.db.models.catalogue_entry import CatalogueEntry
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
        "issue (including one issued for a different `q`), or a `limit` "
        "outside its range."
    ),
}

#: Only on the two routes that render a `display_term`, because only they
#: can raise it. A published binding whose stored FSN is not renderable
#: (FR-83) is a server-side *data* fault on a well-formed request, so it is
#: a 500 and is documented as one - a 422 would tell a vendor's client its
#: own request was wrong and stop it escalating.
_RESPONSE_500_DISPLAY_TERM: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A published code binding's stored Fully Specified Name is not in the form "
        "the terminology server serves, so its display term cannot be rendered. "
        "This is a data fault in the catalogue, not a fault in the request; it "
        "needs an administrator, and retrying will not clear it."
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

#: The by-business-key routes that also serve bindings.
PUBLIC_BINDING_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **PUBLIC_ENTRY_ERROR_RESPONSES,
    500: _RESPONSE_500_DISPLAY_TERM,
}

#: 200 is the documented default and 200 is also the ceiling on what one
#: response should carry; a caller wanting the whole catalogue pages through
#: it with the cursor rather than asking for it in one request.
LimitQuery = Annotated[
    int,
    Query(ge=1, le=200, description="Maximum entries in this page."),
]

#: `/catalogue/entries` pages on `business_key`, so its cursor *is* a
#: business key and is validated as one. Constrained rather than accepted
#: freely so a mangled cursor is a 422 here exactly as it is on
#: `/catalogue/search` - an endpoint that silently serves "the page after
#: whatever this sorts before" gives a client no way to notice it has been
#: corrupting its own cursor.
EntryCursorQuery = Annotated[
    str | None,
    Query(
        pattern=BUSINESS_KEY_PATTERN.pattern,
        description=(
            "The `next_cursor` from the previous page. Pass it back unmodified, "
            "and do not construct one."
        ),
    ),
]

#: `/catalogue/search` pages on `<score>:<query digest>:<business_key>`,
#: which has no single pattern worth expressing here -
#: `nptc.catalogue.search` parses it and raises `MalformedSearchCursorError`
#: (also a 422) for anything it did not mint, including a cursor it minted
#: for a different `q`.
CursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "The `next_cursor` from the previous page. Opaque: pass it back "
            "unmodified, and do not construct one. It is bound to the `q` it was "
            "issued for - sending it with a different `q` is a 422, not a "
            "meaningless page."
        )
    ),
]


class EntryPage(BaseModel):
    """`next_cursor` is `null` on the last page - which is the *only*
    reliable signal that paging is finished. A client must not infer the end
    from a short page: a page can be short and still have a successor."""

    model_config = ConfigDict(frozen=True)

    items: list[EntrySummary]
    next_cursor: str | None


class PropertyList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[PropertyValue]


class SearchHit(EntrySummary):
    """A summary plus its relevance score.

    The score is exposed because it is what the ordering is, and a client
    that cannot see it cannot tell a confident single match from a page of
    weak ones. It is comparable *within* one response only - it is a
    trigram similarity against this particular query, not a quality rating
    of the entry.
    """

    model_config = ConfigDict(frozen=True)

    score: float = Field(description="Trigram similarity against `q`, between 0 and 1.")


class SearchPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[SearchHit]
    next_cursor: str | None


# --- assembling the response models from query rows -----------------------
#
# `entry_summary_fields` and `property_value_from_row` live in
# `catalogue_shared.py` now (issue #228) - `catalogue_admin.py`'s detail
# route needs them too, and duplicating them here would let the two detail
# routes' shapes drift apart by accident.


def _summary(entry: CatalogueEntry) -> EntrySummary:
    return EntrySummary(
        **entry_summary_fields(
            entry.business_key,
            entry.preferred_term,
            entry.length,
            entry.status,
            entry.specimen_unconstrained,
            entry.updated_at,
        )
    )


# --- routes ---------------------------------------------------------------

SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[DatatypeRegistry, Depends(get_datatype_registry)]
_BROWSE = Depends(permission_dep(Permission.CATALOGUE_BROWSE))


@router.get(
    "/entries",
    summary="One page of published catalogue entries",
    responses=PUBLIC_COLLECTION_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def list_entries(
    session: SessionDep,
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
    """
    page = queries.list_entries(session, limit=limit, after=after)
    return EntryPage(
        items=[_summary(entry) for entry in page.entries],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/search",
    summary="Search the published catalogue by term",
    responses=PUBLIC_COLLECTION_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def search(
    session: SessionDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            description=(
                "Free text. Matched against each entry's preferred term and its "
                "active synonyms, insensitive to case and to diacritics, and "
                "tolerant of typographical error (FR-15)."
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
    """
    page = search_entries(session, q=q, limit=limit, after=after)
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
                ),
                score=hit.score,
            )
            for hit in page.hits
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/entries/{business_key}",
    summary="One published catalogue entry, with everything attached to it",
    responses=PUBLIC_BINDING_ERROR_RESPONSES,
    dependencies=[_BROWSE],
)
def read_entry(
    session: SessionDep,
    registry: RegistryDep,
    business_key: BusinessKeyPath,
) -> EntryDetail:
    entry = queries.get_entry(session, business_key)
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
        designations=[
            designation_from_row(row) for row in queries.load_designations(session, entry_ids)
        ],
        bindings=[binding_from_row(row) for row in queries.load_bindings(session, entry_ids)],
        properties=[
            property_value_from_row(row, registry)
            for row in queries.load_property_values(session, entry_ids)
        ],
    )


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
    responses=PUBLIC_BINDING_ERROR_RESPONSES,
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
