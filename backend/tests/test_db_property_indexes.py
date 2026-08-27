"""Naming, desired-state, and reconciler tests for issue #54 (FR-13).

The first section (naming/desired-state/statement builders) needs no
container. The second (`reconcile_property_indexes`) does - see
`test_db_property_index_plan.py` for the `EXPLAIN` proof and
`test_db_numeric_or_null_function.py` for the cast-safe numeric expression.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from nptc.db.models.property_definition import (
    BindingTarget,
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)
from nptc.db.property_indexes import (
    GENERATED_INDEX_NAME_RE,
    DesiredIndex,
    comment_statement,
    create_statement,
    desired_indexes,
    drop_statement,
    include_object,
    index_name,
    matches_indexdef,
)
from nptc.db.property_reconciler import (
    RECONCILE_LOCK_KEY,
    IndexerNotConfiguredError,
    get_indexer_engine,
    reconcile_property_indexes,
)
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps, IndexKind, ValueExpression
from nptc_shared.terminology.stub import StubTerminologyClient

_BIGINT_MAX = 2**63 - 1


def _registry() -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(HandlerDeps(terminology_client=StubTerminologyClient()))
    )


def _definition(
    *,
    key: str,
    index_seq: int,
    datatype: str,
    filterable: bool,
    binding_target: str | None = None,
    local_code_system_key: str | None = None,
    value_set_uri: str | None = None,
    strength: str | None = None,
    edition: str | None = None,
) -> PropertyDefinition:
    """An unattached `PropertyDefinition` - never persisted, so `index_seq`
    (normally a database `Identity` column) can be set directly like any
    other Python attribute. Enough of the model to exercise `desired_
    indexes()` without a database at all."""
    definition = PropertyDefinition(
        key=key,
        label=key.title(),
        datatype=datatype,
        cardinality=PropertyCardinality.ZERO_OR_MANY,
        scope=PropertyScope.BOTH,
        required_for_submission=False,
        required_for_publication=False,
        binding_target=binding_target,
        local_code_system_key=local_code_system_key,
        value_set_uri=value_set_uri,
        strength=strength,
        edition=edition,
        filterable=filterable,
        origin=PropertyOrigin.ADMIN,
        display_order=0,
        constraints={},
    )
    definition.index_seq = index_seq
    return definition


def test_index_name_is_at_most_33_bytes_and_matches_the_regex() -> None:
    """ADR-0012's by-construction safety argument: the fixed 12-byte
    prefix, `index_seq`'s worst case of 19 digits (a signed 64-bit BIGINT's
    maximum magnitude), a 1-byte separator, and a single-digit slot is at
    most 33 bytes - provably under Postgres's 63-byte identifier limit,
    not merely typically so."""
    name = index_name(_BIGINT_MAX, 1)

    assert len(name.encode("utf-8")) <= 33
    assert GENERATED_INDEX_NAME_RE.match(name)


def test_index_name_never_contains_the_property_key() -> None:
    name = index_name(42, 1)

    assert "discipline" not in name
    assert name == "ix_propval_p42_1"


def test_generated_index_name_regex_rejects_slot_zero_and_three() -> None:
    assert not GENERATED_INDEX_NAME_RE.match("ix_propval_p42_0")
    assert not GENERATED_INDEX_NAME_RE.match("ix_propval_p42_3")
    assert not GENERATED_INDEX_NAME_RE.match("ix_propval_p42_1_extra")


def test_desired_indexes_excludes_non_filterable_property() -> None:
    """`usage_guidance` is the real-world instance of this shape (`nptc.db.
    bootstrap`), seeded `filterable=False`."""
    definitions = [
        _definition(key="usage_guidance", index_seq=1, datatype="string", filterable=False)
    ]

    assert desired_indexes(definitions, _registry()) == []


def test_desired_indexes_includes_filterable_code_property_as_gin() -> None:
    definitions = [
        _definition(
            key="discipline",
            index_seq=7,
            datatype="code",
            filterable=True,
            binding_target=BindingTarget.LOCAL_CODE_SYSTEM,
            local_code_system_key="discipline",
        )
    ]

    desired = desired_indexes(definitions, _registry())

    assert desired == [
        DesiredIndex(
            property_key="discipline",
            index_seq=7,
            kind=IndexKind.GIN,
            expression=ValueExpression.RAW_JSONB,
        )
    ]
    assert desired[0].name == "ix_propval_p7_1"


def test_desired_indexes_includes_filterable_string_property_as_expression_btree() -> None:
    definitions = [_definition(key="admin_text", index_seq=99, datatype="string", filterable=True)]

    desired = desired_indexes(definitions, _registry())

    assert desired == [
        DesiredIndex(
            property_key="admin_text",
            index_seq=99,
            kind=IndexKind.EXPRESSION_BTREE,
            expression=ValueExpression.TEXT_SCALAR,
        )
    ]


def test_desired_indexes_includes_filterable_decimal_property_as_expression_btree() -> None:
    definitions = [
        _definition(key="admin_number", index_seq=100, datatype="decimal", filterable=True)
    ]

    desired = desired_indexes(definitions, _registry())

    assert desired == [
        DesiredIndex(
            property_key="admin_number",
            index_seq=100,
            kind=IndexKind.EXPRESSION_BTREE,
            expression=ValueExpression.NUMERIC_SCALAR,
        )
    ]


def test_desired_indexes_is_empty_for_no_definitions() -> None:
    assert desired_indexes([], _registry()) == []


def test_create_statement_renders_property_key_as_a_literal_not_a_placeholder() -> None:
    """The partial index predicate must carry the property key as a SQL
    literal, not a bind parameter (ADR-0012) - `test_db_property_index_
    plan.py` proves *why* this matters under a generic plan; this test only
    proves the statement is actually built that way."""
    desired = DesiredIndex(
        property_key="discipline",
        index_seq=7,
        kind=IndexKind.GIN,
        expression=ValueExpression.RAW_JSONB,
    )

    rendered = create_statement(desired).as_string(None)

    assert "CONCURRENTLY" in rendered
    assert "ix_propval_p7_1" in rendered
    assert "'discipline'" in rendered
    assert "$1" not in rendered
    assert "%s" not in rendered


def test_create_statement_text_scalar_uses_the_astext_expression() -> None:
    desired = DesiredIndex(
        property_key="admin_text",
        index_seq=99,
        kind=IndexKind.EXPRESSION_BTREE,
        expression=ValueExpression.TEXT_SCALAR,
    )

    rendered = create_statement(desired).as_string(None)

    assert "value #>> '{}'" in rendered
    assert "text_pattern_ops" in rendered
    assert "jsonb_path_ops" not in rendered


def test_create_statement_numeric_scalar_uses_the_cast_safe_function() -> None:
    desired = DesiredIndex(
        property_key="admin_number",
        index_seq=100,
        kind=IndexKind.EXPRESSION_BTREE,
        expression=ValueExpression.NUMERIC_SCALAR,
    )

    rendered = create_statement(desired).as_string(None)

    assert "nptc_numeric_or_null(value #>> '{}')" in rendered


def test_drop_statement_is_concurrent_conditional_and_schema_qualified() -> None:
    """Schema-qualified (`public.<name>`), not bare - issue #54 review: the
    indexer role's own `search_path` need not put `public` first, and
    `DROP INDEX`/`COMMENT ON INDEX` (unlike `CREATE INDEX`) accept a
    schema-qualified object name."""
    rendered = drop_statement("ix_propval_p7_1").as_string(None)

    assert rendered == 'DROP INDEX CONCURRENTLY IF EXISTS "public"."ix_propval_p7_1"'


def test_comment_statement_carries_the_property_key() -> None:
    rendered = comment_statement("ix_propval_p7_1", "discipline").as_string(None)

    assert rendered == 'COMMENT ON INDEX "public"."ix_propval_p7_1" IS \'discipline\''


def test_create_statement_qualifies_the_table_not_the_index_name() -> None:
    """A bare `CREATE INDEX name ON ...` cannot itself be schema-qualified -
    Postgres always creates an index in its table's own schema - so
    `create_statement` qualifies the *table* reference instead (issue #54
    review), unlike `drop_statement`/`comment_statement` above."""
    desired = DesiredIndex(
        property_key="discipline",
        index_seq=7,
        kind=IndexKind.GIN,
        expression=ValueExpression.RAW_JSONB,
    )

    rendered = create_statement(desired).as_string(None)

    assert "ON public.property_value" in rendered


# --- matches_indexdef (issue #54 review) ------------------------------------


def test_matches_indexdef_true_for_the_definition_it_would_itself_render() -> None:
    desired = DesiredIndex(
        property_key="admin_text",
        index_seq=99,
        kind=IndexKind.EXPRESSION_BTREE,
        expression=ValueExpression.TEXT_SCALAR,
    )
    indexdef = (
        "CREATE INDEX ix_propval_p99_1 ON public.property_value USING btree "
        "(((value #>> '{}'::text[])) text_pattern_ops) WHERE (property_key = 'admin_text'::text)"
    )

    assert matches_indexdef(desired, indexdef) is True


def test_matches_indexdef_false_after_a_datatype_amendment() -> None:
    """A property amended from `string` to `decimal` keeps the same index
    name (`index_seq` never changes), but the actual index still carries
    the old `TEXT_SCALAR` expression - unusable for the new datatype's
    `filter_clause`."""
    desired = DesiredIndex(
        property_key="admin_text",
        index_seq=99,
        kind=IndexKind.EXPRESSION_BTREE,
        expression=ValueExpression.NUMERIC_SCALAR,
    )
    indexdef = (
        "CREATE INDEX ix_propval_p99_1 ON public.property_value USING btree "
        "(((value #>> '{}'::text[])) text_pattern_ops) WHERE (property_key = 'admin_text'::text)"
    )

    assert matches_indexdef(desired, indexdef) is False


def test_matches_indexdef_false_after_a_key_rename() -> None:
    """The index predicate still names the property's old `key` - a rename
    is the other mutable column an index name alone cannot notice."""
    desired = DesiredIndex(
        property_key="admin_text_renamed",
        index_seq=99,
        kind=IndexKind.EXPRESSION_BTREE,
        expression=ValueExpression.TEXT_SCALAR,
    )
    indexdef = (
        "CREATE INDEX ix_propval_p99_1 ON public.property_value USING btree "
        "(((value #>> '{}'::text[])) text_pattern_ops) WHERE (property_key = 'admin_text'::text)"
    )

    assert matches_indexdef(desired, indexdef) is False


def test_include_object_excludes_a_generated_index() -> None:
    assert include_object(object(), "ix_propval_p7_1", "index", False, None) is False


def test_include_object_includes_a_hand_written_index() -> None:
    assert include_object(object(), "ix_property_value_property_key", "index", False, None) is True


def test_include_object_includes_non_index_objects_regardless_of_name() -> None:
    assert include_object(object(), "ix_propval_p7_1", "table", False, None) is True


def test_include_object_includes_an_index_with_no_name() -> None:
    assert include_object(object(), None, "index", False, None) is True


# --- reconciler (issue #54, FR-13) -----------------------------------------
#
# `postgres_container`'s own `nptc_owner` role is the container's bootstrap
# superuser, so `NPTC_INDEXER_DATABASE_URL` points at the same DSN `db`/
# `owner_engine` already use - a real deployment would scope this to a
# narrower DDL-capable role instead (see `IndexerSettings`'s own docstring),
# but reusing the fixture graph's existing owner credential here needs no
# second role provisioned just for this test module.


@pytest.fixture
def _indexer_configured(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
    migrated: None,
) -> Iterator[None]:
    """Points the module-level `get_indexer_engine()` cache at the running
    container for the duration of one test, then clears it - the cache is
    `@lru_cache(maxsize=1)`, process-wide by design (mirrors `nptc.db.
    session.get_engine`), so a test-scoped override has to reach in and
    clear it explicitly rather than relying on a fresh import."""
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", postgres_container.get_connection_url())
    get_indexer_engine.cache_clear()
    yield
    get_indexer_engine.cache_clear()


def _insert_property(
    owner_engine: Engine,
    *,
    key: str,
    datatype: str,
    filterable: bool,
    cardinality: str = PropertyCardinality.ZERO_OR_MANY,
    binding_target: str | None = None,
    value_set_uri: str | None = None,
) -> int:
    """Inserts one `property_definition` row directly via `owner_engine`
    (bypassing the registry write path, which doesn't exist yet - #51/#55)
    and returns its `index_seq`."""
    with Session(bind=owner_engine) as session:
        definition = PropertyDefinition(
            key=key,
            label=key.title(),
            datatype=datatype,
            cardinality=cardinality,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            binding_target=binding_target,
            value_set_uri=value_set_uri,
            strength="required" if binding_target is not None else None,
            edition="au" if binding_target is not None else None,
            filterable=filterable,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
        session.add(definition)
        session.commit()
        return definition.index_seq


def _delete_property(owner_engine: Engine, key: str) -> None:
    with owner_engine.connect() as connection:
        connection.execute(text("DELETE FROM property_definition WHERE key = :key"), {"key": key})
        connection.commit()


def _drop_generated_index_if_exists(owner_engine: Engine, name: str) -> None:
    """`DROP INDEX CONCURRENTLY` cannot run inside a transaction block, so
    cleanup needs its own `AUTOCOMMIT`-execution connection - the same
    reason `get_indexer_engine()` itself is built that way."""
    with owner_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"'))


@pytest.fixture
def _admin_property(owner_engine: Engine) -> Iterator[dict[str, object]]:
    """One throwaway `admin`, `filterable`, `string` property, cleaned up
    (row + any index the test built) even if the test raises - issue #190's
    rule that reconciler DDL, being non-transactional, cannot rely on a
    rolled-back fixture the way `db`/`app_db` do."""
    key = "test_reconciler_string_property"
    index_seq = _insert_property(owner_engine, key=key, datatype="string", filterable=True)
    try:
        yield {"key": key, "index_seq": index_seq}
    finally:
        _delete_property(owner_engine, key)
        _drop_generated_index_if_exists(owner_engine, index_name(index_seq, 1))


@pytest.mark.integration
def test_get_indexer_engine_raises_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_INDEXER_DATABASE_URL", raising=False)
    get_indexer_engine.cache_clear()

    with pytest.raises(IndexerNotConfiguredError):
        get_indexer_engine()

    get_indexer_engine.cache_clear()


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_creates_index_without_a_restart(
    owner_engine: Engine, _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    """AC 1: flipping `filterable` (here, simply inserting a filterable
    property - #51/#55's write path doesn't exist yet) and reconciling
    creates the index, using an engine (`get_indexer_engine()`) that was
    already cached before this test's property was inserted - proving
    nothing about the reconciler depends on a fresh process."""
    key = _admin_property["key"]
    index_seq = _admin_property["index_seq"]
    expected_name = index_name(index_seq, 1)  # type: ignore[arg-type]

    report = reconcile_property_indexes()

    assert expected_name in report.created
    with owner_engine.connect() as connection:
        indexdef = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": expected_name},
        ).scalar_one()
        comment = connection.execute(
            text("SELECT obj_description((:name)::regclass, 'pg_class')"),
            {"name": expected_name},
        ).scalar_one()
    assert "USING btree" in indexdef
    assert "value #>> '{}'::text[]" in indexdef
    assert f"WHERE (property_key = '{key}'::text)" in indexdef
    assert comment == key


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_is_idempotent(
    _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    reconcile_property_indexes()

    second = reconcile_property_indexes()

    assert second.created == ()
    assert second.dropped == ()
    assert second.repaired_invalid == ()
    assert second.rebuilt_stale_definition == ()
    assert second.repaired_comment == ()
    assert second.failed == ()
    assert not second.changed


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_drops_index_when_unflagged(
    owner_engine: Engine, _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    """The other half of AC 1/AC 3: un-flagging removes the index rather
    than leaving it orphaned."""
    key = _admin_property["key"]
    index_seq = _admin_property["index_seq"]
    expected_name = index_name(index_seq, 1)  # type: ignore[arg-type]
    reconcile_property_indexes()

    with owner_engine.connect() as connection:
        connection.execute(
            text("UPDATE property_definition SET filterable = false WHERE key = :key"),
            {"key": key},
        )
        connection.commit()

    report = reconcile_property_indexes()

    assert expected_name in report.dropped
    with owner_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :name"), {"name": expected_name}
        ).first()
    assert exists is None


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_rebuilds_an_invalid_index(
    owner_engine: Engine, _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    """A `CREATE INDEX CONCURRENTLY` that failed partway leaves an index
    that exists by name but is never used by the planner
    (`indisvalid = false`) - `pg_index`, not `pg_indexes` (which is blind to
    this), is what lets the reconciler notice and rebuild it."""
    index_seq = _admin_property["index_seq"]
    expected_name = index_name(index_seq, 1)  # type: ignore[arg-type]
    reconcile_property_indexes()
    with owner_engine.connect() as connection:
        connection.execute(
            text("UPDATE pg_index SET indisvalid = false WHERE indexrelid = (:name)::regclass"),
            {"name": expected_name},
        )
        connection.commit()

    report = reconcile_property_indexes()

    assert expected_name in report.repaired_invalid
    with owner_engine.connect() as connection:
        is_valid = connection.execute(
            text("SELECT indisvalid FROM pg_index WHERE indexrelid = (:name)::regclass"),
            {"name": expected_name},
        ).scalar_one()
    assert is_valid is True


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_repairs_a_stale_comment_without_rebuilding(
    owner_engine: Engine, _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    key = _admin_property["key"]
    index_seq = _admin_property["index_seq"]
    expected_name = index_name(index_seq, 1)  # type: ignore[arg-type]
    reconcile_property_indexes()
    with owner_engine.connect() as connection:
        connection.execute(text(f"COMMENT ON INDEX \"{expected_name}\" IS 'wrong'"))
        oid_before = connection.execute(
            text("SELECT (:name)::regclass::oid"), {"name": expected_name}
        ).scalar_one()
        connection.commit()

    report = reconcile_property_indexes()

    assert expected_name in report.repaired_comment
    assert expected_name not in report.repaired_invalid
    with owner_engine.connect() as connection:
        comment = connection.execute(
            text("SELECT obj_description((:name)::regclass, 'pg_class')"),
            {"name": expected_name},
        ).scalar_one()
        oid_after = connection.execute(
            text("SELECT (:name)::regclass::oid"), {"name": expected_name}
        ).scalar_one()
    assert comment == key
    assert oid_after == oid_before  # repaired in place, not rebuilt


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_creates_a_gin_index_for_a_multi_valued_coded_property(
    owner_engine: Engine, _indexer_configured: None
) -> None:
    """AC 3: a multi-valued (`0..*`) coded property gets a GIN index."""
    key = "test_reconciler_code_property"
    index_seq = _insert_property(
        owner_engine,
        key=key,
        datatype="code",
        filterable=True,
        cardinality=PropertyCardinality.ZERO_OR_MANY,
        binding_target=BindingTarget.VALUE_SET,
        value_set_uri="http://example.org/vs",
    )
    expected_name = index_name(index_seq, 1)
    try:
        report = reconcile_property_indexes()

        assert expected_name in report.created
        with owner_engine.connect() as connection:
            indexdef = connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                {"name": expected_name},
            ).scalar_one()
        assert "USING gin" in indexdef
        assert "jsonb_path_ops" in indexdef
    finally:
        _delete_property(owner_engine, key)
        _drop_generated_index_if_exists(owner_engine, expected_name)


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_creating_a_generated_index_without_autocommit_fails_loudly(owner_engine: Engine) -> None:
    """`CREATE INDEX CONCURRENTLY` cannot run inside a transaction block -
    proof that `get_indexer_engine()`'s `isolation_level="AUTOCOMMIT"` is
    load-bearing, not cosmetic, and that a non-autocommit connection fails
    loudly (`25001`) rather than silently downgrading to a
    read-blocking, non-concurrent build."""
    desired = DesiredIndex(
        property_key="does_not_matter",
        index_seq=999999,
        kind=IndexKind.EXPRESSION_BTREE,
        expression=ValueExpression.TEXT_SCALAR,
    )
    with owner_engine.connect() as connection:
        raw = connection.connection.driver_connection
        assert raw is not None
        with pytest.raises(psycopg.errors.ActiveSqlTransaction) as excinfo, raw.cursor() as cursor:
            cursor.execute(create_statement(desired))

    assert excinfo.value.sqlstate == "25001"


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_concurrent_reconciliation_is_skipped_not_raced(
    owner_engine: Engine, _indexer_configured: None
) -> None:
    """Two reconciliations converging on the same desired state is a
    no-op, not a race - the loser reports `skipped_locked=True` rather than
    blocking or erroring."""
    with owner_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": RECONCILE_LOCK_KEY})
        holder.commit()
        try:
            report = reconcile_property_indexes()
            assert report.skipped_locked is True
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RECONCILE_LOCK_KEY})
            holder.commit()


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_rebuilds_after_a_datatype_amendment(
    owner_engine: Engine, _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    """Issue #54 review: `datatype` is an ordinary mutable, audited column,
    not `index_seq` - amending it from `string` to `decimal` keeps the same
    index name but leaves the old `TEXT_SCALAR` expression behind, unusable
    for `DecimalHandler.filter_clause`'s `NUMERIC_SCALAR` predicate, unless
    the reconciler notices the actual index definition (not just its name
    and validity) has gone stale."""
    key = _admin_property["key"]
    index_seq = _admin_property["index_seq"]
    expected_name = index_name(index_seq, 1)  # type: ignore[arg-type]
    reconcile_property_indexes()
    with owner_engine.connect() as connection:
        oid_before = connection.execute(
            text("SELECT (:name)::regclass::oid"), {"name": expected_name}
        ).scalar_one()
        connection.execute(
            text("UPDATE property_definition SET datatype = 'decimal' WHERE key = :key"),
            {"key": key},
        )
        connection.commit()

    report = reconcile_property_indexes()

    assert expected_name in report.rebuilt_stale_definition
    with owner_engine.connect() as connection:
        indexdef = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": expected_name},
        ).scalar_one()
        oid_after = connection.execute(
            text("SELECT (:name)::regclass::oid"), {"name": expected_name}
        ).scalar_one()
    assert "nptc_numeric_or_null" in indexdef
    assert oid_after != oid_before  # actually rebuilt, not merely re-commented


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_reconcile_rebuilds_after_a_key_rename(
    owner_engine: Engine, _indexer_configured: None, _admin_property: dict[str, object]
) -> None:
    """The other mutable column an index name alone cannot notice
    (issue #54 review): a renamed `key` leaves the old key embedded in the
    index's `WHERE property_key = '<old key>'` predicate, so a query for
    the new key would never use it at all."""
    old_key = _admin_property["key"]
    new_key = "test_reconciler_string_property_renamed"
    index_seq = _admin_property["index_seq"]
    expected_name = index_name(index_seq, 1)  # type: ignore[arg-type]
    reconcile_property_indexes()
    try:
        with owner_engine.connect() as connection:
            connection.execute(
                text("UPDATE property_definition SET key = :new_key WHERE key = :old_key"),
                {"new_key": new_key, "old_key": old_key},
            )
            connection.commit()

        report = reconcile_property_indexes()

        assert expected_name in report.rebuilt_stale_definition
        with owner_engine.connect() as connection:
            indexdef = connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                {"name": expected_name},
            ).scalar_one()
            comment = connection.execute(
                text("SELECT obj_description((:name)::regclass, 'pg_class')"),
                {"name": expected_name},
            ).scalar_one()
        assert f"property_key = '{new_key}'::text" in indexdef
        assert comment == new_key
    finally:
        # `_admin_property`'s own cleanup deletes by the *old* key - rename
        # it back so that cleanup still finds and removes the row.
        with owner_engine.connect() as connection:
            connection.execute(
                text("UPDATE property_definition SET key = :old_key WHERE key = :new_key"),
                {"old_key": old_key, "new_key": new_key},
            )
            connection.commit()


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_one_failing_index_does_not_abort_the_rest_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
    owner_engine: Engine,
    _indexer_configured: None,
    _admin_property: dict[str, object],
) -> None:
    """Issue #54 review: a single `CREATE INDEX CONCURRENTLY` failure must
    not skip every other desired/orphaned index for the rest of that run -
    the whole justification for a converge-everything reconciler is
    repairing partial failures, not adding a new one of its own."""
    from nptc.db import property_reconciler

    failing_key = "test_reconciler_second_property_that_fails"
    failing_index_seq = _insert_property(
        owner_engine, key=failing_key, datatype="string", filterable=True
    )
    failing_name = index_name(failing_index_seq, 1)
    real_create_statement = property_reconciler.create_statement

    def _fail_for_the_second_property(desired: DesiredIndex) -> object:
        if desired.property_key == failing_key:
            raise psycopg.errors.QueryCanceled("simulated failure")
        return real_create_statement(desired)

    monkeypatch.setattr(property_reconciler, "create_statement", _fail_for_the_second_property)

    try:
        report = reconcile_property_indexes()

        expected_name = index_name(_admin_property["index_seq"], 1)  # type: ignore[arg-type]
        assert expected_name in report.created  # the other property still converged
        assert report.failed == ((failing_name, "QueryCanceled"),)
        with owner_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname = :name"), {"name": failing_name}
            ).first()
        assert exists is None  # the failed index was never left half-built
    finally:
        _delete_property(owner_engine, failing_key)
        _drop_generated_index_if_exists(owner_engine, failing_name)
