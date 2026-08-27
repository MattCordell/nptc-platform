"""Naming and desired-state tests for issue #54 (FR-13) - no container, no
DDL. See `test_db_property_index_plan.py` for the `EXPLAIN` proof and
`test_db_numeric_or_null_function.py` for the cast-safe numeric expression.
"""

from __future__ import annotations

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


def test_drop_statement_is_concurrent_and_conditional() -> None:
    rendered = drop_statement("ix_propval_p7_1").as_string(None)

    assert rendered == 'DROP INDEX CONCURRENTLY IF EXISTS "ix_propval_p7_1"'


def test_comment_statement_carries_the_property_key() -> None:
    rendered = comment_statement("ix_propval_p7_1", "discipline").as_string(None)

    assert rendered == "COMMENT ON INDEX \"ix_propval_p7_1\" IS 'discipline'"


def test_include_object_excludes_a_generated_index() -> None:
    assert include_object(object(), "ix_propval_p7_1", "index", False, None) is False


def test_include_object_includes_a_hand_written_index() -> None:
    assert include_object(object(), "ix_property_value_property_key", "index", False, None) is True


def test_include_object_includes_non_index_objects_regardless_of_name() -> None:
    assert include_object(object(), "ix_propval_p7_1", "table", False, None) is True


def test_include_object_includes_an_index_with_no_name() -> None:
    assert include_object(object(), None, "index", False, None) is True
