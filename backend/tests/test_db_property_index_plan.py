"""The `EXPLAIN` proof issue #54 (FR-13) itself demands: that a filtered
query over a filterable property's `property_value` rows actually uses the
generated index, under a *generic* plan - not merely that the index exists.

ADR-0012 poses two claims that must be kept separate:

- The **index**'s own predicate, `WHERE property_key = '<literal>'`, is
  fixed by the DDL (`nptc.db.property_indexes.create_statement`) - not in
  question here.
- The **query** must carry a predicate the planner can *prove* implies
  that literal. `property_key = $1` under a generic plan cannot be proven
  to imply `property_key = 'some_key'` - the planner has no way to know
  what `$1` will be at execution time, so it cannot conclude the partial
  index's rows even qualify. That is the claim this module proves, with a
  positive case and a negative control.

**No `enable_seqscan = off`, deliberately, unlike `test_db_search_index.py`
(which does use it).** There, the rival plan was a legal-but-cheaper index
- disabling seqscan removes a cost race without weakening the assertion,
because an unindexable predicate still cannot become an `Index Cond`
regardless. Here, the cost/provability decision *is* the whole claim: a
generic-plan bind parameter genuinely cannot use this partial index, and
forcing the seqscan off would either fail loudly (no legal plan exists,
which is itself the intended proof) or - worse - mask the distinction this
module exists to draw between "the literal is provably covered" and "the
planner was left no alternative". The literal-vs-parameter cases below are
compared as a pair for exactly this reason.

Marked `integration`: a query plan is a database fact - there is no
unit-level substitute (NFR-39).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ClauseElement, Executable
from testcontainers.community.postgres import PostgresContainer

from nptc.catalogue.facets import (
    FacetDescriptor,
    _PropertyFacetSource,
    build_facet_count_statement,
    build_facet_counts_statement,
)
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.property_definition import (
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)
from nptc.db.models.property_value import PropertyValue
from nptc.db.property_indexes import index_name
from nptc.db.property_reconciler import get_indexer_engine, reconcile_property_indexes
from nptc.registry.datatypes.code import CodeHandler
from nptc.registry.datatypes.positive_int import PositiveIntHandler
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.handlers import FilterOp, PropertyDefinitionSpec
from nptc_shared.terminology.stub import StubTerminologyClient

#: Large enough that the partial index is a real candidate, not a fixture
#: artefact - see test_db_search_index.py's own note on why this figure is
#: about resembling a real catalogue's shape.
_ROW_COUNT = 5_000

#: `:prefix` distinguishes each test's own rows (both tests in this module
#: commit real data via a connection separate from the reconciler's own,
#: so nothing here can rely on a rolled-back `db` fixture the way
#: `test_db_search_index.py`'s bulk fixture does - cleanup is explicit,
#: per issue #190's rule, and a shared prefix would let one test's cleanup
#: delete the other's still-running fixture data).
_BULK_ENTRIES_SQL = text("""
INSERT INTO catalogue_entry (business_key, preferred_term, status)
SELECT
    :prefix || lpad(g::text, 8, '0'),
    'plan fixture ' || md5(g::text),
    'active'
FROM generate_series(1, :count) AS g
""")

#: Split across two property keys, dissimilar values (md5 hex), so the
#: partial predicate does real work: half the table's rows belong to the
#: *other* key and must never be scanned to answer a query about this one.
_BULK_STRING_VALUES_SQL = text("""
INSERT INTO property_value (entry_id, property_key, ordinal, value)
SELECT
    e.id,
    CASE WHEN (row_number() OVER (ORDER BY e.id)) % 2 = 0
         THEN :key_a ELSE :key_b END,
    0,
    to_jsonb('val ' || md5(e.business_key))
