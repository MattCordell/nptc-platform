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

**`binding_from_row`/`designation_from_row`/`entry_summary_fields`/
`property_value_from_row` carry no leading underscore, unlike every other
free function in this module.** They are this module's actual
cross-router contract - imported by all three of `catalogue.py`,
`catalogue_admin.py` and (the designation one) `catalogue_designations.py`
- so a leading underscore on them would misrepresent an intentional,
`__all__`-listed API as a private implementation detail a future reader
might "clean up" by inlining. `_display_term` keeps its underscore: it is
never imported anywhere outside this file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import Path
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from nptc.api.errors import StoredFSNNotRenderableError
from nptc.catalogue import queries
from nptc.catalogue.code_systems import SYSTEM_TOKEN_PATTERN
from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.exports.semantic_tag import EmptyDisplayTermError, NotAServedFSNError, render_display_term
from nptc.registry.handlers import DatatypeRegistry, SerialisationTarget

__all__ = [
    "Binding",
    "BindingList",
    "BusinessKeyPath",
    "CodePath",
    "Designation",
    "DesignationList",
    "EntryDetail",
    "EntrySummary",
    "PropertyValue",
    "SystemTokenPath",
    "binding_from_row",
    "build_entry_detail",
    "designation_from_row",
    "entry_summary_fields",
    "property_value_from_row",
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


class Binding(BaseModel):
    """A SNOMED CT code binding, active or retired.

    `code` is a string, always (FR-06). `display_term` is `fsn` with its
    semantic tag removed exactly once, by FR-83's single sanctioned
    renderer - it is derived here rather than stored, because a stored
    stripped value is indistinguishable from an unstripped one and that
    ambiguity is what makes double-stripping possible.

    A retired binding carries `retirement_reason` and, where PRD FR-08's
    replacement case applies, `replaced_by_code` - the successor's *code*,
    which is what a client holding the retired one needs in order to move.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    code: str
    fsn: str
    display_term: str
    au_preferred_term: str | None
    edition_hint: str
    status: str
    retirement_reason: str | None
    replaced_by_code: str | None


class BindingList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[Binding]


def _display_term(fsn: str) -> str:
    """FR-83's one sanctioned strip, with its refusal re-labelled: the
    caller here supplied a business key/code, not the FSN, so a stored FSN
    that fails to strip is a 500 (`StoredFSNNotRenderableError`), not the
    422 `render_display_term` raises on its own."""
    try:
        return render_display_term(fsn)
    except (NotAServedFSNError, EmptyDisplayTermError) as exc:
        raise StoredFSNNotRenderableError(str(exc)) from exc


def binding_from_row(row: queries.BindingRow) -> Binding:
    return Binding(
        system=row.system,
        code=row.code,
        fsn=row.fsn,
        display_term=_display_term(row.fsn),
        au_preferred_term=row.au_preferred_term,
        edition_hint=row.edition_hint,
        status=row.status,
        retirement_reason=row.retirement_reason,
        replaced_by_code=row.replaced_by_code,
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
    }


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


class DesignationList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[Designation]


def designation_from_row(row: queries.DesignationRow) -> Designation:
    return Designation(
        term=row.term,
        use=row.use,
        language=row.language,
        status=row.status,
        length=row.length,
    )
