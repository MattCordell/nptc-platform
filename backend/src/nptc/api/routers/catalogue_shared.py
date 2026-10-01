"""Response models and helpers shared by the public read router (`catalogue.py`, FR-20),
the admin read router (`catalogue_admin.py`) and the catalogue write routers.

Each router stays a separate module (see their docstrings), but a row written or read by
one must come back out looking exactly like the same row read by another. The response
models, query-parameter types and row-to-model assemblers therefore live here, so no
router imports another's private helpers and the shapes cannot drift. Moving a model
between modules leaves its OpenAPI component name unchanged.

`binding_from_row`, `designation_from_row`, `entry_summary_fields` and
`property_value_from_row` have no leading underscore because they are this module's
cross-router contract, listed in `__all__`. An underscore would mark them private and
invite a future reader to inline them.

**No `display_term`, and no strip anywhere in this module (FR-83, FR-98).** `Binding.fsn`
is served as stored (FR-82), and `Binding.label_provenance` declares that fact. FR-83's
sanctioned renderer, `nptc.exports.semantic_tag.render_display_term`, is reached only from
the export surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

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
from nptc.settings import ApiSettings

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

#: FR-17: the short alias half of `GET /catalogue/code/{system_token}/{code}`. A
#: malformed token is a 422 here; a well-formed but unregistered one reaches
#: `nptc.catalogue.code_systems.system_for_token` and is a 404 (that module's
#: docstring explains why the two differ).
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

#: FR-17: the exact code to resolve. No `pattern=`: ADR-0033 records why `code`
#: is not shape-validated, so an unrecognised and a malformed code get the same
#: 404.
CodePath = Annotated[
    str,
    Path(description="The exact code to resolve.", examples=["49466006"]),
]

#: The page-size ceiling, shared by every collection route, public or admin. A
#: caller wanting the whole catalogue pages through it with the cursor.
LimitQuery = Annotated[
    int,
    Query(ge=1, le=200, description="Maximum entries in this page."),
]

#: `/catalogue/entries` pages on `business_key`, so its cursor is a business key
#: and is validated as one. A mangled cursor is a 422, as on the search routes;
#: otherwise the endpoint would silently serve "the page after whatever this
#: sorts before" and the client could not notice it had corrupted its cursor.
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

#: `/catalogue/search` and `/catalogue/admin/search` page on
#: `<score>:<request digest>:<business_key>`. `nptc.catalogue.search` parses it
#: and raises `MalformedSearchCursorError` (also a 422) for anything it did not
#: mint, including a cursor minted for a different `q`, filter set or status
#: scope.
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
    """The FR-16 filter query parameter, declared by hand (ADR-0032), shared by the
    two collection routes that accept `?filter.<key>=<value>`.

    **Why by hand.** The name is `filter.<property_key>` for whichever properties an
    administrator has marked `filterable`, and FastAPI declares only parameters known
    when the signature is written. Each router's `_filter_request` reads the query
    string directly, so declaring the parameter here is what stops the OpenAPI
    document (the FR-20 contract, policed by the breaking-change check) omitting a
    parameter the API accepts.

    `search_path` names this collection's own search route, so the description points
    a caller at the endpoint that returns this facet's current buckets with counts.
    The admin route covers every status, the public one `active` alone.

    FastAPI merges `openapi_extra` with `deep_dict_update`, which concatenates lists,
    so this is appended to the signature's parameters rather than replacing them.
    `explode: true` over `style: form` is OpenAPI's spelling of "repeat the key once
    per value", the wire shape.
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

    Shared by `catalogue.py` and `catalogue_admin.py`. Only the `status_values` that
    each router's `_filter_request` passes to `load_facet_context` differs
    (`queries.PUBLIC_STATUSES` or `maintenance.MAINTENANCE_STATUSES`).
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


def binding_from_row(row: queries.BindingRow, settings: ApiSettings) -> Binding:
    """`settings` is the request's own `ApiSettingsDep`, never a fresh
    `get_api_settings()` call, so a binding's FSN provenance cannot disagree
    with the settings the rest of the request read."""
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
    #: A real `datetime`, not a formatted string, so the OpenAPI document says
    #: `format: date-time` and a generated client parses it as a date.
    updated_at: datetime
    #: FR-18: `true` when the entry has at least one `open` `ValidationFinding`. A
    #: bare `bool` by construction: no type, severity, count or internal id can
    #: leak through it. An `acknowledged`, `resolved` or `superseded` finding does
    #: not set it.
    has_open_finding: bool
    #: FR-98: `preferred_term` is the catalogue's own en-AU preferred term
    #: (ADR-0022), never an FSN - fixed, not configuration-driven, so this
    #: is the same constant on every row.
    label_provenance: dict[str, LabelProvenance]


#: `EntrySummary.label_provenance` is the same one entry on every row, so it is a
#: constant, not rebuilt on each call.
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
    would see. One exception (issue #239): `designations` also carries
    retired rows on the admin route, because its reader is an editor
    deciding against editorial history rather than an implementer who has
    no use for it - see `queries.load_designations`'s own docstring.
    `bindings` and `properties` were already identical on both routes
    before this issue and stay that way.
    """

    model_config = ConfigDict(frozen=True)

    #: FR-38's optimistic-locking token, here and not on `EntrySummary`. A write
    #: that touches the entry requires the caller's `expected_row_version`
    #: (`nptc.catalogue.entries.save_entry`), and the detail is what an edit screen
    #: loads first. A public list or search result is not an editing context, so
    #: putting the token on `EntrySummary` would publish a per-row counter on every
    #: public page. The admin listing carries it through `AdminEntrySummary`.
    #:
    #: Not an internal identifier: `business_key` still names the entry, and this
    #: counter addresses nothing. It is opaque to a read-only consumer.
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
    """The `EntrySummary` for one loaded `CatalogueEntry` row, used by
    `catalogue.py`'s `list_entries`."""
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
    session: Session, registry: DatatypeRegistry, entry: CatalogueEntry, settings: ApiSettings
) -> EntryDetail:
    """Assembles the `EntryDetail` that every FR-17 URL form serves for the same
    entry. `catalogue.py`'s business-key route and its two code-lookup routes share
    it, so one edit cannot update only some copies."""
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
        bindings=[
            binding_from_row(row, settings) for row in queries.load_bindings(session, entry_ids)
        ],
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
