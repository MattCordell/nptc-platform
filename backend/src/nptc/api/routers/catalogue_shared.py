"""Response models and helpers shared between the public read router
(`routers/catalogue.py`, FR-20), the authenticated admin read router
(`routers/catalogue_admin.py`, issue #228), and the authenticated write
routers (`routers/catalogue_bindings.py`, issue #219;
`routers/catalogue_designations.py`, issue #224).

Every write router deliberately stays a separate module -
`catalogue.py`'s own docstring explains why a POST cannot fold into the
public surface, and `catalogue_admin.py`'s own docstring explains the same
for its status-unfiltered GET - but a row written or read by one has to
come back out looking exactly like the same row read by another, so the
response models and their row-to-model assemblers live here rather than
being duplicated or imported private-to-private between routers.

`EntrySummary`/`PropertyValue`/`EntryDetail` and their assembly helpers
moved here from `catalogue.py` when `catalogue_admin.py` was added (issue
#228): `catalogue_admin.py`'s detail route serves the identical shape
`catalogue.py`'s own detail route does, and reaching into another router's
private helpers is exactly what this module exists to avoid.

`EntryPage`/`SearchHit`/`SearchPage`/`Facet`/`FacetBucket` moved here for
the identical reason when `catalogue_admin.py` grew its own all-status
listing and search (issue #266): `GET /catalogue/admin/entries` and
`GET /catalogue/admin/search` serve the same page/hit/facet shapes their
public counterparts do, just over a different status scope, so one set of
models rather than two that could drift. Model *class names* are unchanged
by the move, so the generated `docs/api/openapi.json` component names are
unaffected - only their import path changed.

`LimitQuery`/`EntryCursorQuery`/`CursorQuery`/`FilterRequest`/
`filter_parameter` moved here in the same PR's review pass, for the same
reason and not a different one: `catalogue_admin.py` had started importing
these five names directly from `catalogue.py` (and re-declaring
`_summary`/its own filter-openapi constant byte-for-byte) to serve its two
new collection routes, which is exactly the router-to-router coupling this
module's own paragraph above says the shared response models exist to
avoid - a query-parameter type is no different from a response model in
that respect. `summary_from_entry` is the same move for the one assembly
helper (`EntrySummary` from a `CatalogueEntry` row) both routers' listing
routes need and neither had a shared name for.

**`binding_from_row`/`designation_from_row`/`entry_summary_fields`/
`property_value_from_row` carry no leading underscore, unlike every other
free function in this module.** They are this module's actual
cross-router contract - imported by all three of `catalogue.py`,
`catalogue_admin.py` and (the designation one) `catalogue_designations.py`
- so a leading underscore on them would misrepresent an intentional,
`__all__`-listed API as a private implementation detail a future reader
might "clean up" by inlining.

**No `display_term`, and no strip anywhere in this module (FR-83, FR-98,
issue #144).** `Binding.fsn` is served exactly as stored - FR-82's
as-served guarantee - and `Binding.label_provenance` declares that fact
instead of a second, silently-derived copy of the label. FR-83's one
sanctioned renderer, `nptc.exports.semantic_tag.render_display_term`, is
reached only from the export surface; this module used to be its one
allowlisted read-path consumer and no longer is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_api_settings
from nptc.api.labels import (
    AU_PREFERRED_TERM_PROVENANCE,
    PREFERRED_VARIANT_PROVENANCE,
    SYNONYM_PROVENANCE,
    LabelProvenance,
    fsn_provenance,
)
from nptc.catalogue import queries
from nptc.catalogue.code_systems import SYSTEM_TOKEN_PATTERN
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.catalogue.facets import (
    FACET_BUCKET_CAP,
    FILTER_OP_SEPARATOR,
    FILTER_PARAM_PREFIX,
    FILTER_VALUE_CAP,
    FacetContext,
    FilterSelection,
)
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.registry.handlers import DatatypeRegistry, SerialisationTarget

__all__ = [
    "Binding",
    "BindingList",
    "BusinessKeyPath",
    "CodePath",
    "CursorQuery",
    "Designation",
    "DesignationList",
    "EntryCursorQuery",
    "EntryDetail",
    "EntryPage",
    "EntrySummary",
    "Facet",
    "FacetBucket",
    "FilterRequest",
    "LimitQuery",
    "PropertyValue",
    "SearchHit",
    "SearchPage",
    "SystemTokenPath",
    "binding_from_row",
    "build_entry_detail",
    "designation_from_row",
    "entry_summary_fields",
    "filter_parameter",
    "property_value_from_row",
    "summary_from_entry",
]

#: Shared by every route addressing an entry by its public identifier. A
#: business key that is not `NPTC-` plus at least six digits (FR-03) is a
#: 422 here, before any query runs.
BusinessKeyPath = Annotated[
    str,
    Path(
        pattern=BUSINESS_KEY_PATTERN.pattern,
        description="The entry's public identifier, e.g. `NPTC-000247` (FR-03).",
        examples=["NPTC-000247"],
    ),
]

#: FR-17, issue #140: the short alias half of `GET /catalogue/code/
#: {system_token}/{code}`. A malformed token is a 422 here, before any
#: query runs; a well-formed but unregistered one reaches
#: `nptc.catalogue.code_systems.system_for_token` and is a 404 instead - see
#: that module's own docstring for why the two are different status codes.
SystemTokenPath = Annotated[
    str,
    Path(
        pattern=SYSTEM_TOKEN_PATTERN.pattern,
        description=(
            "A short alias for a code system's URI, e.g. `sct` for "
            "`http://snomed.info/sct` (FR-17). An unregistered but "
            "well-formed token is a 404, not a 422 - see "
            "`docs/architecture/public-api.md`."
        ),
        examples=["sct"],
    ),
]

#: FR-17, issue #140: the exact code to resolve on `GET /catalogue/code/
#: {system_token}/{code}`. Deliberately no `pattern=` - `docs/adr/
#: 0033-exact-code-lookup-routes.md` records why `code` is not
#: shape-validated at the API layer; an unrecognised code and a malformed
#: one both resolve to nothing and get the identical 404.
CodePath = Annotated[
    str,
    Path(description="The exact code to resolve.", examples=["49466006"]),
]

#: 200 is the documented default and 200 is also the ceiling on what one
#: response should carry; a caller wanting the whole catalogue pages through
#: it with the cursor rather than asking for it in one request. Shared by
#: every collection route, public or admin (issue #266).
LimitQuery = Annotated[
    int,
    Query(ge=1, le=200, description="Maximum entries in this page."),
]

#: `/catalogue/entries` and `/catalogue/admin/entries` (issue #266) both page
#: on `business_key`, so this cursor *is* a business key and is validated as
#: one. Constrained rather than accepted freely so a mangled cursor is a 422
#: here exactly as it is on the search routes - an endpoint that silently
#: serves "the page after whatever this sorts before" gives a client no way
#: to notice it has been corrupting its own cursor.
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

#: `/catalogue/search` and `/catalogue/admin/search` (issue #266) both page
#: on `<score>:<request digest>:<business_key>`, which has no single pattern
#: worth expressing here - `nptc.catalogue.search` parses it and raises
#: `MalformedSearchCursorError` (also a 422) for anything it did not mint,
#: including a cursor it minted for a different `q`, filter set, or status
#: scope (see that module's own docstring).
CursorQuery = Annotated[
    str | None,
    Query(
        description=(
            "The `next_cursor` from the previous page. Opaque: pass it back "
            "unmodified, and do not construct one. It is bound to the `q`, the "
            "filters, and this endpoint's own status scope - sending it with any "
            "changed (including replaying it against the other collection route) "
            "is a 422, not a meaningless page, because a relevance score means "
            "nothing against a different request."
        )
    ),
]


def filter_parameter(search_path: str) -> dict[str, Any]:
    """The FR-16 filter query parameter, declared by hand (issue #139,
    ADR-0032) - shared because `catalogue.py` and `catalogue_admin.py`
    (issue #266) both accept `?filter.<key>=<value>` on their collection
    routes.

    **Why by hand.** The parameter name is not fixed - it is
    `filter.<property_key>` for whatever properties an administrator has
    marked `filterable`, which is the whole point of FR-16 - and FastAPI
    generates parameters from a typed signature, which by construction can
    only name parameters known when this file is written. Reading the query
    string directly (each router's own `_filter_request`) is the only way to
    accept the shape; declaring it here is what stops `docs/api/openapi.json`
    from quietly omitting a parameter the API does in fact accept. FR-20
    makes that document the contract vendors build against, and the
    `breaking` CI job polices every later change to it - a parameter absent
    from the document is a parameter nobody is protecting.

    `search_path` names *this collection's own* search route -
    `/catalogue/search` for the public surface, `/catalogue/admin/search`
    for the maintenance one - so the description below points a caller at
    the endpoint that actually returns this facet's current bucket list with
    counts. The two are not interchangeable: the admin one covers every
    `CatalogueEntryStatus`, the public one `active` alone (issue #266
    review - the two routes had shared one hard-coded sentence naming only
    the public path).

    FastAPI merges `openapi_extra` into the generated operation with
    `deep_dict_update`, which *concatenates* lists - so this is appended to
    the parameters FastAPI derived from the signature rather than replacing
    them.

    `explode: true` over `style: form` is OpenAPI's own spelling of "repeat
    the key once per value", which is exactly the wire shape.
    """
    return {
        "name": f"{FILTER_PARAM_PREFIX}{{property_key}}",
        "in": "query",
        "required": False,
        "style": "form",
        "explode": True,
        "schema": {"type": "array", "items": {"type": "string"}},
        "description": (
            "Filter by a facet. The parameter name is the facet's `key` prefixed "
            f"with `{FILTER_PARAM_PREFIX}` - `?{FILTER_PARAM_PREFIX}discipline=chemistry`. "
            "Repeat the parameter to select several values of one facet; they are "
            "OR-ed. Filters on different facets are AND-ed, so adding one always "
            "narrows the result. The facets available are not fixed: they are "
            "every property an administrator has marked filterable, plus the "
            f"entry status, and `GET {search_path}` returns the current list "
            "with counts. An operator other than the default `equals` is named "
            f"after the key, separated by `{FILTER_OP_SEPARATOR}` - "
            f"`?{FILTER_PARAM_PREFIX}assay_name{FILTER_OP_SEPARATOR}prefix=glu`, or "
            f"`?{FILTER_PARAM_PREFIX}volume_ml{FILTER_OP_SEPARATOR}range=1..5`. "
            "Which operators a facet accepts follows from the property's "
            "datatype; one it does not accept is a 422, never a silently ignored "
            f"parameter. At most {FILTER_VALUE_CAP} distinct values are accepted "
            "in one facet's selection (repeated parameter or `:in` list alike); "
            "more than that is also a 422. NOTE for generated clients: "
            "`{property_key}` above is a "
            "placeholder, not a literal parameter name - OpenAPI has no syntax "
            "for a templated parameter name, so a generated client typically "
            "renders one field named literally `filter.{property_key}`. Sending "
            "that literal string is a 422 (`{property_key}` is not a filter this "
            "endpoint offers); a real filter parameter's name is built by hand, "
            "substituting an actual facet key (see ADR-0032)."
        ),
    }


@dataclass(frozen=True)
class FilterRequest:
    """The facets one request has, and the selection made against them.

    Both, from one dependency, because they come from one read of
    `property_definition` - resolving them separately would enumerate the
    registry twice per request and could, under a concurrent registry
    write, validate a filter against one facet list and count buckets
    against another.

    Shared between `catalogue.py` and `catalogue_admin.py` (issue #266): the
    shape is identical on both surfaces, and only the `status_values` each
    router's own `_filter_request` passes to `load_facet_context` differs
    (`queries.PUBLIC_STATUSES` vs `nptc.catalogue.maintenance.
    MAINTENANCE_STATUSES`) - see each router's own filter dependency.
    """

    context: FacetContext
    selections: tuple[FilterSelection, ...]


class Binding(BaseModel):
    """A SNOMED CT code binding, active or retired.

    `code` is a string, always (FR-06). `fsn` is served exactly as stored -
    FR-82's as-served guarantee - with no strip applied anywhere on this
    read path; `label_provenance["fsn"]` is FR-98's declaration of that
    fact (`semantic_tag` is config-driven, see `nptc.api.labels.
    fsn_provenance`), not a second, silently-stripped copy of the label.

    A retired binding carries `retirement_reason` and, where PRD FR-08's
    replacement case applies, `replaced_by_code` - the successor's *code*,
    which is what a client holding the retired one needs in order to move.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    code: str
    fsn: str
    au_preferred_term: str | None
    edition_hint: str
    status: str
    retirement_reason: str | None
    replaced_by_code: str | None
    #: FR-98: one entry per label-bearing field on this model - `fsn` and
    #: `au_preferred_term` - declared once per row (not restated per field
    #: access) by `binding_from_row`.
    label_provenance: dict[str, LabelProvenance]


class BindingList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[Binding]


def binding_from_row(row: queries.BindingRow) -> Binding:
    """`get_api_settings()` reads the same process-wide cached
    `ApiSettings` singleton every other read-path consumer of settings
    does (`nptc.api.dependencies`, `lru_cache`d) - a plain call, not a
    FastAPI `Depends`, because this is an assembler function, not a route
    handler, and `get_api_settings` takes no request-scoped argument to
    inject in the first place.

    **A deliberate, temporary trade** (review, issue #144): calling it
    directly means `app.dependency_overrides` cannot reach this call the
    way it reaches `get_auth_settings`/`get_session`/`get_terminology_
    client` in the test harness (`api_app_support.py`). Harmless today -
    `ApiSettings` refuses any `fsn_semantic_tag` but `"intact"` at
    construction time, so there is only one value this could ever read -
    but it will need revisiting once FR-66 makes the setting legitimately
    vary and a test wants to serve `"stripped"` without a real environment
    variable.
    """
    settings = get_api_settings()
    return Binding(
        system=row.system,
        code=row.code,
        fsn=row.fsn,
        au_preferred_term=row.au_preferred_term,
        edition_hint=row.edition_hint,
        status=row.status,
        retirement_reason=row.retirement_reason,
        replaced_by_code=row.replaced_by_code,
        label_provenance={
            "fsn": fsn_provenance(settings),
            "au_preferred_term": AU_PREFERRED_TERM_PROVENANCE,
        },
    )


class EntrySummary(BaseModel):
    """An entry as it appears in a list or a search result.

    `length` is FR-85's published figure - the character count of the
    catalogue's own preferred term, computed by `CatalogueEntry.length` and
    never stored, so it cannot drift from the term it describes.
    """

    model_config = ConfigDict(frozen=True)

    business_key: str
    preferred_term: str
    length: int
    status: str
    #: FR-89: `true` means "this test accepts any specimen", which is a
    #: different statement from "no specimen property has been recorded" -
    #: the ambiguity this core column exists to destroy.
    specimen_unconstrained: bool
    #: A real `datetime`, not a pre-formatted string: that is what puts
    #: `format: date-time` in `docs/api/openapi.json`, so #147's generated
    #: client parses it as a date rather than handing the caller a string to
    #: guess at.
    updated_at: datetime
    #: FR-18: `true` when this entry has at least one `open`
    #: `ValidationFinding`. Nothing else about a finding appears here or
    #: anywhere else on the public surface - no type, no severity, no
    #: count, no internal id - by construction: this is a bare `bool`, and
    #: there is no other field a type or severity could ever leak through.
    #: An `acknowledged`/`resolved`/`superseded` finding does not set it.
    has_open_finding: bool
    #: FR-98: `preferred_term` is the catalogue's own en-AU preferred term
    #: (ADR-0022), never an FSN - fixed, not configuration-driven, so this
    #: is the same constant on every row.
    label_provenance: dict[str, LabelProvenance]


#: `EntrySummary.label_provenance` is one entry, fixed for every row - see
#: the field's own docstring. A module-level constant, not rebuilt inside
#: `entry_summary_fields` on every call.
_ENTRY_SUMMARY_LABEL_PROVENANCE: dict[str, LabelProvenance] = {
    "preferred_term": AU_PREFERRED_TERM_PROVENANCE,
}


class EntryPage(BaseModel):
    """One page of `EntrySummary` rows, keyset-paginated on `business_key`.

    Served by both `catalogue.py`'s public `GET /catalogue/entries`
    (`PUBLIC_STATUSES` only) and `catalogue_admin.py`'s
    `GET /catalogue/admin/entries` (any status, issue #266) - one shape, the
    same reason `EntryDetail` is shared rather than duplicated. `next_cursor`
    is `null` on the last page - which is the *only* reliable signal that
    paging is finished. A client must not infer the end from a short page: a
    page can be short and still have a successor.
    """

    model_config = ConfigDict(frozen=True)

    items: list[EntrySummary]
    next_cursor: str | None


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


class FacetBucket(BaseModel):
    """One value of one facet, with how many entries in the current result
    set carry it."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(
        description=(
            "Send this back as the filter value to select this bucket - "
            "`?filter.<key>=<value>`. It is the stored value, not the label."
        )
    )
    label: str = Field(
        description=(
            "How to show this bucket. For a coded property it is the display "
            "term stored alongside the code when the value was recorded, never "
            "a live terminology lookup, so it is stable and offline. Falls back "
            "to `value` where the stored value carries no label of its own."
        )
    )
    count: int = Field(
        description=(
            "Entries in the current result set carrying this value. An entry "
            "with several values of one property counts once under each of "
            "them, never several times under one."
        )
    )


class Facet(BaseModel):
    """One facet, derived from the property registry at request time.

    Never a fixed list: a property an administrator marks filterable appears
    here on the next request, with no deployment and no restart (FR-09,
    FR-16). A client must therefore render whatever it is given rather than
    hard-coding the facets it knows about.
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(description="The property key, and the suffix of its `filter.` parameter.")
    label: str = Field(description="The property's own label, as an administrator set it.")
    facetable: bool = Field(
        description=(
            "`false` for a property that can be filtered on but not grouped - a "
            "continuous numeric one, where every value would be its own bucket. "
            "Such a facet is reported with no buckets rather than omitted, so a "
            "client can tell it apart from a facet whose values happen to match "
            "nothing."
        )
    )
    truncated: bool = Field(
        description=(
            f"`true` when this facet has more than {FACET_BUCKET_CAP} distinct "
            "values and only the most common were returned. There is no way to "
            "page through the remainder; narrow the search instead."
        )
    )
    buckets: list[FacetBucket]


class SearchPage(BaseModel):
    """Served by both `catalogue.py`'s public `GET /catalogue/search`
    (`PUBLIC_STATUSES` only) and `catalogue_admin.py`'s
    `GET /catalogue/admin/search` (any status, issue #266) - see
    `EntryPage`'s own docstring for why one shape rather than two."""

    model_config = ConfigDict(frozen=True)

    items: list[SearchHit]
    next_cursor: str | None
    facets: list[Facet] = Field(
        description=(
            "Every facet available for this search, with counts over the whole "
            "result set rather than this page. A facet's own selection is "
            "excluded from its own counts, so a bucket you have not chosen "
            "still tells you how many entries it would give you."
        )
    )


class PropertyValue(BaseModel):
    """One property value, rendered by its datatype's own handler.

    `value` is whatever that handler's `serialise(..., JSON)` returns, so a
    new datatype (FR-77) appears here correctly without this module
    changing. `ordinal` is meaningful for a multi-valued property: it is the
    position of this value among that property's values, zero-based.

    `status` is the *definition's* status (issue #248) - `active` or
    `deprecated` - not a fact about this value. FR-11 makes a deprecated
    definition retain its recorded values, so without this field a client
    reading an entry could not tell such a value apart from one recorded
    against a property still open for new writes, short of a second call to
    `GET /registry/properties?include_deprecated=true`.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    datatype: str
    cardinality: str
    status: str
    ordinal: int
    value: Any
    justification: str | None


class EntryDetail(EntrySummary):
    """A summary plus everything attached to the entry.

    One response rather than making a client fetch four: the sub-resources
    are also served individually (a client refreshing one panel should not
    re-fetch the lot), but the common case is "show me this entry", and
    four round trips for one screen is a contract that pushes latency onto
    every consumer.

    Served by both `catalogue.py`'s public detail route (`active` only) and
    `catalogue_admin.py`'s admin detail route (any status, issue #228) -
    one shape, so an edit screen consuming the admin route today gets the
    exact same fields a public consumer of the same entry, once published,
    would see.
    """

    model_config = ConfigDict(frozen=True)

    #: FR-38's optimistic-locking token (issue #227), and the reason this
    #: model carries a field `EntrySummary` does not. A write route that
    #: touches the entry itself requires the caller's `expected_row_version`
    #: (`nptc.catalogue.entries.save_entry`), so an editing client has to be
    #: able to read the current one - and the *detail* is what an edit
    #: screen loads before it can edit anything. A list or a search result
    #: is not an editing context: putting the token on `EntrySummary` would
    #: publish a per-row counter on every page of the public catalogue to
    #: serve a case that does not exist yet (a bulk save straight from a
    #: list, FR-39/#63), so it stays here until it does.
    #:
    #: Not an internal identifier, despite the module-level ban `catalogue.py`
    #: states: `business_key` is still the only thing that *names* an entry,
    #: and this counter addresses nothing. It is opaque to a read-only
    #: consumer and meaningful only as the value handed straight back on the
    #: next write.
    row_version: int
    designations: list[Designation]
    bindings: list[Binding]
    properties: list[PropertyValue]


# --- assembling the entry-level models from query rows ---------------------
#
# Free functions rather than model methods: `nptc.catalogue.queries`' row
# types are the read layer's vocabulary and these models are the HTTP
# contract, and a classmethod on the model would make the contract import
# the read layer's shapes into its own definition.


def entry_summary_fields(
    business_key: str,
    preferred_term: str,
    length: int,
    status: str,
    specimen_unconstrained: bool,
    updated_at: datetime,
    has_open_finding: bool,
) -> dict[str, Any]:
    return {
        "business_key": business_key,
        "preferred_term": preferred_term,
        "length": length,
        "status": status,
        "specimen_unconstrained": specimen_unconstrained,
        "updated_at": updated_at,
        "has_open_finding": has_open_finding,
        "label_provenance": _ENTRY_SUMMARY_LABEL_PROVENANCE,
    }


def summary_from_entry(entry: CatalogueEntry, has_open_finding: bool) -> EntrySummary:
    """The `EntrySummary` for one loaded `CatalogueEntry` row - the listing
    routes' own assembler, shared between `catalogue.py`'s `list_entries`
    and `catalogue_admin.py`'s `list_entries_any_status` (issue #266 review):
    both were carrying a byte-for-byte copy of this three-line function
    under a leading-underscore name, which is the identical drift risk this
    module's own docstring gives for not duplicating a response model."""
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


def build_entry_detail(
    session: Session, registry: DatatypeRegistry, entry: CatalogueEntry
) -> EntryDetail:
    """Assembles the one `EntryDetail` shape every FR-17 URL form serves for
    the same entry (issue #140) - `catalogue.py`'s business-key route and
    its two code-lookup siblings all call this, rather than reassembling
    the same four loaders three times over with the risk that a future edit
    updates one copy and not the others."""
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


def property_value_from_row(
    row: queries.PropertyValueRow, registry: DatatypeRegistry
) -> PropertyValue:
    handler = registry.get(row.datatype)
    return PropertyValue(
        key=row.property_key,
        label=row.label,
        datatype=row.datatype,
        cardinality=row.cardinality,
        status=row.status,
        ordinal=row.ordinal,
        value=handler.serialise(row.value, SerialisationTarget.JSON),
        justification=row.justification,
    )


class Designation(BaseModel):
    """A catalogue-authored synonym, or a preferred variant in a language
    other than en-AU.

    The catalogue's own en-AU preferred term is **not** here - it is
    `EntrySummary.preferred_term`, and ADR-0022 makes its absence from
    `designation` a database invariant rather than a convention. A client
    building a term list needs both: `preferred_term`, plus these.

    No `id` (matching `Binding`'s own rule, NFR-04/NFR-26): issue #224's
    write router addresses a designation by term in the request body, not
    an internal identifier - see that router's own module docstring.
    """

    model_config = ConfigDict(frozen=True)

    term: str
    use: str
    language: str
    status: str
    length: int
    #: FR-98. Singular, not a dict: this model carries exactly one label
    #: field (`term`), unlike `Binding`/`EntrySummary`. Per-row, not a
    #: shared constant - it depends on this row's own `use`, see
    #: `designation_from_row`.
    label_provenance: LabelProvenance


class DesignationList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[Designation]


#: `Designation.use`'s two values, each mapped to its own FR-98 provenance -
#: ADR-0022 is why `"preferred"` here means `PREFERRED_VARIANT_PROVENANCE`
#: (a non-en-AU preferred term) rather than the AU preferred term.
_DESIGNATION_LABEL_PROVENANCE: dict[str, LabelProvenance] = {
    "synonym": SYNONYM_PROVENANCE,
    "preferred": PREFERRED_VARIANT_PROVENANCE,
}


def designation_from_row(row: queries.DesignationRow) -> Designation:
    return Designation(
        term=row.term,
        use=row.use,
        language=row.language,
        status=row.status,
        length=row.length,
        label_provenance=_DESIGNATION_LABEL_PROVENANCE[row.use],
    )