FROM catalogue_entry AS e
WHERE e.business_key LIKE :prefix || '%'
""")


class _Explain(Executable, ClauseElement):
    """`EXPLAIN <statement>`, executed through SQLAlchemy's normal pipeline
    - unlike `exec_driver_sql`, this still applies each column's bind
    processor, so `CodeHandler.filter_clause`'s `@>` containment (a JSONB
    dict bound directly, not text) reaches psycopg correctly typed rather
    than as a bare Python `dict` it cannot adapt. Compiled statements are
    cached per class by default (`inherit_cache`); this one is deliberately
    excluded (`inherit_cache = False`) since the wrapped statement's own
    literal values differ by call, and stale caching an `EXPLAIN` around
    the wrong bound values would be a subtle, hard-to-notice test bug."""

    inherit_cache = False

    def __init__(self, statement: ClauseElement) -> None:
        self.statement = statement


@compiles(_Explain)
def _compile_explain(element: _Explain, compiler: object, **kw: object) -> str:
    return "EXPLAIN " + compiler.process(element.statement, **kw)  # type: ignore[attr-defined]


def _delete_bulk_entries(owner_engine: Engine, prefix: str) -> None:
    with owner_engine.connect() as connection:
        connection.execute(
            text(
                "DELETE FROM property_value WHERE entry_id IN "
                "(SELECT id FROM catalogue_entry WHERE business_key LIKE :prefix || '%')"
            ),
            {"prefix": prefix},
        )
        connection.execute(
            text("DELETE FROM catalogue_entry WHERE business_key LIKE :prefix || '%'"),
            {"prefix": prefix},
        )
        connection.commit()


@pytest.fixture
def _indexer_configured(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch, migrated: None
) -> Iterator[None]:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", postgres_container.get_connection_url())
    get_indexer_engine.cache_clear()
    yield
    get_indexer_engine.cache_clear()


@pytest.fixture
def _string_property_pair(owner_engine: Engine) -> Iterator[dict[str, int]]:
    """Two filterable `string` properties - the second exists purely so the
    partial index's predicate has something to exclude."""
    keys = ["test_plan_string_a", "test_plan_string_b"]
    index_seqs: dict[str, int] = {}
    with Session(bind=owner_engine) as session:
        for key in keys:
            definition = PropertyDefinition(
                key=key,
                label=key,
                datatype="string",
                cardinality=PropertyCardinality.ZERO_OR_ONE,
                scope=PropertyScope.BOTH,
                required_for_submission=False,
                required_for_publication=False,
                filterable=True,
                origin=PropertyOrigin.ADMIN,
                display_order=0,
                constraints={},
            )
            session.add(definition)
            session.flush()
            index_seqs[key] = definition.index_seq
        session.commit()
    try:
        yield index_seqs
    finally:
        with owner_engine.connect() as connection:
            connection.execute(
                text("DELETE FROM property_value WHERE property_key = ANY(:keys)"),
                {"keys": keys},
            )
            connection.execute(
                text("DELETE FROM property_definition WHERE key = ANY(:keys)"), {"keys": keys}
            )
            connection.commit()
        with owner_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            for seq in index_seqs.values():
                connection.execute(
                    text(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name(seq, 1)}"')
                )


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_partial_index_is_used_under_a_literal_property_key_but_not_a_generic_plan(
    owner_engine: Engine, _indexer_configured: None, _string_property_pair: dict[str, int]
) -> None:
    prefix = "NPTC-91"
    key_a = "test_plan_string_a"
    key_b = "test_plan_string_b"
    index_name_a = index_name(_string_property_pair[key_a], 1)

    with owner_engine.connect() as connection:
        connection.execute(_BULK_ENTRIES_SQL, {"count": _ROW_COUNT, "prefix": prefix})
        connection.execute(
            _BULK_STRING_VALUES_SQL, {"key_a": key_a, "key_b": key_b, "prefix": prefix}
        )
        connection.commit()

    try:
        _run_partial_index_plan_proof(owner_engine, key_a, index_name_a)
    finally:
        _delete_bulk_entries(owner_engine, prefix)


