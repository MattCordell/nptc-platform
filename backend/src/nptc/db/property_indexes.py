"""Automatic index generation for filterable properties (issue #54, FR-13).

ADR-0012's "FR-13 index strategy" section fixes almost everything here: the
three index shapes (a `jsonb_path_ops` GIN for object-valued `code`
properties, an expression btree on `(value #>> '{}')` for `string`/`url`,
and one on `nptc_numeric_or_null(value #>> '{}')` - see
`nptc.db.functions` and ADR-0027 for why the numeric expression must be
cast-safe - for `decimal`/`positiveInt`), and the
`ix_propval_p{index_seq}_{slot}` naming scheme that never embeds the
property `key` in an identifier (the key goes in `COMMENT ON INDEX`
instead - see `comment_statement` below).

**A desired-state reconciler, not an event handler** (issue #54's own
amendment to ADR-0012's open executor-topology question). There is no
`property_definition` write path today - only `nptc.db.bootstrap`, called
only from tests - so nothing exists yet to "react" to a flag flip. Reading
every `property_definition` row and diffing the result against actual
`pg_index` state instead needs no write-path event, gets "un-flagging
removes the index" for free, and - the more important reason - is the only
shape that notices a `CREATE INDEX CONCURRENTLY` that failed partway,
leaving an index that exists by name but is `indisvalid = false` and is
never used by the planner. `nptc.db.property_reconciler` is the module that
does that diffing and executes the DDL; this module owns only the pure,
container-free pieces: naming, desired-state computation, and statement
construction.

**This is the one module NFR-22's guard (`test_sql_parameterisation.py`,
rule 5) permits to compose DDL via `psycopg.sql`.** Every `.format()` call
below has a `sql.SQL(<string literal>)` receiver, called directly, and
every argument is itself a `sql.Identifier`/`sql.Literal` call - the exact
shape the guard checks for, so composing a statement any other way (an
f-string, a stored template later filled in from a variable) fails CI
rather than merely review. `nptc.db.property_reconciler` executes these
`Composed` statements directly against a raw psycopg connection - never
stringified, which would be exactly the intermediate-string hazard NFR-22
exists to close.

Lives under `nptc.db`, not `nptc.registry`: it imports
`nptc.db.models.property_definition.PropertyDefinition` and
`nptc.db.property_specs`, both of which ADR-0013 SS2's leaf rule keeps out
of `registry/` (see `nptc.db.bootstrap`'s own docstring for the same
argument).
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
    "comment_statement",
    "create_statement",
    "desired_indexes",
    "drop_statement",
    "include_object",
    "index_name",
]

#: `ix_propval_p{index_seq}_{slot}` (ADR-0012). The fixed 12-byte prefix
#: (`ix_propval_p`) plus `index_seq`'s worst case of 19 digits (a signed
#: 64-bit BIGINT's maximum magnitude) plus a 1-byte separator plus a
#: single-digit `slot` is at most 33 bytes - provably under both the ADR's
#: 39-byte target and Postgres's 63-byte identifier limit by construction,
#: not by a length check. Matched against `pg_index`/`pg_class` to find
#: actual generated indexes, and by `include_object` below to exclude them
#: from Alembic autogenerate.
GENERATED_INDEX_NAME_RE: Final = re.compile(r"^ix_propval_p\d+_[12]$")

#: The one index #54 generates per filterable property.
_SLOT_PRIMARY: Final = 1

#: Reserved by ADR-0012 for a composite `(property_key, <expr>)` btree
#: fallback, in case the partial index proved unusable under a generic plan
#: (`property_key = $1` rather than a literal). `test_db_property_index_
#: plan.py`'s negative control found the literal-rendered partial index
#: usable, so #54 does not generate a slot-2 index - this constant exists
#: only so the reservation (and the `[12]` in the regex above) is spelled
#: out, not so anything constructs one today.
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


def desired_indexes(
    definitions: Sequence[PropertyDefinition], registry: DatatypeRegistry
) -> list[DesiredIndex]:
    """The full desired index set, derived from every `property_definition`
    row's own handler - never a second, independent list of "which
    properties are filterable". A `filterable=False` property (e.g. the
    seeded `usage_guidance`) or a handler that returns `None` from
    `index_shape()` (indexing meaningless for that datatype) contributes
    nothing here, which is what makes "un-flagging removes the index" true
    without any special-case code in the reconciler itself."""
    desired: list[DesiredIndex] = []
    for definition in definitions:
        handler = registry.get(definition.datatype)
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
    return desired


def create_statement(desired: DesiredIndex) -> sql.Composed:
    """The `CREATE INDEX CONCURRENTLY` for one desired index, as a
    `psycopg.sql.Composed` - never a string. `property_key` is rendered as
    a literal, not a bind parameter: proving that this is what makes the
    partial index usable under a generic plan (ADR-0012's other open claim)
    is `test_db_property_index_plan.py`'s job, not this function's.

    The JSONB empty-path literal must be spelled `'{{}}'` here -
    `sql.SQL.format` uses `str.format` placeholder syntax, so a literal
    brace has to be doubled to survive it.

    **Every `.format()` argument below is an inline `sql.Identifier`/
    `sql.Literal` call, never a variable referencing one** - NFR-22's guard
    (`test_sql_parameterisation.py` rule 5) requires exactly this shape in
    this module, the same "no indirection through a Name" posture rule 1
    already takes at a `text()`/`.execute()` call site.
    """
    if desired.expression is ValueExpression.RAW_JSONB:
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON property_value USING gin "
            "(value jsonb_path_ops) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    if desired.expression is ValueExpression.TEXT_SCALAR:
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON property_value "
            "((value #>> '{{}}')) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    if desired.expression is ValueExpression.NUMERIC_SCALAR:
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON property_value "
            "(nptc_numeric_or_null(value #>> '{{}}')) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    raise AssertionError(f"unhandled ValueExpression: {desired.expression!r}")  # pragma: no cover


def drop_statement(name: str) -> sql.Composed:
    """`IF EXISTS`: the reconciler's own drop-orphaned step may race a
    concurrent reconciliation that already dropped the same index (see the
    advisory lock in `property_reconciler`, which makes that vanishingly
    rare but not impossible to reason about as a no-op)."""
    return sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {name}").format(name=sql.Identifier(name))


def comment_statement(name: str, property_key: str) -> sql.Composed:
    """Carries the property key an index's own name never does (ADR-0012),
    resolving data-model.md's name-to-key traceability caveat for an
    operator staring at `pg_indexes`."""
    return sql.SQL("COMMENT ON INDEX {name} IS {key}").format(
        name=sql.Identifier(name), key=sql.Literal(property_key)
    )


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """False for a generated index (ADR-0012): these are reconciler-managed
    runtime state, not schema history, so Alembic autogenerate must never
    propose dropping one just because it isn't in `Base.metadata`. Wired
    into every `context.configure(...)` call in `backend/migrations/env.py`,
    and into `test_db_migrations.py`'s own `MigrationContext.configure` -
    that test runs `compare_metadata` against the shared session-scoped
    database the reconciler's own integration tests also write to."""
    return not (type_ == "index" and name is not None and GENERATED_INDEX_NAME_RE.match(name))
