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
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.handlers import FilterOp
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
