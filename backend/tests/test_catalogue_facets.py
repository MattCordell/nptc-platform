"""`nptc.catalogue.facets` - the facet/filter service (issue #139, FR-16).

Two halves, deliberately in one module.

The **unit** half below needs no database and no HTTP: a `FacetContext` is
just descriptors, and a descriptor is a handler plus a spec, so the whole of
the parsing, validation and predicate-composition surface can be exercised
against handcrafted specs. That is the point of `facets.py` being a service
module rather than router code - the refusals FR-16 asks for are provable
without standing anything up.

The **integration** half (`test_api_public_search.py` and the `EXPLAIN`
tests) proves the things a unit test cannot: that the counts match the rows,
that a multi-valued property counts an entry once per value, that flipping
`filterable` changes the answer with no restart, and that the aggregation
uses #54's generated index.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from nptc.catalogue.facets import (
    FACET_BUCKET_CAP,
    FILTER_VALUE_CAP,
    ConflictingFilterOperatorError,
    FacetContext,
    FacetDescriptor,
    FilterNotAvailableError,
    FilterValueError,
    TooManyFilterValuesError,
    UnknownFilterKeyError,
    UnsupportedFilterOperatorError,
    filter_digest_material,
    filter_predicates,
    parse_filters,
)

from nptc.catalogue.search import _request_digest  # isort: skip
from nptc.registry.datatypes.code import CodeHandler
from nptc.registry.datatypes.decimal import DecimalHandler
from nptc.registry.datatypes.positive_int import PositiveIntHandler
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.handlers import BindingSpec, FilterOp, PropertyDefinitionSpec
from nptc_shared.terminology import StubTerminologyClient

# `facets.py` keeps its two source implementations private on purpose - the
# only supported way to build one is `load_facet_context`, which reads the
# registry. These tests need a context without a database, so they reach for
# the private constructor rather than inventing a parallel one that could
# drift from what production actually builds.
from nptc.catalogue import facets as facets_module  # isort: skip


def _spec(key: str, datatype: str, **overrides: object) -> PropertyDefinitionSpec:
    fields: dict[str, object] = {
        "key": key,
        "label": key.replace("_", " ").title(),
        "datatype": datatype,
        "cardinality": "0..*",
        "scope": frozenset({"submission", "maintenance"}),
        "required_for_submission": False,
        "required_for_publication": False,
        "binding": None,
        "filterable": True,
        "constraints": {},
    }
    fields.update(overrides)
    return PropertyDefinitionSpec(**fields)  # type: ignore[arg-type]


def _property_descriptor(
    key: str, handler: object, spec: PropertyDefinitionSpec, *, display_order: int = 0
) -> FacetDescriptor:
    return FacetDescriptor(
        key=key,
        label=spec.label,
        display_order=display_order,
        source=facets_module._PropertyFacetSource(handler=handler, spec=spec),  # type: ignore[arg-type]
    )


@pytest.fixture
def context() -> FacetContext:
    """One facet of each shape that behaves differently: a coded,
    multi-valued property (`@>` containment, an object value), a plain
    string (text equality and prefix), a `positiveInt` (a coerced numeric),
    and a `decimal` (filterable but *not* facetable). Plus the declared
    core-column status facet, which is not a property at all."""
    coded = _spec(
        "specimen",
        "code",
        binding=BindingSpec(
            binding_target="value_set",
            value_set_uri="http://example.org/vs",
            strength="required",
            edition="au",
        ),
    )
    return FacetContext(
        descriptors=(
            *facets_module._core_facets(status_values=("active",)),
            _property_descriptor(
                "specimen", CodeHandler(terminology_client=StubTerminologyClient()), coded
            ),
            _property_descriptor(
                "discipline", StringHandler(), _spec("discipline", "string"), display_order=1
            ),
            _property_descriptor(
                "volume_ml",
                PositiveIntHandler(),
                _spec("volume_ml", "positiveInt"),
                display_order=2,
            ),
            _property_descriptor(
                "turnaround_days",
                DecimalHandler(),
                _spec("turnaround_days", "decimal"),
                display_order=3,
            ),
        ),
        known_property_keys=frozenset(
            {"specimen", "discipline", "volume_ml", "turnaround_days", "internal_note"}
        ),
    )


def _sql(clause: object) -> str:
    return str(clause.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def _rendered(clause: object) -> str:
    """As `_sql`, but with `literal_execute` parameters expanded - which is
    what actually reaches PostgreSQL, and the only spelling in which the
    `property_key` literal is visible at all."""
    return str(
        clause.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
        )
    )


# --- the facet list is derived, not declared ------------------------------


@pytest.mark.req("FR-16")
def test_the_core_status_facet_is_a_descriptor_like_any_other(context: FacetContext) -> None:
    """The acceptance criterion the plan is most explicit about: status is
    present *by declaration*, not by a special case inside the builder. If
    it were special-cased, this assertion would need to know about it."""
    status = context.descriptor("status")
    assert status is not None
    assert status.facetable
    # It parses, validates and renders through the identical code path as a
    # registry-derived facet - no branch on `key == "status"` anywhere.
    selections = parse_filters([("filter.status", "active")], context)
    assert len(filter_predicates(selections)) == 1


@pytest.mark.req("FR-16")
def test_a_filterable_but_ungroupable_property_is_filterable_and_not_facetable(
    context: FacetContext,
) -> None:
    """ADR-0013 SS8's "stated cost", asserted rather than assumed: a
    `decimal` property's handler returns `None` from `facet_expression()`,
    so it has no buckets - but it must still be usable as a filter, and it
    must not simply vanish from the facet list."""
    descriptor = context.descriptor("turnaround_days")
    assert descriptor is not None
    assert descriptor.facetable is False
    # Still a filter, and `decimal` supports RANGE but not IN.
    selections = parse_filters([("filter.turnaround_days:range", "1..5")], context)
    assert selections[0].values == ((1.0, 5.0),)


# --- refusals -------------------------------------------------------------


@pytest.mark.req("FR-16")
def test_an_unknown_filter_key_is_refused(context: FacetContext) -> None:
    with pytest.raises(UnknownFilterKeyError):
        parse_filters([("filter.not_a_property", "x")], context)


@pytest.mark.req("FR-16")
def test_a_known_but_non_filterable_key_is_refused(context: FacetContext) -> None:
    """Distinct from "unknown" in the log, identical in the response - see
    `FilterNotAvailableError`. The failure this guards against is the
    opposite one: a non-filterable key silently ignored, which serves the
    caller a page that answers a different question than the one asked."""
    with pytest.raises(FilterNotAvailableError):
        parse_filters([("filter.internal_note", "x")], context)


@pytest.mark.req("FR-16")
def test_an_operator_the_handler_does_not_support_is_refused(context: FacetContext) -> None:
    """`prefix` on a coded property: the value is an object, so there is no
    prefix to take, and `CodeHandler.supported_filter_ops()` says so."""
    assert (
        FilterOp.PREFIX
        not in CodeHandler(terminology_client=StubTerminologyClient()).supported_filter_ops()
    )
    with pytest.raises(UnsupportedFilterOperatorError):
        parse_filters([("filter.specimen:prefix", "abc")], context)


@pytest.mark.req("FR-16")
def test_an_operator_this_api_does_not_know_is_refused(context: FacetContext) -> None:
    with pytest.raises(UnsupportedFilterOperatorError):
        parse_filters([("filter.discipline:contains", "abc")], context)


@pytest.mark.req("FR-16")
def test_a_value_that_is_not_of_the_property_s_shape_is_refused(context: FacetContext) -> None:
    """A `positiveInt` property compared against a word. Refused at parse
    time rather than left for the database to raise as a 500 - the fault is
    in the request, so the status has to say so."""
    with pytest.raises(FilterValueError):
        parse_filters([("filter.volume_ml", "not a number")], context)


@pytest.mark.req("FR-16")
def test_a_malformed_range_is_refused(context: FacetContext) -> None:
    with pytest.raises(FilterValueError):
        parse_filters([("filter.turnaround_days:range", "5")], context)


@pytest.mark.req("FR-16")
def test_an_unrecognised_status_value_is_refused(context: FacetContext) -> None:
    """`status` has no `PropertyDefinition` and so no handler to validate
    against - it must check itself. Before this check existed, a typo
    silently matched zero rows (an empty page indistinguishable from a
    legitimately empty one) instead of the 422 every other unusable filter
    value earns."""
    with pytest.raises(FilterValueError):
        parse_filters([("filter.status", "activee")], context)


@pytest.mark.req("FR-16")
def test_a_status_value_outside_this_surface_is_refused_not_silently_empty(
    context: FacetContext,
) -> None:
    """`draft` is a real `CatalogueEntryStatus` member - it is not a typo,
    the way `test_an_unrecognised_status_value_is_refused`'s value is. But
    this `context` was built with `status_values=("active",)`, matching a
    public route, and `?filter.status=draft` there is well-formed and would
    match zero rows forever: refusing it is what tells a caller their filter
    can never do anything, rather than letting them believe an empty
    catalogue is an empty search."""
    with pytest.raises(FilterValueError):
        parse_filters([("filter.status", "draft")], context)


@pytest.mark.req("FR-16")
def test_the_same_facet_with_two_operators_is_refused(context: FacetContext) -> None:
    """Grouping selections by `(key, op)` would otherwise silently accept
    `?filter.discipline=chemistry&filter.discipline:in=haematology` as two
    separate selections on the same key, ANDed together into a predicate no
    row can satisfy - a caller error should be a 422, not a silent
    always-empty result."""
    with pytest.raises(ConflictingFilterOperatorError):
        parse_filters(
            [("filter.discipline", "chemistry"), ("filter.discipline:in", "haematology")], context
        )


@pytest.mark.req("FR-16")
def test_more_than_the_value_cap_in_one_selection_is_refused(context: FacetContext) -> None:
    """`FACET_BUCKET_CAP` bounds a facet's response; nothing bounded the
    request before this existed. For any operator but `IN`,
    `_selection_predicate` turns each repeated value into its own
    correlated `EXISTS` subquery, `or_`-ed together - an unbounded repeat
    count is an unbounded `OR` chain on an unauthenticated endpoint."""
    too_many = [("filter.discipline", str(i)) for i in range(FILTER_VALUE_CAP + 1)]
    with pytest.raises(TooManyFilterValuesError):
        parse_filters(too_many, context)


@pytest.mark.req("FR-16")
def test_exactly_the_value_cap_is_accepted(context: FacetContext) -> None:
    at_cap = [("filter.discipline", str(i)) for i in range(FILTER_VALUE_CAP)]
    selections = parse_filters(at_cap, context)
    assert len(selections[0].values) == FILTER_VALUE_CAP


# --- composition ----------------------------------------------------------


@pytest.mark.req("FR-16")
def test_repeating_a_key_ors_within_the_facet_and_ands_across_facets(
    context: FacetContext,
) -> None:
    selections = parse_filters(
        [
            ("filter.discipline", "chemistry"),
            ("filter.discipline", "haematology"),
            ("filter.specimen", "119297000"),
        ],
        context,
    )
    predicates = filter_predicates(selections)
    # Two facets -> two predicates, AND-ed by the caller's `where()`.
    assert len(predicates) == 2
    discipline = next(p for p in predicates if "'discipline'" in _rendered(p))
    # The repeated key becomes one OR-ed predicate, not two AND-ed ones -
    # AND across values within a facet can only ever match an entry holding
    # both, which is not what a facet checkbox means.
    assert " OR " in _sql(discipline)


@pytest.mark.req("FR-16")
def test_every_property_predicate_is_an_exists_over_property_value(
    context: FacetContext,
) -> None:
    """The multi-valued criterion, at the SQL level. A join would return an
    entry once per matching `property_value` row; `EXISTS` returns it once.
    The integration half asserts the resulting counts, this asserts the
    shape that makes them possible."""
    selections = parse_filters([("filter.specimen", "119297000")], context)
    rendered = _sql(filter_predicates(selections)[0])
    assert "EXISTS" in rendered
    assert "property_value" in rendered


@pytest.mark.req("FR-16")
def test_the_property_key_is_rendered_as_a_literal_not_a_placeholder(
    context: FacetContext,
) -> None:
    """Issue #54's generated index is partial on `property_key = '<literal>'`
    and a bound key defeats it under a generic plan
    (`test_db_property_index_plan.py` proves both halves). `literal_execute`
    is what keeps the predicate provably covered - and it is SQLAlchemy's
    own escaping, not string-built SQL (NFR-22)."""
    selections = parse_filters([("filter.discipline", "chemistry")], context)
    assert "'discipline'" in _rendered(filter_predicates(selections)[0])


@pytest.mark.req("FR-16")
def test_a_facet_s_own_selection_is_excluded_from_its_own_count(
    context: FacetContext,
) -> None:
    """Drill-down: counting `discipline`'s buckets applies every *other*
    facet's filters and not its own, so a user who has chosen one value can
    still see what the siblings would give them."""
    selections = parse_filters(
        [("filter.discipline", "chemistry"), ("filter.specimen", "119297000")], context
    )
    assert len(filter_predicates(selections, excluding="discipline")) == 1
    assert len(filter_predicates(selections, excluding="specimen")) == 1
    assert len(filter_predicates(selections)) == 2


# --- the cursor digest ----------------------------------------------------


@pytest.mark.req("FR-16")
def test_the_digest_material_changes_with_the_filter_set(context: FacetContext) -> None:
    """A relevance cursor is only meaningful against the request that minted
    it. Narrowing the filters changes which entries exist to be scored, so a
    replayed cursor would select a window with no defined meaning - refused,
    exactly as a cursor replayed under a different `q` already is."""
    one = filter_digest_material(parse_filters([("filter.discipline", "chemistry")], context))
    two = filter_digest_material(parse_filters([("filter.discipline", "haematology")], context))
    none = filter_digest_material(())
    assert one != two != none
    assert one != none


@pytest.mark.req("FR-16")
def test_the_digest_material_is_stable_across_parameter_order(context: FacetContext) -> None:
    """Two requests that mean the same thing must mint the same cursor -
    otherwise a client that reorders its own query string pages forever."""
    forward = filter_digest_material(
        parse_filters(
            [("filter.discipline", "chemistry"), ("filter.specimen", "119297000")], context
        )
    )
    reverse = filter_digest_material(
        parse_filters(
            [("filter.specimen", "119297000"), ("filter.discipline", "chemistry")], context
        )
    )
    assert forward == reverse


@pytest.mark.req("FR-16")
def test_the_digest_material_is_stable_across_repeated_value_order(
    context: FacetContext,
) -> None:
    """Facet *order* is canonicalised by the test above; within one facet,
    OR is commutative and `?filter.discipline=a&filter.discipline=b` means
    the same thing as the same request with the two swapped. Before
    `raw_values` was sorted inside `filter_digest_material`, a client that
    reordered its own repeated parameter got a different digest for an
    unchanged request - `SearchCursorQueryMismatchError` for a cursor that
    should have paged."""
    forward = filter_digest_material(
        parse_filters([("filter.discipline:in", "a"), ("filter.discipline:in", "b")], context)
    )
    reverse = filter_digest_material(
        parse_filters([("filter.discipline:in", "b"), ("filter.discipline:in", "a")], context)
    )
    assert forward == reverse


@pytest.mark.req("FR-16")
def test_the_digest_material_does_not_collide_across_a_literal_separator(
    context: FacetContext,
) -> None:
    """A caller can send either two repeated values or one value that
    happens to contain the character a naive join would use as the list
    separator - `?filter.discipline=in=A&filter.discipline:in=B` (two OR'd
    values) versus one value spelled `"A,B"` (a single literal). A `,`-joined
    digest cannot tell them apart, and two selections with different
    meaning would then mint the same cursor - length-prefixing (this
    function's actual encoding) is what rules that out regardless of what a
    value contains."""
    two_values = filter_digest_material(
        parse_filters([("filter.discipline:in", "A"), ("filter.discipline:in", "B")], context)
    )
    one_value_with_a_comma = filter_digest_material(
        parse_filters([("filter.discipline:in", "A,B")], context)
    )
    assert two_values != one_value_with_a_comma


@pytest.mark.req("FR-16")
def test_q_cannot_impersonate_filter_material_in_the_request_digest(
    context: FacetContext,
) -> None:
    """`search._request_digest` binds a cursor to `q` and the filter set
    together. Before `q` was length-prefixed, nothing stopped it from
    ending in text that happened to read as a well-formed continuation of
    `filter_digest_material`'s own encoding - so a request with a real
    filter selection could digest identically to a filterless request whose
    `q` was crafted to contain that filter material as literal text, and a
    cursor minted under one would be accepted under the other."""
    selections = parse_filters([("filter.discipline", "chemistry")], context)
    real_filter_request = _request_digest("glucose", selections)
    impersonated_q = "glucose" + filter_digest_material(selections)
    impersonating_request = _request_digest(impersonated_q, ())
    assert real_filter_request != impersonating_request


@pytest.mark.req("FR-16")
def test_non_filter_query_parameters_are_ignored(context: FacetContext) -> None:
    """`q`, `limit` and `after` share the query string with the filters, and
    the dotted prefix is exactly what keeps them from colliding."""
    assert (
        parse_filters([("q", "glucose"), ("limit", "50"), ("after", "NPTC-000001")], context) == ()
    )


def test_the_bucket_cap_is_a_named_constant() -> None:
    """ADR-0032 argues the number; this asserts it is stated once rather
    than repeated at a call site where it could drift."""
    assert FACET_BUCKET_CAP == 20