def _run_partial_index_plan_proof(owner_engine: Engine, key_a: str, index_name_a: str) -> None:
    report = reconcile_property_indexes()
    assert index_name_a in report.created

    with owner_engine.connect() as connection:
        connection.execute(text("ANALYZE property_value"))
        connection.commit()

        probe_value = connection.execute(
            text(
                "SELECT value #>> '{}' FROM property_value "
                "WHERE property_key = :key ORDER BY entry_id LIMIT 1"
            ),
            {"key": key_a},
        ).scalar_one()

        handler = StringHandler()
        stmt = select(PropertyValue.entry_id).where(
            PropertyValue.property_key == key_a,
            handler.filter_clause(FilterOp.EQUALS, probe_value, PropertyValue.value),
        )
        literal_sql = str(
            stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )

        # --- positive: the literal-rendered query uses the partial index ---
        # (a "Bitmap Index Scan on <name>" under a "Bitmap Heap Scan", at
        # this row count - either shape names the index, so this checks
        # for the name rather than one specific scan type.)
        plan = "\n".join(connection.execute(text("EXPLAIN " + literal_sql)).scalars().all())
        assert index_name_a in plan, plan
        assert "Index Cond" in plan, plan
        assert "Seq Scan on property_value" not in plan, plan

        # --- PREFIX also uses the index (issue #54 review): switching the
        # TEXT_SCALAR opclass to text_pattern_ops is only proven by this
        # module if PREFIX itself gets an EXPLAIN case, not just EQUALS.
        # `startswith(..., autoescape=True)` renders `col LIKE <lit> || '%'
        # ESCAPE '/'` - two constants either side of `||`, which Postgres
        # constant-folds into one literal at plan time, so this is the same
        # "provably constant" shape EQUALS above relies on.
        prefix_stmt = select(PropertyValue.entry_id).where(
            PropertyValue.property_key == key_a,
            handler.filter_clause(FilterOp.PREFIX, "val ", PropertyValue.value),
        )
        prefix_literal_sql = str(
            prefix_stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        prefix_plan = "\n".join(
            connection.execute(text("EXPLAIN " + prefix_literal_sql)).scalars().all()
        )
        assert index_name_a in prefix_plan, prefix_plan
        assert "Seq Scan on property_value" not in prefix_plan, prefix_plan

        # --- the shape production actually sends (issue #54 review, third
        # pass): `filter_clause` never renders `literal_binds=True` in real
        # code - the compiled statement above proves the index is usable in
        # principle, but the real predicate production emits is
        # `LIKE $1 || '%' ESCAPE '/'`, a bound parameter concatenated with a
        # literal, not one folded constant. `_Explain`, not `text()` +
        # `literal_binds`, executes this exactly as a real caller would:
        # through SQLAlchemy's normal bind-parameter path. A single ad hoc
        # execution like this - no `PREPARE`, no repeated execution - gets
        # Postgres's own per-execution "custom plan", which substitutes the
        # bound value before planning, same as the literal case; the
        # `force_generic_plan` negative control below is what demonstrates
        # the *other* case (a plan reused across many distinct parameter
        # values), which is what would defeat this.
        realistic_prefix_stmt = select(PropertyValue.entry_id).where(
            PropertyValue.property_key == key_a,
            handler.filter_clause(FilterOp.PREFIX, "val ", PropertyValue.value),
        )
        realistic_prefix_plan = "\n".join(
            connection.execute(_Explain(realistic_prefix_stmt)).scalars().all()
        )
        assert index_name_a in realistic_prefix_plan, realistic_prefix_plan
        assert "Seq Scan on property_value" not in realistic_prefix_plan, realistic_prefix_plan

        # --- negative control: the identical predicate shape, but with
        # property_key bound as a parameter under a forced generic plan,
        # cannot use the partial index at all - proving the literal above
        # is load-bearing, not incidental, with no planner-persuasion knob
        # touched. Built as a literal SQL string, not via `filter_clause`
        # (which always renders a literal, never a placeholder) - the
        # point here is `property_key` specifically as `$1`, which nothing
        # in production code ever does, so there is no module-level
        # constant to import as `test_db_search_index.py`'s own `_explain`
        # helper does. String concatenation into `text()` is confined to
        # this test tree (`test_sql_parameterisation.py` scans only
        # `backend/src`/`backend/migrations`), and `probe_value` is a
        # 32-character md5 hex digest with a fixed `'val '` prefix - never
        # a value that could contain a quote.
        connection.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
        connection.execute(
            text(
                "PREPARE plan_proof_stmt (text) AS "
                "SELECT entry_id FROM property_value "
                f"WHERE property_key = $1 AND (value #>> '{{}}') = '{probe_value}'"
            )
        )
        generic_plan = "\n".join(
            connection.execute(text(f"EXPLAIN EXECUTE plan_proof_stmt ('{key_a}')")).scalars().all()
        )
        connection.execute(text("DEALLOCATE plan_proof_stmt"))

    assert index_name_a not in generic_plan, generic_plan


@pytest.fixture
def _code_property(owner_engine: Engine) -> Iterator[int]:
    """One filterable, multi-valued (`0..*`) `code` property - AC 3's
    "Specimen-shaped" case."""
    key = "test_plan_code_property"
    with Session(bind=owner_engine) as session:
        definition = PropertyDefinition(
            key=key,
            label=key,
            datatype="code",
            cardinality=PropertyCardinality.ZERO_OR_MANY,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            binding_target=BindingTarget.VALUE_SET,
            value_set_uri="http://example.org/vs",
            strength="required",
            edition="au",
            filterable=True,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
        session.add(definition)
        session.commit()
        index_seq = definition.index_seq
    try:
        yield index_seq
    finally:
        with owner_engine.connect() as connection:
            connection.execute(
                text("DELETE FROM property_value WHERE property_key = :key"), {"key": key}
            )
            connection.execute(
                text("DELETE FROM property_definition WHERE key = :key"), {"key": key}
            )
            connection.commit()
        with owner_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name(index_seq, 1)}"')
            )


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_gin_index_serves_a_multi_valued_coded_property_on_any_ordinal(
    owner_engine: Engine, _indexer_configured: None, _code_property: int
) -> None:
    prefix = "NPTC-93"
    key = "test_plan_code_property"
    expected_name = index_name(_code_property, 1)
    first_entry = f"{prefix}00000001"
    second_entry = f"{prefix}00000002"

    with owner_engine.connect() as connection:
        # Two entries, each with two ordinals - the value that must match
        # sits at ordinal 1 on one entry and ordinal 0 on the other, so a
        # query that only ever looked at ordinal 0 would still (wrongly)
        # seem to work.
        connection.execute(_BULK_ENTRIES_SQL, {"count": 200, "prefix": prefix})
        connection.execute(
            text(
                "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
                "SELECT id, :key, 0, "
                "jsonb_build_object('system', 'http://example.org', 'code', 'no-match') "
                "FROM catalogue_entry "
                "WHERE business_key LIKE :prefix || '%' "
                "AND business_key NOT IN (:first_entry, :second_entry) "
                "LIMIT 100"
            ),
            {
                "key": key,
                "prefix": prefix,
                "first_entry": first_entry,
                "second_entry": second_entry,
            },
        )
        connection.execute(
            text(
                "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
                "SELECT id, :key, 1, "
                "jsonb_build_object('system', 'http://example.org', 'code', 'target-code') "
                "FROM catalogue_entry WHERE business_key = :entry"
            ),
            {"key": key, "entry": first_entry},
        )
        connection.execute(
            text(
                "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
                "SELECT id, :key, 0, "
                "jsonb_build_object('system', 'http://example.org', 'code', 'target-code') "
                "FROM catalogue_entry WHERE business_key = :entry"
            ),
            {"key": key, "entry": second_entry},
        )
        connection.commit()

    try:
        _run_gin_index_plan_proof(owner_engine, key, expected_name)
    finally:
        _delete_bulk_entries(owner_engine, prefix)


