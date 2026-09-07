"""Faceted filtering over the catalogue, derived from the property registry
at request time (issue #139, FR-16, FR-09).

See `docs/adr/0032-faceted-filter-query-surface.md` for the query surface
this module implements and the alternatives that were rejected.

**Nothing here names a datatype, ever.** A facet's grouping expression, the
operators it accepts and the predicate it renders all come from the
property's `DatatypeHandler` (`nptc.registry.handlers`) - `facet_expression()`,
`supported_filter_ops()` and `filter_clause()`, the three members ADR-0013
added for precisely this caller. That is what makes FR-16's actual
requirement true: an administrator flips `filterable` on a property and it
becomes a facet, with no code change and no restart (FR-09), because the
facet list is a `SELECT` against `property_definition` on every request
rather than a constant in this file. `backend/tests/
test_datatype_dispatch.py`'s AST guard enforces the no-switch half
mechanically; the rest is this module's own discipline.

**Two kinds of facet, one builder.** Most facets are registry-derived. Entry
`status` is a *declared core-column* facet: it lives on `catalogue_entry`,
not in `property_value`, so there is no `PropertyDefinition` to enumerate it
from - but it is declared as a descriptor of the same shape and consumed by
the identical predicate/aggregation code (`_core_facets` below). The only
branch between them is which `FacetSource` a descriptor holds, which is a
fact about *where the value is stored*, never about its datatype.

**Filter predicates are `EXISTS` subqueries, and that is load-bearing.** A
property may be multi-valued (`specimen`, cardinality `0..*`), so an entry
can own several `property_value` rows for one key. A join would return that
entry once per matching row and count it several times; `EXISTS` asks
whether *any* value matches and yields the entry exactly once. The same
property makes `COUNT(DISTINCT entry_id)` - not `COUNT(*)` - the right
aggregate on the facet side: an entry with seven specimens must contribute
one to each of seven buckets, never seven to one.

**`property_key` is rendered as a literal, on purpose.** Issue #54 generates
one partial index per filterable property, predicated on
`property_key = '<literal>'`. A *bound* key defeats that index under a
generic plan - the planner cannot prove `$1` will equal the literal the
index is partial on, so it cannot use the index at all
(`backend/tests/test_db_property_index_plan.py` proves both halves). The key
is therefore bound with `literal_execute=True`, which renders it inline at
execution time through SQLAlchemy's own escaping. This is not string-built
SQL (NFR-22): the value never touches the statement text in Python, and
`property_key` is constrained by the database to `^[a-z][a-z0-9_]{0,62}$`
besides.

**Facet counts exclude their own facet's selection.** Computing
`discipline`'s buckets applies `q` and every *other* facet's filters, but
not `discipline`'s - standard drill-down, so a user who has picked
"Chemistry" can still see how many entries "Haematology" would give them
and switch. Applying a facet's own filter to its own counts produces the
degenerate answer (one bucket, the one already chosen) and makes the panel
a dead end.

**Labels come from the stored value, never from a terminology call**
(FR-54). A coded value is stored as `{"system", "code", "display"}` and the
bucket is labelled from that stored `display`. The label expression is
`jsonb_extract_path_text(value, 'display')`, which is generic JSON handling
- it returns `NULL` for a value that is not an object with that member, and
the bucket then falls back to its own grouping value. Nothing here knows
that `code` is the datatype that happens to have a `display`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol
from typing import cast as type_cast

from sqlalchemy import ColumnElement, Select, Text, bindparam, cast, distinct, exists, func, or_
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
    "compute_facets",
    "filter_digest_material",
    "filter_predicates",
    "load_facet_context",
    "parse_filters",
]

#: At most this many buckets per facet, ordered by count descending. A
#: `string` property with thousands of distinct values would otherwise
#: return thousands of buckets on an unauthenticated endpoint, and no facet
#: panel is usable past a couple of dozen entries anyway.
#:
#: It is an invented number, in the same category as ADR-0024's threshold
#: discipline, so it is named here and argued in ADR-0032 rather than
#: buried at a call site. `Facet.truncated` says when it bit, so a client
#: is never quietly shown a partial list it cannot tell from a whole one.
FACET_BUCKET_CAP: Final[int] = 20

#: At most this many values in one facet's selection - `?filter.discipline=`
#: repeated this many times, or an `:in` list this long. `FACET_BUCKET_CAP`
#: bounds the *response*; nothing bounded the *request* before this existed.
#: For any operator but `IN`, `_selection_predicate` builds one correlated
#: `EXISTS` subquery per value and `or_`s them, so an uncapped repeat count
#: is an uncapped `OR` chain on an unauthenticated endpoint - and
#: `compute_facets` re-runs that chain, embedded in the whole scored CTE,
#: once per facet (the query-cost amplification issue #275 tracks). In the
#: same spirit as `limit`'s 200: an invented number, not a tuned one, and
#: named here rather than left implicit in whatever the query planner
#: happens to tolerate.
FILTER_VALUE_CAP: Final[int] = 50

#: `?filter.discipline=chem&filter.discipline=haem`. The dotted prefix keeps
#: the filter namespace from colliding with `q`/`limit`/`after` or with any
#: parameter added later, and repeating the key is the natural spelling of
#: "any of these" (ADR-0032).
FILTER_PARAM_PREFIX: Final[str] = "filter."

#: `?filter.volume_ml:range=1..5`. Omitted, the operator is `equals`.
FILTER_OP_SEPARATOR: Final[str] = ":"

#: `1..5`. Only meaningful for `FilterOp.RANGE`, which is the only operator
#: whose value is a pair rather than a scalar.
_RANGE_SEPARATOR: Final[str] = ".."

#: The operator names the query surface accepts, mapped to the handler
#: enum. Spelled out rather than derived from `FilterOp.__members__` so the
#: wire vocabulary is a deliberate choice rather than a rename away from a
#: silent contract change (FR-20).
_OPERATOR_NAMES: Final[Mapping[str, FilterOp]] = {
    "equals": FilterOp.EQUALS,
    "in": FilterOp.IN,
    "prefix": FilterOp.PREFIX,
    "range": FilterOp.RANGE,
}

_DEFAULT_OPERATOR: Final[FilterOp] = FilterOp.EQUALS


# --- refusals -------------------------------------------------------------
#
# Every one of these is a 422 and every one of them is *loud*. A filter the
# server did not understand and quietly dropped is the worst possible
# outcome: the caller is served a page that looks like an answer to the
# question they asked and is an answer to a different one, with nothing in
# the response to tell them apart.


class FilterRefusedError(ValueError):
    """Base for every reason a filter parameter is unusable.

    One base class, so `nptc.api.errors` maps the family with one handler
    and one client-facing sentence - from a caller's point of view these
    are one fault ("this filter is not one this endpoint accepts"). The
    subclasses exist for the log, which records the exception class.
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
    """The same facet key was sent with more than one distinct operator -
    `?filter.discipline=Chemistry&filter.discipline:in=Haematology`, say.

    Selections are grouped by `(key, op)` (see `parse_filters`), so two
    different operators on the same key would otherwise become two separate
    `FilterSelection`s that `filter_predicates` ANDs together as if they
    were two different facets. For most operator pairs that AND is
    satisfiable by nothing (`discipline == 'Chemistry' AND discipline IN
    ('Haematology')`), so the caller would get a silent, always-empty
    result instead of the 422 every other unusable filter earns."""


