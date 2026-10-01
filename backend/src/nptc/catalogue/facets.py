"""Faceted filtering over the catalogue, derived from the property registry at
request time (FR-16, FR-09).

`docs/adr/0032-faceted-filter-query-surface.md` records the query surface and
the rejected alternatives. The points below govern code in this module.

**Nothing here names a datatype.** A facet's grouping expression, accepted
operators and predicate all come from the property's `DatatypeHandler`
(`facet_expression()`, `supported_filter_ops()` and `filter_clause()`,
ADR-0013). So flipping `filterable` on a property makes it a facet with no code
change or restart (FR-09): the facet list is a `SELECT` against
`property_definition` on every request. `test_datatype_dispatch.py` enforces the
no-switch half.

**Two kinds of facet, one builder.** Entry `status` is a declared core-column
facet: it lives on `catalogue_entry`, so no `PropertyDefinition` enumerates it.
It is a descriptor of the same shape (`_core_facets`), and the only branch
between the kinds is which `FacetSource` a descriptor holds. That is a fact
about where the value is stored, never about its datatype.

**`EXISTS` predicates and `COUNT(DISTINCT entry_id)`.** A multi-valued property
(`specimen`, `0..*`) gives an entry several `property_value` rows. A join would
count it once per row. `EXISTS` matches it once, and `COUNT(DISTINCT)` makes an
entry with seven specimens contribute one to each of seven buckets, not seven to
one.

**`property_key` is rendered as a literal, on purpose.** The per-property
partial index is predicated on `property_key = '<literal>'`, and a bound key
defeats it under a generic plan. `literal_execute=True` renders the key inline
through SQLAlchemy's escaping. That is not string-built SQL (NFR-22), and the
database constrains `property_key` to `^[a-z][a-z0-9_]{0,62}$`.
`test_db_property_index_plan.py` proves both halves.

**A facet's counts exclude its own selection.** A user who picked "Chemistry"
still sees how many entries "Haematology" would give. Applying a facet's own
filter to its own counts yields one bucket and a dead-end panel.

**Labels come from the stored value, never a terminology call** (FR-54). A
coded value stores `{"system", "code", "display"}`, and the bucket label is
`jsonb_extract_path_text(value, 'display')`. That is generic JSON handling: it
yields `NULL` for a value with no such member, and the bucket then falls back to
its grouping value.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol
from typing import cast as type_cast

from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    bindparam,
    cast,
    distinct,
    exists,
    func,
    or_,
    union_all,
)
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import literal

from nptc.db.definitions import list_definitions
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.property_definition import PropertyDefinition, PropertyStatus
from nptc.db.models.property_value import PropertyValue
from nptc.db.property_specs import spec_for
from nptc.registry.definitions import DefinitionAudience
from nptc.registry.handlers import (
    DatatypeHandler,
    DatatypeRegistry,
    FilterOp,
    PropertyDefinitionSpec,
)

__all__ = [
    "CORE_FACET_KEYS",
    "FACET_BUCKET_CAP",
    "FILTER_OP_SEPARATOR",
    "FILTER_PARAM_PREFIX",
    "FILTER_VALUE_CAP",
    "ConflictingFilterOperatorError",
    "Facet",
    "FacetBucket",
    "FacetContext",
    "FacetDescriptor",
    "FilterRefusedError",
    "FilterSelection",
    "FilterValueError",
    "TooManyFilterValuesError",
    "UnknownFilterKeyError",
    "UnsupportedFilterOperatorError",
    "build_facet_count_statement",
    "build_facet_counts_statement",
    "compute_facets",
    "filter_digest_material",
    "filter_predicates",
    "load_facet_context",
    "parse_filters",
]

#: At most this many buckets per facet, by count descending. An invented number,
#: argued in ADR-0032. `Facet.truncated` says when it bit, so a client is never
#: shown a partial list it cannot tell from a whole one.
FACET_BUCKET_CAP: Final[int] = 20

#: At most this many values in one facet's selection (a repeated
#: `?filter.discipline=` or an `:in` list). `FACET_BUCKET_CAP` bounds the
#: response; this bounds the request. Any operator but `IN` builds one correlated
#: `EXISTS` per value, and every facet's chain goes into the one statement
#: `compute_facets` runs, so an uncapped repeat count is an uncapped `OR` chain
#: on an unauthenticated endpoint. An invented number, argued in ADR-0032.
FILTER_VALUE_CAP: Final[int] = 50

#: `?filter.discipline=chem&filter.discipline=haem`. The prefix keeps the
#: namespace clear of `q`, `limit`, `after` and later parameters, and repeating
#: the key means "any of these" (ADR-0032).
FILTER_PARAM_PREFIX: Final[str] = "filter."

#: `?filter.volume_ml:range=1..5`. Omitted, the operator is `equals`.
FILTER_OP_SEPARATOR: Final[str] = ":"

#: `1..5`; only `FilterOp.RANGE` takes a pair.
_RANGE_SEPARATOR: Final[str] = ".."

#: The operator names the surface accepts. Spelled out, not derived from
#: `FilterOp.__members__`, so renaming an enum member cannot silently change the
#: wire contract (FR-20).
_OPERATOR_NAMES: Final[Mapping[str, FilterOp]] = {
    "equals": FilterOp.EQUALS,
    "in": FilterOp.IN,
    "prefix": FilterOp.PREFIX,
    "range": FilterOp.RANGE,
}

_DEFAULT_OPERATOR: Final[FilterOp] = FilterOp.EQUALS


# --- refusals -------------------------------------------------------------
#
# Every one is a 422 and loud: a filter the server quietly dropped would serve a
# page that answers a different question than the caller asked (ADR-0032).


class FilterRefusedError(ValueError):
    """Base for every reason a filter parameter is unusable.

    One base class, so `nptc.api.errors` maps the family with one handler and one
    client-facing sentence. The subclasses exist for the log, which records the
    exception class.
    """

    http_status: ClassVar[int] = 422


class UnknownFilterKeyError(FilterRefusedError):
    """No property definition, and no core-column facet, has this key."""


class FilterNotAvailableError(FilterRefusedError):
    """The key names a real property that is not `filterable`, or is
    deprecated.

    Distinct from `UnknownFilterKeyError` in the log only. Deliberately
    indistinguishable in the response: which properties exist but are not
    offered as filters is editorial state the public API has no reason to
    disclose, and the caller's remedy is identical either way.
    """


class UnsupportedFilterOperatorError(FilterRefusedError):
    """An operator this endpoint does not know, or one absent from the
    property handler's own `supported_filter_ops()` - `prefix` on a coded
    property, say, which stores an object and has no prefix to take."""


class ConflictingFilterOperatorError(FilterRefusedError):
    """The same facet key was sent with more than one operator, such as
    `?filter.discipline=Chemistry&filter.discipline:in=Haematology`.

    `parse_filters` groups selections by `(key, op)`, so two operators would
    become two `FilterSelection`s that `filter_predicates` ANDs as if they were
    different facets. Most pairs are unsatisfiable together, which would give a
    silent, always-empty result instead of a 422."""


class TooManyFilterValuesError(FilterRefusedError):
    """One facet's selection exceeded `FILTER_VALUE_CAP` values; see that
    constant."""


class FilterValueError(FilterRefusedError):
    """The value cannot be interpreted as this property's values are - a
    non-numeric string for a `positiveInt` property, or a `range` that is
    not `<min>..<max>`."""


# --- what a facet is ------------------------------------------------------


class FacetSource(Protocol):
    """Where one facet's values live, and how to ask about them.

    Two implementations: a registry property (values in `property_value`,
    behaviour from its `DatatypeHandler`) and a declared core column (values on
    `catalogue_entry`). Parsing, predicate composition, aggregation and truncation
    are written once against this Protocol.
    """

    def supported_ops(self) -> frozenset[FilterOp]: ...

    def coerce(self, raw: str) -> Any:
        """One query-string token, as the value this source compares
        against. Raises `FilterValueError`."""
        ...

    def predicate(self, op: FilterOp, value: Any) -> ColumnElement[bool]:
        """A predicate over `catalogue_entry`, correlated where needed."""
        ...

    def value_source(self) -> Select[Any] | None:
        """`(entry_id, value, label)` rows for aggregation, or `None` where
        this source cannot be faceted at all (a `decimal` property, whose
        handler returns `None` from `facet_expression()` because every
        value is its own bucket)."""
        ...


#: JSON Schema type names mapped to the Python value the comparison needs, taken
#: from the handler's `json_schema_fragment()`. This is generic JSON Schema
#: handling, not a datatype switch (FR-77, ADR-0013). Any other type, notably
#: `"object"` (how a coding declares itself), passes through as the raw string,
#: because `CodeHandler.filter_clause` builds a `@>` containment from the code.
_JSON_TYPE_COERCIONS: Final[Mapping[str, Callable[[str], Any]]] = {
    "integer": int,
    "number": float,
}


def _value_column() -> ColumnElement[Any]:
    """`property_value.value` as the plain `ColumnElement` the handler contract
    takes. The ORM attribute's JSONB-specific type is narrower and not assignable
    under `mypy --strict`; it is cast once here instead of at three call sites.
    """
    return type_cast("ColumnElement[Any]", PropertyValue.value)


@dataclass(frozen=True, slots=True)
class _PropertyFacetSource:
    """A facet over a `filterable` `PropertyDefinition`."""

    handler: DatatypeHandler
    spec: PropertyDefinitionSpec

    @property
    def _key_literal(self) -> ColumnElement[Any]:
        """`property_key` rendered inline at execution time, so the partial index is
        provably covered; see the module docstring."""
        return bindparam(
            f"facet_property_key_{self.spec.key}",
            self.spec.key,
            type_=Text,
            literal_execute=True,
        )

    def supported_ops(self) -> frozenset[FilterOp]:
        return self.handler.supported_filter_ops()

    def coerce(self, raw: str) -> Any:
        json_type = self.handler.json_schema_fragment(self.spec).get("type")
        coercion = _JSON_TYPE_COERCIONS.get(json_type) if isinstance(json_type, str) else None
        if coercion is None:
            return raw
        try:
            return coercion(raw)
        except (TypeError, ValueError):
            raise FilterValueError(
                f"{raw!r} is not a usable value for property {self.spec.key!r}"
            ) from None

    def predicate(self, op: FilterOp, value: Any) -> ColumnElement[bool]:
        # EXISTS, never a join; see the module docstring.
        return exists(
            sa_select(literal(1))
            .select_from(PropertyValue)
            .where(PropertyValue.entry_id == CatalogueEntry.id)
            .where(PropertyValue.property_key == self._key_literal)
            .where(self.handler.filter_clause(op, value, _value_column()))
        )

    def value_source(self) -> Select[Any] | None:
        expression = self.handler.facet_expression(_value_column())
        if expression is None:
            return None
        return (
            sa_select(
                PropertyValue.entry_id.label("entry_id"),
                expression.label("value"),
                # Generic JSON handling: NULL unless the value is an object
                # carrying `display`. No terminology call (FR-54), no datatype named.
                func.jsonb_extract_path_text(PropertyValue.value, "display").label("label"),
            )
            .select_from(PropertyValue)
            .where(PropertyValue.property_key == self._key_literal)
        )


@dataclass(frozen=True, slots=True)
class _CoreColumnFacetSource:
    """A facet over a column of `catalogue_entry` itself.

    Declared in `_core_facets`, not discovered, since entry status has no
    `PropertyDefinition`. It yields descriptors of the shape the registry-derived
    ones have, so no predicate or aggregation code special-cases it.
    """

    column: ColumnElement[Any]
    #: The values this surface may filter on, not every value
    #: `CatalogueEntryStatus` admits. The public routes restrict every other query
    #: to `queries.PUBLIC_STATUSES`, so `?filter.status=draft` there would be
    #: well-formed and silently match nothing. The caller supplies the set, so an
    #: admin listing can pass the whole enum.
    allowed_values: frozenset[str]

    def supported_ops(self) -> frozenset[FilterOp]:
        return frozenset({FilterOp.EQUALS, FilterOp.IN})

    def coerce(self, raw: str) -> Any:
        # No handler validates `status`, so refuse anything outside this
        # surface's permitted set: a value that merely failed to match would
        # give a silently empty page.
        if raw not in self.allowed_values:
            known = ", ".join(sorted(self.allowed_values))
            raise FilterValueError(
                f"{raw!r} is not a status this endpoint can filter on; expected one of {known}"
            )
        return raw

    def predicate(self, op: FilterOp, value: Any) -> ColumnElement[bool]:
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", self.column == value)
        if op is FilterOp.IN:
            return type_cast("ColumnElement[bool]", self.column.in_(tuple(value)))
        raise UnsupportedFilterOperatorError(f"core-column facet does not support {op.value}")

    def value_source(self) -> Select[Any] | None:
        return sa_select(
            CatalogueEntry.id.label("entry_id"),
            self.column.label("value"),
            cast(literal(None), Text).label("label"),
        ).select_from(CatalogueEntry)


@dataclass(frozen=True, slots=True)
class FacetDescriptor:
    """One facet, whatever it is derived from."""

    key: str
    label: str
    #: Ordering only, as `PropertyDefinition.display_order`. Core facets take a
    #: negative one so they lead, a presentation choice.
    display_order: int
    source: FacetSource

    @property
    def facetable(self) -> bool:
        """`False` for a property that is filterable but cannot be grouped; see
        `Facet.facetable`."""
        return self.source.value_source() is not None


def _core_facets(status_values: Iterable[str]) -> tuple[FacetDescriptor, ...]:
    """The declared core-column facets. One today (ADR-0032); a tuple so a second
    is a data change, not a new code path.

    `status_values` is the caller's statement of which rows the surface can show:
    `queries.PUBLIC_STATUSES` (`active` alone) on the public API, so the facet has
    one bucket there, or the whole enum on an admin listing.
    `_CoreColumnFacetSource.coerce` refuses a value outside it rather than let it
    match nothing silently.
    """
    return (
        FacetDescriptor(
            key="status",
            label="Status",
            display_order=-1,
            source=_CoreColumnFacetSource(
                column=type_cast("ColumnElement[Any]", CatalogueEntry.status),
                allowed_values=frozenset(status_values),
            ),
        ),
    )


#: Named so the "a core facet is not special-cased" test asserts against the
#: value a descriptor carries. Derived from `_core_facets` so a second core facet
#: is one edit; `status_values=()` is safe because a descriptor's `key` does not
#: depend on its permitted values.
CORE_FACET_KEYS: Final[frozenset[str]] = frozenset(d.key for d in _core_facets(()))


@dataclass(frozen=True, slots=True)
class FacetContext:
    """Every facet available on this request, plus enough about the unavailable
    keys to refuse them accurately.

    Built per request, never cached: caching is the "needs a restart" behaviour
    FR-09 forbids.
    """

    descriptors: tuple[FacetDescriptor, ...]
    #: Every `property_definition.key`, whatever its status or `filterable` flag,
    #: so the log can tell an unknown key from an unavailable one
    #: (`FilterNotAvailableError`).
    known_property_keys: frozenset[str]

    def descriptor(self, key: str) -> FacetDescriptor | None:
        for descriptor in self.descriptors:
            if descriptor.key == key:
                return descriptor
        return None


def load_facet_context(
    session: Session, registry: DatatypeRegistry, *, status_values: Iterable[str]
) -> FacetContext:
    """Enumerate the facets from `property_definition`, now.

    `status_values` is this surface's permitted `catalogue_entry.status` values,
    passed to the core `status` facet (see `_core_facets`).

    One `list_definitions(audience=EXPORT)` call, not two. `EXPORT` returns every
    property regardless of status, and `DATA_ENTRY` is that set filtered to
    `PropertyStatus.ACTIVE` (`nptc.db.definitions`). A second round trip would
    re-derive rows already in hand, on every request to both public collection
    endpoints, only to separate `UnknownFilterKeyError` from
    `FilterNotAvailableError`, a distinction the log alone sees.

    A deprecated property's stored values are still served (FR-11), but it is no
    longer offered for new entries, and offering it as a filter would invite a
    client to build UI around a retired property. So only active, filterable
    properties become descriptors, while `known_property_keys` keeps every key.
    """
    every = list_definitions(session, audience=DefinitionAudience.EXPORT)
    descriptors = list(_core_facets(status_values))
    for definition in every:
        if definition.status != PropertyStatus.ACTIVE or not definition.filterable:
            continue
        descriptors.append(_descriptor_for(definition, registry))
    descriptors.sort(key=lambda d: (d.display_order, d.key))
    return FacetContext(
        descriptors=tuple(descriptors),
        known_property_keys=frozenset(d.key for d in every),
    )


def _descriptor_for(definition: PropertyDefinition, registry: DatatypeRegistry) -> FacetDescriptor:
    spec = spec_for(definition)
    return FacetDescriptor(
        key=definition.key,
        label=definition.label,
        display_order=definition.display_order,
        source=_PropertyFacetSource(handler=registry.get(definition.datatype), spec=spec),
    )


# --- parsing the query string ---------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterSelection:
    """One facet's selection: an operator and one or more values.

    `raw_values` is kept beside the coerced `values` because the cursor digest
    fingerprints what the caller sent, as `search._query_digest` does for `q`.
    """

    descriptor: FacetDescriptor
    op: FilterOp
    values: tuple[Any, ...]
    raw_values: tuple[str, ...]

    @property
    def key(self) -> str:
        return self.descriptor.key


def _split_parameter(name: str) -> tuple[str, FilterOp] | None:
    """`filter.<key>[:<op>]` -> `(key, op)`, or `None` for a parameter that
    is not a filter at all."""
    if not name.startswith(FILTER_PARAM_PREFIX):
        return None
    remainder = name[len(FILTER_PARAM_PREFIX) :]
    key, separator, op_name = remainder.partition(FILTER_OP_SEPARATOR)
    if not separator:
        return key, _DEFAULT_OPERATOR
    op = _OPERATOR_NAMES.get(op_name)
    if op is None:
        raise UnsupportedFilterOperatorError(
            f"{op_name!r} is not a filter operator; expected one of {sorted(_OPERATOR_NAMES)}"
        )
    return key, op


def _coerce_one(descriptor: FacetDescriptor, op: FilterOp, raw: str) -> Any:
    if op is not FilterOp.RANGE:
        return descriptor.source.coerce(raw)
    minimum, separator, maximum = raw.partition(_RANGE_SEPARATOR)
    if not separator:
        raise FilterValueError(
            f"{raw!r} is not a range; expected '<minimum>{_RANGE_SEPARATOR}<maximum>'"
        )
    return (descriptor.source.coerce(minimum), descriptor.source.coerce(maximum))


def parse_filters(
    parameters: Iterable[tuple[str, str]], context: FacetContext
) -> tuple[FilterSelection, ...]:
    """The `filter.*` query parameters, validated against the facets this request
    actually has.

    `parameters` is the whole multi-valued query string
    (`request.query_params.multi_items()`). Anything without the `filter.` prefix
    is ignored here and left to the route's typed signature.

    Every refusal is a `FilterRefusedError`; nothing is dropped silently.

    Selections come back in facet declaration order, not the caller's order, so the
    cursor digest and the rendered SQL are stable across two requests that mean the
    same thing.
    """
    grouped: dict[tuple[str, FilterOp], list[str]] = {}
    ops_by_key: dict[str, FilterOp] = {}
    for name, value in parameters:
        split = _split_parameter(name)
        if split is None:
            continue
        key, op = split
        existing_op = ops_by_key.setdefault(key, op)
        if existing_op is not op:
            raise ConflictingFilterOperatorError(
                f"facet {key!r} was sent with both {existing_op.value!r} and "
                f"{op.value!r}; a filter uses one operator per facet"
            )
        grouped.setdefault((key, op), []).append(value)

    selections: list[FilterSelection] = []
    for (key, op), grouped_values in grouped.items():
        descriptor = context.descriptor(key)
        if descriptor is None:
            if key in context.known_property_keys:
                raise FilterNotAvailableError(
                    f"property {key!r} is not available as a filter on this endpoint"
                )
            raise UnknownFilterKeyError(f"{key!r} is not a filter this endpoint offers")
        if op not in descriptor.source.supported_ops():
            raise UnsupportedFilterOperatorError(
                f"facet {key!r} does not support the {op.value!r} operator"
            )
        # Values within a facet OR together, and OR is idempotent, so
        # `?filter.x=a&filter.x=a` means `?filter.x=a`. `dict.fromkeys` dedupes in
        # first-seen order, avoiding a redundant `EXISTS` and a different cursor
        # digest for the same request.
        raw_values = tuple(dict.fromkeys(grouped_values))
        if len(raw_values) > FILTER_VALUE_CAP:
            raise TooManyFilterValuesError(
                f"facet {key!r} was sent {len(raw_values)} distinct values; "
                f"at most {FILTER_VALUE_CAP} are accepted in one selection"
            )
        selections.append(
            FilterSelection(
                descriptor=descriptor,
                op=op,
                values=tuple(_coerce_one(descriptor, op, raw) for raw in raw_values),
                raw_values=raw_values,
            )
        )
    order = {descriptor.key: index for index, descriptor in enumerate(context.descriptors)}
    selections.sort(key=lambda s: (order[s.key], s.op.value))
    return tuple(selections)


# --- turning selections into SQL ------------------------------------------


def _selection_predicate(selection: FilterSelection) -> ColumnElement[bool]:
    """One facet's whole contribution: its values OR-ed together.

    `FilterOp.IN` gets the whole value list in one call, and `CodeHandler` renders
    it as an indexable `OR` of containments itself. Every other operator is applied
    per value and OR-ed here, so repeating a parameter means "any of these"
    (ADR-0032).
    """
    if selection.op is FilterOp.IN:
        return selection.descriptor.source.predicate(selection.op, list(selection.values))
    clauses = [
        selection.descriptor.source.predicate(selection.op, value) for value in selection.values
    ]
    if len(clauses) == 1:
        return clauses[0]
    return or_(*clauses)


def filter_predicates(
    selections: Sequence[FilterSelection], *, excluding: str | None = None
) -> tuple[ColumnElement[bool], ...]:
    """One predicate per selection, AND-ed by the caller's `where()`.

    Values OR within a facet and AND across facets, the only composition under
    which a second facet narrows rather than broadens (ADR-0032). `excluding`
    drops one facet's own selection, for a drill-down count.
    """
    return tuple(
        _selection_predicate(selection) for selection in selections if selection.key != excluding
    )


def filter_digest_material(selections: Sequence[FilterSelection]) -> str:
    """The canonical text a cursor's digest covers, alongside `q`.

    A search cursor carries a relevance score, which is meaningful only against the
    whole request that produced it: narrowing the filters changes which entries are
    scored. `nptc.catalogue.search` binds the digest to this string as it does to
    `q`.

    Built from `raw_values`, not the coerced ones: two spellings that coerce to the
    same number are different requests to a paging client.

    Every value is length-prefixed (`<byte length>:<value>`, a netstring), not
    joined on a separator. A value can contain any percent-encoded character, so a
    `,`-joined scheme cannot tell `filter.discipline=in=A,B` (two OR-ed values)
    from `filter.discipline=in=A%2CB` (one value containing a comma), and a cursor
    minted under one would page under the other. Length-prefixing needs no
    escaping.

    `raw_values` is sorted before joining, because OR is commutative: the same
    parameters in another order are the same query. Facet order is already
    canonical, since `parse_filters` sorts selections by descriptor order. Without
    the sort, reordering a facet's repeated parameter would raise
    `SearchCursorQueryMismatchError` for an unchanged request.
    """
    segments = []
    for selection in selections:
        values = "".join(f"{len(v.encode())}:{v}" for v in sorted(selection.raw_values))
        segments.append(f"{selection.key}{FILTER_OP_SEPARATOR}{selection.op.value}={values}")
    return "".join(f"{len(segment.encode())}:{segment}" for segment in segments)


# --- counting -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FacetBucket:
    value: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class Facet:
    key: str
    label: str
    #: `False` for a `filterable` property whose handler returns `None` from
    #: `facet_expression()` (`decimal`: every value is its own bucket). It is still
    #: a usable filter, with no buckets. Reported rather than omitted (ADR-0013
    #: SS8, ADR-0032): a vanished facet would look like one whose values match
    #: nothing.
    facetable: bool
    truncated: bool
    buckets: tuple[FacetBucket, ...]


#: Supplied by the caller to say what "the current result set" is:
#: `nptc.catalogue.search` closes over its scored CTE, a plain browse over
#: `catalogue_entry`. Given the predicates to apply, it returns a `SELECT` of
#: entry ids.
BaseEntryIds = Callable[[Sequence[ColumnElement[bool]]], Select[Any]]


def build_facet_count_statement(
    descriptor: FacetDescriptor, base: Select[Any]
) -> Select[Any] | None:
    """One facet's bucket query, or `None` where the facet cannot be grouped.

    Public and separate from `compute_facets`, as
    `nptc.catalogue.search.build_search_statement` is:
    `test_db_property_index_plan.py` `EXPLAIN`s this statement to prove the
    aggregation reaches the generated partial index, and explaining a hand-copied
    approximation would test the copy.

    `base` is a `SELECT` of the entry ids in the current result set, with this
    facet's own selection already excluded by the caller.
    """
    source = descriptor.source.value_source()
    if source is None:
        return None
    subquery = source.subquery(f"facet_values_{descriptor.key}")
    # `bucket_count`, not `count`: a SQLAlchemy `Row` inherits `tuple.count`, so
    # `row.count` would silently yield the bound method.
    count = func.count(distinct(subquery.c.entry_id)).label("bucket_count")
    return (
        sa_select(
            subquery.c.value.label("value"),
            func.max(subquery.c.label).label("label"),
            count,
        )
        .where(subquery.c.entry_id.in_(base))
        # A value that groups to `NULL` has no text form under this handler's
        # facet expression, and a caller could never send `null` back as a filter.
        .where(subquery.c.value.is_not(None))
        .group_by(subquery.c.value)
        # Count, then value: ties would otherwise come back in plan order, and a
        # panel that reshuffles between identical requests looks broken.
        .order_by(count.desc(), subquery.c.value.asc())
        # One more than the cap, as every keyset page asks for one more row than
        # it serves: its existence answers "was this truncated".
        .limit(FACET_BUCKET_CAP + 1)
    )


def build_facet_counts_statement(
    descriptors: Sequence[FacetDescriptor],
    base_for: Callable[[FacetDescriptor], Select[Any]],
) -> Select[Any] | None:
    """Every facetable descriptor's buckets, unioned into one statement, or `None`
    if none can be grouped.

    Postgres does not share a CTE across separately submitted statements, so one
    statement per facet re-ran the caller's scored scan once per facet. A
    `UNION ALL` inside one statement gives Postgres several references to the one
    CTE object `base_for` closes over, so it materialises the scan once whatever the
    facet count (ADR-0032).

    `build_facet_count_statement` is each branch, unmodified, so
    `test_db_property_index_plan.py`'s `EXPLAIN` of it still proves the index and
    the truncation (`FACET_BUCKET_CAP + 1` rows per facet) is unchanged. Each branch
    is a subquery so its own `LIMIT` survives the union.

    Three columns are added. `facet_key` is a literal that attributes rows to their
    descriptor. `value` gets a `Text` cast, because branch value types differ
    (`text`, and `numeric` for `positiveInt`) and `UNION ALL` needs one type per
    column. `bucket_rank` is `row_number() OVER (ORDER BY bucket_count DESC, value
    ASC)`, computed before the cast over the branch's still-typed `value`. It needs
    no `PARTITION BY`, because each branch numbers from 1 independently.

    The outer statement sorts by `bucket_rank`, not the cast column. A `positiveInt`
    value sorts as text `'10' < '100' < '2'` but numerically `2 < 10 < 100`, so
    ordering by the cast would reorder ties and change which row `compute_facets`'
    `rows[:FACET_BUCKET_CAP]` slice drops. ADR-0032 records the defect this closes.

    The cast lives in this wrapper so the grouping expression, and the index plan
    the other module proves, stay untouched.
    """
    branches: list[Select[Any]] = []
    for descriptor in descriptors:
        inner = build_facet_count_statement(descriptor, base_for(descriptor))
        if inner is None:
            continue
        wrapped = inner.subquery(f"facet_counts_{descriptor.key}")
        bucket_rank = (
            func.row_number()
            .over(order_by=(wrapped.c.bucket_count.desc(), wrapped.c.value.asc()))
            .label("bucket_rank")
        )
        branches.append(
            sa_select(
                literal(descriptor.key).label("facet_key"),
                cast(wrapped.c.value, Text).label("value"),
                wrapped.c.label.label("label"),
                wrapped.c.bucket_count.label("bucket_count"),
                bucket_rank,
            )
        )
    if not branches:
        return None
    combined = union_all(*branches).subquery("facet_counts")
    return sa_select(combined).order_by(combined.c.facet_key.asc(), combined.c.bucket_rank.asc())


def compute_facets(
    session: Session,
    *,
    context: FacetContext,
    selections: Sequence[FilterSelection],
    base_entry_ids: BaseEntryIds,
    params: Mapping[str, Any] | None = None,
) -> tuple[Facet, ...]:
    """Every facet's buckets, counted against the current result set in one
    statement (`build_facet_counts_statement`). Each is a `COUNT(DISTINCT
    entry_id)` grouped by the handler's facet expression; the module docstring
    covers `DISTINCT` and why a facet's own selection is excluded from its base.

    **The one legitimate `COUNT` over the catalogue.** The ban in
    `nptc.catalogue.queries` is on counting rows in a page: a total the client did
    not ask for, costing a full scan, which ADR-0024's keyset pagination avoids. A
    facet count is the answer to the question, is bounded by `FACET_BUCKET_CAP`,
    and reads one property's `property_value` rows rather than scanning
    `catalogue_entry` (ADR-0032).
    """

    def base_for(descriptor: FacetDescriptor) -> Select[Any]:
        return base_entry_ids(filter_predicates(selections, excluding=descriptor.key))

    statement = build_facet_counts_statement(context.descriptors, base_for)
    rows_by_key: dict[str, list[Any]] = {}
    if statement is not None:
        for row in session.execute(statement, dict(params or {})).all():
            rows_by_key.setdefault(row.facet_key, []).append(row)

    facets: list[Facet] = []
    for descriptor in context.descriptors:
        if not descriptor.facetable:
            facets.append(
                Facet(
                    key=descriptor.key,
                    label=descriptor.label,
                    facetable=False,
                    truncated=False,
                    buckets=(),
                )
            )
            continue
        rows = rows_by_key.get(descriptor.key, [])
        truncated = len(rows) > FACET_BUCKET_CAP
        buckets = tuple(
            FacetBucket(
                value=str(row.value),
                label=row.label if row.label is not None else str(row.value),
                count=int(row.bucket_count),
            )
            for row in rows[:FACET_BUCKET_CAP]
        )
        facets.append(
            Facet(
                key=descriptor.key,
                label=descriptor.label,
                facetable=True,
                truncated=truncated,
                buckets=buckets,
            )
        )
    return tuple(facets)