def _run_gin_index_plan_proof(owner_engine: Engine, key: str, expected_name: str) -> None:
    report = reconcile_property_indexes()
    assert expected_name in report.created

    with owner_engine.connect() as connection:
        connection.execute(text("ANALYZE property_value"))
        connection.commit()

        # `_Explain`, not `literal_binds=True`: `CodeHandler.filter_clause`'s
        # `@>` containment binds a JSONB dict directly, which SQLAlchemy
        # core has no literal renderer for at all (unlike the string case
        # above, where the bound value is plain text via
        # `jsonb_root_as_text`). This test's own claim doesn't need a
        # literal `property_key` either - the generic-plan negative control
        # above already carries that proof; this one only needs "the real
        # handler-built query uses the GIN index and returns every ordinal
        # that matches".
        handler = CodeHandler(terminology_client=StubTerminologyClient())
        stmt = select(PropertyValue.entry_id).where(
            PropertyValue.property_key == key,
            handler.filter_clause(FilterOp.EQUALS, "target-code", PropertyValue.value),
        )

        plan = "\n".join(connection.execute(_Explain(stmt)).scalars().all())
        assert "Bitmap Index Scan" in plan and expected_name in plan, plan

        matches = connection.execute(stmt).scalars().all()

    assert len(matches) == 2  # both entries, regardless of which ordinal held the match


# --- FR-16's own two plans (issue #139) -----------------------------------