class TooManyFilterValuesError(FilterRefusedError):
    """One facet's selection repeated the parameter, or gave an `:in` list,
    more than `FILTER_VALUE_CAP` times.

    `FACET_BUCKET_CAP` bounds a facet's *response*; this is the request-side
    counterpart. Without it, any operator but `IN` turns into one correlated
    `EXISTS` subquery per value, `or_`-ed together, with no upper bound but
    the query string's own length limit - and `compute_facets` re-runs that
    chain, embedded in the whole scored CTE, once per facet on the page."""


class FilterValueError(FilterRefusedError):
    """The value cannot be interpreted as this property's values are - a
    non-numeric string for a `positiveInt` property, or a `range` that is
    not `<min>..<max>`."""


# --- what a facet is ------------------------------------------------------


class FacetSource(Protocol):
    """Where one facet's values live, and how to ask about them.

    Two implementations: a registry property (values in `property_value`,
    behaviour from its `DatatypeHandler`) and a declared core column
    (values on `catalogue_entry`). Everything above this Protocol -
    parsing, predicate composition, aggregation, truncation - is written
    once against it.
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


#: JSON Schema's own type vocabulary, mapped to the Python value the
#: comparison needs. Taken from the handler's `json_schema_fragment()` -
#: the contract member whose job is to say what shape this property's
#: values are - so this is generic JSON Schema handling, not a datatype
#: switch: the same three lines would serve any schema from any source, and
#: no datatype name appears in them (FR-77, ADR-0013).
#:
#: Anything else - notably `"object"`, which is how a coding declares
#: itself - passes through as the raw string, because that is what the
#: handler's own `filter_clause` takes for it (`CodeHandler` builds a
#: `@> {"code": <value>}` containment, so the filter value *is* the code).
_JSON_TYPE_COERCIONS: Final[Mapping[str, Callable[[str], Any]]] = {
    "integer": int,
    "number": float,
}


def _value_column() -> ColumnElement[Any]:
    """`property_value.value`, as the plain `ColumnElement` the handler
    contract takes.

    The ORM attribute is an `InstrumentedAttribute` carrying the JSONB
    column's own Python union type, which is narrower than
    `ColumnElement[Any]` and so is not assignable to it under
    `mypy --strict`. Named once here rather than cast at each of the three
    call sites, so the reason is written down once too.
    """
    return type_cast("ColumnElement[Any]", PropertyValue.value)


@dataclass(frozen=True, slots=True)
class _PropertyFacetSource:
    """A facet over a `filterable` `PropertyDefinition`."""

    handler: DatatypeHandler
    spec: PropertyDefinitionSpec

    @property
    def _key_literal(self) -> ColumnElement[Any]:
        """`property_key` rendered inline at execution time, so #54's
        partial index is provably covered - see the module docstring."""
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
        # EXISTS, never a join - see the module docstring on why a
        # multi-valued property makes this the difference between a correct
        # count and a multiplied one.
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
                # Generic JSON handling: NULL for any value that is not an
                # object carrying `display`, which the caller falls back
                # from. No terminology call (FR-54), and no datatype named.
                func.jsonb_extract_path_text(PropertyValue.value, "display").label("label"),
            )
            .select_from(PropertyValue)
            .where(PropertyValue.property_key == self._key_literal)
        )


@dataclass(frozen=True, slots=True)
class _CoreColumnFacetSource:
    """A facet over a column of `catalogue_entry` itself.

    Declared, not discovered: there is no `PropertyDefinition` for entry
    status, so `_core_facets` states it. It produces descriptors of exactly
    the shape the registry-derived ones produce, so no caller - and no line
    of the predicate or aggregation code - has a special case for it.
    """

    column: ColumnElement[Any]
    #: The values this *surface* may filter on - not every value
    #: `CatalogueEntryStatus` admits. `/catalogue/search` and
    #: `/catalogue/entries` restrict every other query to
    #: `queries.PUBLIC_STATUSES`; accepting `?filter.status=draft` there
    #: would be well-formed and would silently match nothing, the same
    #: empty-page-indistinguishable-from-a-legitimate-one outcome refusing
    #: an unrecognised value altogether exists to prevent. A future admin
    #: listing (#266) passes the whole enum here instead - the restriction
    #: is the caller's fact about which rows this surface can ever show, not
    #: something this module hard-codes.
    allowed_values: frozenset[str]

    def supported_ops(self) -> frozenset[FilterOp]:
        return frozenset({FilterOp.EQUALS, FilterOp.IN})

    def coerce(self, raw: str) -> Any:
        # `status` has no PropertyDefinition and so no handler to validate
        # against - refusing anything outside this surface's own permitted
        # set is the closest equivalent, and the reason it must be refused
        # rather than merely fail to match is the same as everywhere else in
        # this module: a silently empty page is indistinguishable from a
        # correct one.
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
    #: Ordering only, matching `PropertyDefinition.display_order`'s own
    #: meaning. Core facets take a negative one so they lead, which is a
    #: presentation choice rather than a claim about their importance.
    display_order: int
    source: FacetSource

    @property
    def facetable(self) -> bool:
        """`False` for a property that is filterable but cannot be grouped
        - see `Facet.facetable`."""
        return self.source.value_source() is not None


#: Named so the "a core facet is not special-cased" test can assert against
#: the same value a descriptor carries, rather than retyping it. Independent
#: of `_core_facets`'s `status_values` argument: the set of *keys* a surface
#: declares does not depend on which values it permits.
CORE_FACET_KEYS: Final[frozenset[str]] = frozenset({"status"})


def _core_facets(status_values: Iterable[str]) -> tuple[FacetDescriptor, ...]:
    """The declared core-column facets. One entry today (ADR-0032); the
    tuple shape exists so a second is a data change rather than a new code
    path.

    `status_values` is the caller's own fact about which rows this surface
    can ever show - not a constant this module could bake in. On the public
    API it is `queries.PUBLIC_STATUSES`, which is `active` alone, so the
    facet has exactly one bucket there; a future admin listing (#266) passes
    the whole `CatalogueEntryStatus` set instead. Accepting a value outside
    that set would be well-formed and would silently match nothing - the
    same empty-page-indistinguishable-from-a-legitimate-one outcome this
    module refuses everywhere else, so `_CoreColumnFacetSource.coerce`
    refuses it too rather than merely failing to match.
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


@dataclass(frozen=True, slots=True)
class FacetContext:
    """Every facet available on this request, plus enough about the keys
    that are *not* available to refuse them accurately.

    Built per request, never cached: caching it is exactly the "needs a
    restart" behaviour FR-09 forbids.
    """

    descriptors: tuple[FacetDescriptor, ...]
    #: Every `property_definition.key`, whatever its status or `filterable`
    #: flag - so an unknown key and a known-but-unavailable one can be told
    #: apart in the log (see `FilterNotAvailableError`).
    known_property_keys: frozenset[str]

    def descriptor(self, key: str) -> FacetDescriptor | None:
        for descriptor in self.descriptors:
            if descriptor.key == key:
                return descriptor
        return None


def load_facet_context(
    session: Session, registry: DatatypeRegistry, *, status_values: Iterable[str]
) -> FacetContext:
    """Enumerate the facets, from `property_definition`, now.

    `status_values` is this *surface's* permitted `catalogue_entry.status`
    values - `queries.PUBLIC_STATUSES` for the public routes - and is
    threaded through to the declared core-column `status` facet so it
    refuses a value this surface could never show rather than silently
    matching nothing (see `_core_facets`).

    One `list_definitions(audience=EXPORT)` call, not two: `EXPORT` returns
    every property regardless of status, and `DATA_ENTRY` is exactly that
    set filtered to `PropertyStatus.ACTIVE` (`nptc.db.definitions`'s own
    docstring) - a second round trip to re-derive a strict subset of rows
    already in hand would cost every request on both public collection
    endpoints for no answer a client ever sees, only a distinction
    (`UnknownFilterKeyError` vs `FilterNotAvailableError`) that reaches the
    log and nothing else.

    A deprecated property's stored values are still *served* (FR-11), but it
    is no longer offered for new entries and offering it as a filter would
    invite a client to build a UI around a property the catalogue has
    retired - so only the active, filterable ones become descriptors, while
    `known_property_keys` keeps every key regardless of status.
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

    `raw_values` is kept alongside the coerced `values` because the cursor
    digest fingerprints what the caller sent, not what this module made of
    it - the same reason `search._query_digest` fingerprints `q` verbatim.
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
    """The `filter.*` query parameters, validated against the facets this
    request actually has.

    `parameters` is the whole multi-valued query string (FastAPI's
    `request.query_params.multi_items()`); anything without the `filter.`
    prefix is ignored here and handled by the route's own typed signature.

    Every refusal is a `FilterRefusedError`. Nothing is dropped silently -
    see that class's own docstring for why that is the whole point.

    Selections come back in the order their facets are declared, not the
    order the caller happened to send them, so the cursor digest and the
    rendered SQL are stable across two requests that mean the same thing.
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
    for (key, op), raw_values in grouped.items():
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
        if len(raw_values) > FILTER_VALUE_CAP:
            raise TooManyFilterValuesError(
                f"facet {key!r} was sent {len(raw_values)} values; "
                f"at most {FILTER_VALUE_CAP} are accepted in one selection"
            )
        selections.append(
            FilterSelection(
                descriptor=descriptor,
                op=op,
                values=tuple(_coerce_one(descriptor, op, raw) for raw in raw_values),
                raw_values=tuple(raw_values),
            )
        )
    order = {descriptor.key: index for index, descriptor in enumerate(context.descriptors)}
    selections.sort(key=lambda s: (order[s.key], s.op.value))
    return tuple(selections)


# --- turning selections into SQL ------------------------------------------


def _selection_predicate(selection: FilterSelection) -> ColumnElement[bool]:
    """One facet's whole contribution: its values OR-ed together.

    `FilterOp.IN` is handed the whole value list in one call - that is what
    the operator means, and `CodeHandler` in particular renders it as an
    indexable `OR` of containments itself. Every other operator is applied
    per value and OR-ed here, which is what makes repeating a parameter the
    natural spelling of "any of these" (ADR-0032).
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

    Within a facet values OR; across facets they AND - the behaviour every
    faceted search has, and the only one under which adding a second facet
    narrows rather than broadens.

    `excluding` drops one facet's own selection, which is what a
    drill-down count needs: see the module docstring.
    """
    return tuple(
        _selection_predicate(selection) for selection in selections if selection.key != excluding
    )


def filter_digest_material(selections: Sequence[FilterSelection]) -> str:
    """The canonical text a cursor's digest covers, alongside `q`.

    A search cursor carries a relevance score, and a score is only
    meaningful against the *whole* request that produced it - narrowing the
    filter set changes which entries exist to be scored, so replaying a
    cursor under a different filter set selects a window with no defined
    meaning. `nptc.catalogue.search` binds the digest to this string for
    exactly the reason it already binds it to `q`.

    Built from `raw_values`, not the coerced ones: the question is "is this
    the same request", and two spellings that coerce to the same number are
    still two different requests as far as a client's paging loop is
    concerned.

    Every value is length-prefixed (`<byte length>:<value>`, a netstring)
    rather than joined on a fixed separator. A query parameter's value can
    contain any character the caller cares to percent-encode, including
    whatever separator this function might otherwise pick - a `,`-joined
    scheme, for instance, cannot tell `filter.discipline=in=A,B` (two OR'd
    values) apart from `filter.discipline=in=A%2CB` (one value that happens
    to contain a comma), so two selections with different meaning would
    digest identically and a cursor minted under one would silently page
    under the other. Length-prefixing makes that collision impossible
    regardless of what a value contains, with no escaping needed.

    `raw_values` is sorted before it is joined, not taken in the order the
    caller repeated the parameter. Facet *order* is already canonicalised
    (the loop below walks `context.descriptors`, and `parse_filters` sorts
    selections the same way), but within one facet `?filter.discipline=a&
    filter.discipline=b` and the same request with the two swapped mean the
    same query - OR is commutative - and would otherwise mint two different
    digests, handing a client that happens to reorder its own repeated
    parameter a `SearchCursorQueryMismatchError` for a request that did not
    change.
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
    #: `False` for a `filterable` property whose handler returns `None`
    #: from `facet_expression()` - `decimal`, where every value is its own
    #: bucket and grouping says nothing. Such a property is still a usable
    #: *filter*; it simply has no buckets to offer. Reported explicitly
    #: rather than omitted, which is ADR-0013 SS8's "stated cost": a facet
    #: that vanished silently would look to a client exactly like one whose
    #: values happen to match nothing.
    facetable: bool
    truncated: bool
    buckets: tuple[FacetBucket, ...]


#: The callable a caller supplies to say what "the current result set" is.
#: `nptc.catalogue.search` closes over its scored CTE; a plain browse
#: closes over `catalogue_entry`. Given the predicates to apply, it returns
#: a `SELECT` of entry ids.
BaseEntryIds = Callable[[Sequence[ColumnElement[bool]]], Select[Any]]


def build_facet_count_statement(
    descriptor: FacetDescriptor, base: Select[Any]
) -> Select[Any] | None:
    """One facet's bucket query, or `None` where the facet cannot be
    grouped at all.

    Public, and separate from `compute_facets`, for the same reason
    `nptc.catalogue.search.build_search_statement` is:
    `backend/tests/test_db_property_index_plan.py` `EXPLAIN`s this statement
    to prove the aggregation reaches issue #54's generated partial index,
    and explaining a hand-copied approximation would be a test of the copy.

    `base` is a `SELECT` of the entry ids in the current result set, with
    this facet's *own* selection already excluded by the caller.
    """
    source = descriptor.source.value_source()
    if source is None:
        return None
    subquery = source.subquery(f"facet_values_{descriptor.key}")
    # `bucket_count`, not `count`: a SQLAlchemy `Row` inherits `tuple.count`,
    # so a column labelled `count` is reachable only positionally and
    # `row.count` silently yields the bound method.
    count = func.count(distinct(subquery.c.entry_id)).label("bucket_count")
    return (
        sa_select(
            subquery.c.value.label("value"),
            func.max(subquery.c.label).label("label"),
            count,
        )
        .where(subquery.c.entry_id.in_(base))
        # A value that groups to `NULL` is not a bucket: it is a row whose
        # stored value has no text form under this handler's own facet
        # expression, and a `null` bucket is not something a caller could
        # ever send back as a filter value.
        .where(subquery.c.value.is_not(None))
        .group_by(subquery.c.value)
        # Count first, then the value itself: ties would otherwise come back
        # in whatever order the plan happened to produce, and a facet panel
        # that reshuffles between two identical requests looks broken.
        .order_by(count.desc(), subquery.c.value.asc())
        # One more than the cap, exactly as every keyset page here asks for
        # one more row than it serves: its existence *is* the answer to
        # "was this truncated".
        .limit(FACET_BUCKET_CAP + 1)
    )


def compute_facets(
    session: Session,
    *,
    context: FacetContext,
    selections: Sequence[FilterSelection],
    base_entry_ids: BaseEntryIds,
    params: Mapping[str, Any] | None = None,
) -> tuple[Facet, ...]:
    """Every facet's buckets, counted against the current result set.

    One statement per facet, each one `COUNT(DISTINCT entry_id)` grouped by
    the handler's own facet expression - see the module docstring on why
    `DISTINCT` and why the facet's own selection is excluded from its own
    base.

    **This is the one place a `COUNT` over the catalogue is legitimate**,
    and `nptc.catalogue.queries`'s own docstring records the exception. That
    ban is on counting *rows in a page* - a total the client did not ask
    for, that costs a full scan and that ADR-0024's keyset pagination
    deliberately does without. A facet count is the opposite: it is the
    answer to the question, it is bounded by `FACET_BUCKET_CAP` buckets, and
    it is served from #54's per-property partial index rather than a scan of
    `catalogue_entry`.
    """
    facets: list[Facet] = []
    for descriptor in context.descriptors:
        base = base_entry_ids(filter_predicates(selections, excluding=descriptor.key))
        statement = build_facet_count_statement(descriptor, base)
        if statement is None:
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
        rows = session.execute(statement, dict(params or {})).all()
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
