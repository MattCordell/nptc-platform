"""Automatic index generation for filterable properties (FR-13).

ADR-0012's "FR-13 index strategy" fixes the design. There are three index
shapes: a `jsonb_path_ops` GIN for object-valued `code` properties, an
expression btree on `(value #>> '{}')` for `string`/`url`, and one on
`nptc_numeric_or_null(value #>> '{}')` for `decimal`/`positiveInt` (cast-safe,
ADR-0027). Names follow `ix_propval_p{index_seq}_{slot}` and never embed the
property `key`, which goes in `COMMENT ON INDEX` instead.

**A desired-state reconciler, not an event handler** (ADR-0012). Diffing every
`property_definition` row against actual `pg_index` state needs no write-path
event, makes "un-flagging removes the index" free, and is the only shape that
notices a `CREATE INDEX CONCURRENTLY` that failed partway and left an
`indisvalid = false` index. `nptc.db.property_reconciler` does the diffing and
runs the DDL; this module owns the pure pieces: naming, desired-state
computation and statement construction.

**This is the one module NFR-22's guard (`test_sql_parameterisation.py`, rule
5) permits to compose DDL via `psycopg.sql`.** Every `.format()` receiver is a
`sql.SQL(<string literal>)` called directly, and every argument is an inline
`sql.Identifier` or `sql.Literal` call, never a variable. Any other way of
composing a statement fails CI. The reconciler executes the `Composed`
statements directly, never stringified.

Lives under `nptc.db`, not `nptc.registry`, because it imports
`PropertyDefinition` and `nptc.db.property_specs`, which ADR-0013 SS2's leaf
rule keeps out of `registry/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from psycopg import sql

from nptc.db.property_specs import spec_for
from nptc.registry.handlers import (
    INDEX_KIND_BY_EXPRESSION,
    IndexKind,
    UnknownDatatypeError,
    ValueExpression,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.schema import SchemaItem

    from nptc.db.models.property_definition import PropertyDefinition
    from nptc.registry.handlers import DatatypeRegistry

__all__ = [
    "GENERATED_INDEX_NAME_RE",
    "DesiredIndex",
    "UnknownDatatypeProperty",
    "comment_statement",
    "create_statement",
    "desired_indexes",
    "drop_statement",
    "include_object",
    "index_name",
    "matches_indexdef",
]

#: `ix_propval_p{index_seq}_{slot}` (ADR-0012). At most 33 bytes by
#: construction (a 12-byte prefix, up to 19 digits, a separator and one digit),
#: under both the ADR's 39-byte target and Postgres's 63-byte limit. It finds
#: the actual generated indexes, and `include_object` uses it to exclude them
#: from Alembic autogenerate.
GENERATED_INDEX_NAME_RE: Final = re.compile(r"^ix_propval_p\d+_[12]$")

#: The one index generated per filterable property.
_SLOT_PRIMARY: Final = 1

#: Reserved by ADR-0012 for a composite `(property_key, <expr>)` btree fallback.
#: `test_db_property_index_plan.py`'s negative control found the
#: literal-rendered partial index usable, so no slot-2 index is generated. This
#: constant only spells out the reservation (and the `[12]` above).
_SLOT_COMPOSITE_FALLBACK: Final = 2


def index_name(index_seq: int, slot: int) -> str:
    return f"ix_propval_p{index_seq}_{slot}"


@dataclass(frozen=True, slots=True)
class DesiredIndex:
    """One row of the reconciler's desired state: one filterable property's
    slot-1 index. `nptc.db.property_reconciler` diffs a list of these
    against actual `pg_index` state."""

    property_key: str
    index_seq: int
    kind: IndexKind
    expression: ValueExpression

    @property
    def name(self) -> str:
        return index_name(self.index_seq, _SLOT_PRIMARY)


@dataclass(frozen=True, slots=True)
class UnknownDatatypeProperty:
    """One filterable `property_definition` row whose `datatype` has no handler
    in the running build.

    Reachable: `datatype` is plain, mutable `TEXT` (FR-77's extension point), so
    an app rollback, a seed applied ahead of the code or a raw-SQL amendment can
    leave one. `desired_indexes` reports these separately, because skipping them
    would look like "un-flagged" to the orphan-drop sweep and destroy a working
    index (ADR-0012)."""

    property_key: str
    index_seq: int

    @property
    def name(self) -> str:
        return index_name(self.index_seq, _SLOT_PRIMARY)


def desired_indexes(
    definitions: Sequence[PropertyDefinition], registry: DatatypeRegistry
) -> tuple[list[DesiredIndex], list[UnknownDatatypeProperty]]:
    """The desired index set, derived from each row's own handler, paired with
    the rows this build cannot ask a handler about.

    A `filterable=False` property, or a handler whose `index_shape()` returns
    `None`, contributes to neither list. That is what makes "un-flagging
    removes the index" true with no special case in the reconciler. A
    `filterable=True` property with an unregistered datatype goes in the second
    list (see `UnknownDatatypeProperty`); a `filterable=False` one is skipped."""
    desired: list[DesiredIndex] = []
    unknown: list[UnknownDatatypeProperty] = []
    for definition in definitions:
        try:
            handler = registry.get(definition.datatype)
        except UnknownDatatypeError:
            if definition.filterable:
                unknown.append(
                    UnknownDatatypeProperty(
                        property_key=definition.key, index_seq=definition.index_seq
                    )
                )
            continue
        shape = handler.index_shape(spec_for(definition))
        if shape is None:
            continue
        desired.append(
            DesiredIndex(
                property_key=definition.key,
                index_seq=definition.index_seq,
                kind=INDEX_KIND_BY_EXPRESSION[shape.expression],
                expression=shape.expression,
            )
        )
    return desired, unknown


def create_statement(desired: DesiredIndex) -> sql.Composed:
    """The `CREATE INDEX CONCURRENTLY` for one desired index, as a
    `psycopg.sql.Composed`, never a string.

    `property_key` is rendered as a literal, not a bind parameter, so the
    partial-index predicate can match; `test_db_property_index_plan.py` checks
    the plan.

    The JSONB empty-path literal is spelled `'{{}}'` because `sql.SQL.format`
    uses `str.format` placeholders, so a literal brace must be doubled.

    The table is schema-qualified (`public.property_value`). The reconciler
    connects as `NPTC_INDEXER_DATABASE_URL`'s role, whose `search_path` need not
    put `public` first. An unqualified name could succeed against a same-named
    relation in another schema, which the actual-state query (fixed to
    `nspname = 'public'`) never sees, so every later run would fail with
    "already exists". `CREATE INDEX` cannot qualify the index name, so the table
    is qualified; `drop_statement` and `comment_statement` qualify the index
    name, which those statements accept.
    """
    if desired.expression is ValueExpression.RAW_JSONB:
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON public.property_value USING gin "
            "(value jsonb_path_ops) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    if desired.expression is ValueExpression.TEXT_SCALAR:
        # `text_pattern_ops`, not the default opclass: it is the one opclass
        # that serves `EQUALS`/`IN` and a `PREFIX` (`LIKE 'foo%'`) filter from
        # one index under a non-`C` collation. A second index just for `PREFIX`
        # would double write amplification on `property_value`.
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON public.property_value "
            "((value #>> '{{}}') text_pattern_ops) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    if desired.expression is ValueExpression.NUMERIC_SCALAR:
        # `public.` on the function too, for the `search_path` reason above.
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON public.property_value "
            "(public.nptc_numeric_or_null(value #>> '{{}}')) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    raise AssertionError(f"unhandled ValueExpression: {desired.expression!r}")  # pragma: no cover


def drop_statement(name: str) -> sql.Composed:
    """`IF EXISTS`, because the drop-orphaned step may race a concurrent
    reconciliation that already dropped the index. Schema-qualified for the
    `search_path` reason in `create_statement`; `DROP INDEX` accepts a
    qualified name."""
    return sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {name}").format(
        name=sql.Identifier("public", name)
    )


def comment_statement(name: str, property_key: str) -> sql.Composed:
    """Carries the property key that an index's own name never does (ADR-0012),
    so an operator reading `pg_indexes` can trace name to key.
    Schema-qualified as `drop_statement` is."""
    return sql.SQL("COMMENT ON INDEX {name} IS {key}").format(
        name=sql.Identifier("public", name), key=sql.Literal(property_key)
    )


def _expression_marker(expression: ValueExpression) -> str:
    """A substring of `pg_get_indexdef()`'s output that appears for this
    expression and no other. Postgres reformats the input syntax (extra
    parentheses, a `::text[]` cast on the `#>>` argument), so each marker was
    checked against real output on `postgres:18.6`.

    It must be exclusive, not merely present: `value #>> '{}'::text[]` also
    appears in the `NUMERIC_SCALAR` rendering, which would hide a `decimal` to
    `string` amendment. Anchoring `TEXT_SCALAR` on `text_pattern_ops` fixes
    that, and also marks an index built with the old default opclass as stale
    so it is rebuilt (ADR-0012)."""
    if expression is ValueExpression.RAW_JSONB:
        return "jsonb_path_ops"
    if expression is ValueExpression.TEXT_SCALAR:
        return "text_pattern_ops"
    if expression is ValueExpression.NUMERIC_SCALAR:
        return "nptc_numeric_or_null("
    raise AssertionError(f"unhandled ValueExpression: {expression!r}")  # pragma: no cover


def matches_indexdef(desired: DesiredIndex, indexdef: str) -> bool:
    """True when `indexdef` (from `pg_get_indexdef`) already reflects
    `desired`'s expression and property key.

    False after a `datatype` amendment, because `datatype` is mutable while the
    `index_seq` in the index name is not, so the name alone cannot notice. The
    key comparison also catches a raw-SQL rename, as defence in depth (`key` is
    otherwise immutable, FR-12). `False` tells the reconciler to drop and
    rebuild, not merely re-comment.

    The key is compared by f-string interpolation into a substring check
    because `property_key`'s CHECK (`^[a-z][a-z0-9_]{0,62}$`) rules out a quote
    character."""
    return (
        _expression_marker(desired.expression) in indexdef
        and f"property_key = '{desired.property_key}'::text" in indexdef
    )


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """False for a generated index (ADR-0012): reconciler-managed runtime state,
    not schema history, so Alembic autogenerate must not propose dropping one.
    Wired into every `context.configure(...)` in `backend/migrations/env.py` and
    into `test_db_migrations.py`, whose `compare_metadata` runs against the
    shared database that the reconciler's own tests also write to."""
    return not (type_ == "index" and name is not None and GENERATED_INDEX_NAME_RE.match(name))