def _facet_descriptor(key: str) -> FacetDescriptor:
    """A descriptor for one of `_string_property_pair`'s properties, built
    the way `nptc.catalogue.facets.load_facet_context` builds one - a
    handler plus a spec - so this explains the real predicate rather than a
    hand-written lookalike."""
    return FacetDescriptor(
        key=key,
        label=key,
        display_order=0,
        source=_PropertyFacetSource(
            handler=StringHandler(),
            spec=PropertyDefinitionSpec(
                key=key,
                label=key,
                datatype="string",
                cardinality="0..1",
                scope=frozenset({"submission", "maintenance"}),
                required_for_submission=False,
                required_for_publication=False,
                binding=None,
                filterable=True,
                constraints={},
            ),
        ),
    )


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_facet_filter_and_count_plans_are_both_index_supported(
    owner_engine: Engine, _indexer_configured: None, _string_property_pair: dict[str, int]
) -> None:
    """FR-16 issues two shapes of query, and this is the only test that can
    tell either of them from a full scan: every functional facet test in
    `test_api_public_search.py` returns identical answers whether the query
    reads one property's rows or the whole `property_value` table, and the
    difference shows up only as a slow catalogue.

    Both statements are built through `nptc.catalogue.facets` itself, for the
    reason `test_db_search_index.py` explains the real search statement: a
    hand-copied approximation is a test of the copy.

    **The filter predicate reaches issue #54's generated index**, which is
    the claim #139 inherits from #54 and the reason `_PropertyFacetSource`
    renders `property_key` with `literal_execute=True` at all. The `EXISTS`
    subquery it builds is the same `(value #>> '{}') = ...` shape the
    literal-vs-parameter proof above `EXPLAIN`s directly, now composed as a
    correlated subquery over `catalogue_entry`.

    **The count aggregation does not, and cannot**, and saying so plainly is
    better than an assertion that quietly means less than it looks like it
    does. The generated index holds the *value expression* only; a facet
    count also needs `entry_id`, for the `COUNT(DISTINCT ...)` that keeps a
    multi-valued property from counting an entry several times. No index-only
    scan can serve both, so the planner reads the property's rows through
    `ix_property_value_property_key` instead. What matters - and what is
    asserted - is that the aggregation costs the size of *this property*
    rather than the size of the whole table: an `Index Cond` on
    `property_key`, and no sequential scan.

    **`enable_seqscan = off`, unlike the two tests above.** They are about
    *provability* - whether a bound `property_key` lets the planner conclude
    the partial index qualifies at all - and forcing the knob would destroy
    exactly that distinction. This test's claim is the other one: that both
    statements are *expressed* so an index can serve them. Whether the
    planner picks one on a given day is a cost decision about table size, and
    a test that turned on winning that race would be a test of the fixture's
    row count. Disabling sequential scans removes the cost race and weakens
    nothing: a predicate over an expression the index does not contain still
    cannot become an `Index Cond`, however expensive the alternative is made.
    """
    prefix = "NPTC-95"
    key_a = "test_plan_string_a"
    key_b = "test_plan_string_b"
    index_name_a = index_name(_string_property_pair[key_a], 1)

    with owner_engine.connect() as connection:
        connection.execute(_BULK_ENTRIES_SQL, {"count": _ROW_COUNT, "prefix": prefix})
        connection.execute(
            _BULK_STRING_VALUES_SQL, {"key_a": key_a, "key_b": key_b, "prefix": prefix}
        )
        connection.commit()

    try:
        report = reconcile_property_indexes()
        assert index_name_a in report.created

        descriptor = _facet_descriptor(key_a)
        base = select(CatalogueEntry.id).where(
            CatalogueEntry.status == CatalogueEntryStatus.ACTIVE.value
        )
        count_statement = build_facet_count_statement(descriptor, base)
        assert count_statement is not None

        with owner_engine.connect() as connection:
            connection.execute(text("ANALYZE property_value"))
            connection.execute(text("ANALYZE catalogue_entry"))
            probe_value = connection.execute(
                text(
                    "SELECT value #>> '{}' FROM property_value "
                    "WHERE property_key = :key ORDER BY entry_id LIMIT 1"
                ),
                {"key": key_a},
            ).scalar_one()
            # LOCAL: reverted with this connection's own transaction, so it
            # cannot leak into another test sharing the container.
            connection.execute(text("SET LOCAL enable_seqscan = off"))

            filter_statement = select(CatalogueEntry.id).where(
                descriptor.source.predicate(FilterOp.EQUALS, probe_value)
            )
            filter_plan = "\n".join(connection.execute(_Explain(filter_statement)).scalars().all())
            count_plan = "\n".join(connection.execute(_Explain(count_statement)).scalars().all())

        assert index_name_a in filter_plan, filter_plan
        assert "Seq Scan on property_value" not in filter_plan, filter_plan

        assert "Index Cond: (property_key = 'test_plan_string_a'::text)" in count_plan, count_plan
        assert "Seq Scan on property_value" not in count_plan, count_plan
    finally:
        _delete_bulk_entries(owner_engine, prefix)


@pytest.fixture
def _positive_int_property(owner_engine: Engine, migrated: None) -> Iterator[None]:
    """One filterable `positiveInt` property - a `numeric`-valued facet, the
    shape `test_the_combined_statement_preserves_a_numeric_facet_s_native_ordering`
    needs and none of this module's other fixtures give it."""
    key = "test_plan_positive_int"
    with Session(bind=owner_engine) as session:
        definition = PropertyDefinition(
            key=key,
            label=key,
            datatype="positiveInt",
            cardinality=PropertyCardinality.ZERO_OR_ONE,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            filterable=True,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
        session.add(definition)
        session.commit()
    try:
        yield
    finally:
        with owner_engine.connect() as connection:
            connection.execute(
                text("DELETE FROM property_value WHERE property_key = :key"), {"key": key}
            )
            connection.execute(
                text("DELETE FROM property_definition WHERE key = :key"), {"key": key}
            )
            connection.commit()


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_combined_statement_preserves_a_numeric_facet_s_native_ordering(
    owner_engine: Engine, _positive_int_property: None
) -> None:
    """PR #315 review: `build_facet_counts_statement` casts `value` to `TEXT`
    so a `UNION ALL` across heterogeneous handler value types can share one
    column. An earlier version of that statement's outer `ORDER BY` sorted
    on the *cast* column - which reorders a numeric facet, since `'10' <
    '100' < '2'` as text while `2 < 10 < 100` as numbers, and worse, changes
    which row the bucket cap truncates away.

    Three entries, one `positiveInt` value each (10, 100, 2, deliberately in
    that creation order - it is also the *text*-sorted order, so this test
    fails under the old ordering and passes only under the numeric one), all
    with an equal bucket count of 1: the case a `bucket_count DESC` tie-break
    alone cannot distinguish, so only the value ordering is left to get right
    or wrong.
    """
    key = "test_plan_positive_int"
    prefix = "NPTC-97"
    try:
        with owner_engine.connect() as connection:
            connection.execute(_BULK_ENTRIES_SQL, {"count": 3, "prefix": prefix})
            connection.commit()
            entry_ids = (
                connection.execute(
                    text(
                        "SELECT id FROM catalogue_entry WHERE business_key LIKE :prefix || '%' "
                        "ORDER BY business_key"
                    ),
                    {"prefix": prefix},
                )
                .scalars()
                .all()
            )
            for entry_id, value in zip(entry_ids, [10, 100, 2], strict=True):
                connection.execute(
                    text(
                        "INSERT INTO property_value (entry_id, property_key, ordinal, value) "
                        "VALUES (:entry_id, :key, 0, to_jsonb(:value))"
                    ),
                    {"entry_id": entry_id, "key": key, "value": value},
                )
            connection.commit()

        descriptor = FacetDescriptor(
            key=key,
            label=key,
            display_order=0,
            source=_PropertyFacetSource(
                handler=PositiveIntHandler(),
                spec=PropertyDefinitionSpec(
                    key=key,
                    label=key,
                    datatype="positiveInt",
                    cardinality="0..1",
                    scope=frozenset({"submission", "maintenance"}),
                    required_for_submission=False,
                    required_for_publication=False,
                    binding=None,
                    filterable=True,
                    constraints={},
                ),
            ),
        )
        base = select(CatalogueEntry.id).where(CatalogueEntry.business_key.like(f"{prefix}%"))
        statement = build_facet_counts_statement((descriptor,), lambda _d: base)
        assert statement is not None

        with owner_engine.connect() as connection:
            rows = connection.execute(statement).all()
    finally:
        _delete_bulk_entries(owner_engine, prefix)

    assert [row.value for row in rows] == ["2", "10", "100"]
