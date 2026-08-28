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


@dataclass(frozen=True, slots=True)
class UnknownDatatypeProperty:
    """One filterable `property_definition` row whose `datatype` has no
    handler registered in the *running* build (issue #54 review, fourth
    pass) - reachable, not theoretical: `datatype` is plain `TEXT` with no
    `CHECK`/`ENUM` (FR-77's own extension point - "admitting a new datatype
    never touches this table") and is mutable, so an app rollback across a
    datatype addition, a seed applied ahead of the code, or a raw-SQL
    amendment can all leave a row like this on record. `desired_indexes`
    reports these separately rather than silently skipping past them: doing
    that would make the row indistinguishable from "un-flagged" to the
    orphan-drop sweep, destroying a working index under `CONCURRENTLY`
    (the expensive direction to rebuild) merely because a handler is
    temporarily unavailable."""

    property_key: str
    index_seq: int

    @property
    def name(self) -> str:
        return index_name(self.index_seq, _SLOT_PRIMARY)


def desired_indexes(
    definitions: Sequence[PropertyDefinition], registry: DatatypeRegistry
) -> tuple[list[DesiredIndex], list[UnknownDatatypeProperty]]:
    """The full desired index set, derived from every `property_definition`
    row's own handler - never a second, independent list of "which
    properties are filterable" - paired with the rows this build cannot
    even ask a handler about at all.

    A `filterable=False` property (e.g. the seeded `usage_guidance`) or a
    handler that returns `None` from `index_shape()` (indexing meaningless
    for that datatype) contributes nothing to either list, which is what
    makes "un-flagging removes the index" true without any special-case
    code in the reconciler itself.

    A `filterable=True` property whose `datatype` has no registered handler
    (`registry.get` raises `UnknownDatatypeError`) goes into the second list
    instead of being silently dropped from the first - see
    `UnknownDatatypeProperty`'s own docstring for why the reconciler must
    protect, not orphan-sweep, whatever index that property already has. A
    `filterable=False` property with an unknown datatype needs no index
    either way and is skipped entirely, same as a known one."""
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
    `psycopg.sql.Composed` - never a string. `property_key` is rendered as
    a literal, not a bind parameter: proving that this is what makes the
    partial index usable under a generic plan (ADR-0012's other open claim)
    is `test_db_property_index_plan.py`'s job, not this function's.

    The JSONB empty-path literal must be spelled `'{{}}'` here -
    `sql.SQL.format` uses `str.format` placeholder syntax, so a literal
    brace has to be doubled to survive it.

    The table reference is schema-qualified (`public.property_value`), not
    bare `property_value` - the reconciler's DDL connection uses
    `NPTC_INDEXER_DATABASE_URL`'s own role, whose `search_path` need not put
    `public` first (issue #54 review): an unqualified reference would let
    `CREATE INDEX` silently succeed against a same-named relation in a
    different schema, which the reconciler's actual-state query (fixed to
    `nspname = 'public'`) would never see, and every subsequent run would
    then retry the same `CREATE INDEX` and fail with "already exists". A bare
    `CREATE INDEX name ON ...` cannot itself be schema-qualified - Postgres
    always creates an index in its table's own schema - so qualifying the
    table is what closes this, not qualifying the index name (`drop_statement`/
    `comment_statement` below qualify the *index* name instead, since `DROP
    INDEX`/`COMMENT ON INDEX` do accept a schema-qualified object name).

    **Every `.format()` argument below is an inline `sql.Identifier`/
    `sql.Literal` call, never a variable referencing one** - NFR-22's guard
    (`test_sql_parameterisation.py` rule 5) requires exactly this shape in
    this module, the same "no indirection through a Name" posture rule 1
    already takes at a `text()`/`.execute()` call site.
    """
    if desired.expression is ValueExpression.RAW_JSONB:
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON public.property_value USING gin "
            "(value jsonb_path_ops) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    if desired.expression is ValueExpression.TEXT_SCALAR:
        # `text_pattern_ops`, not the default opclass: verified directly
        # against `postgres:18.6` that this is the one opclass that serves
        # `EQUALS`/`IN` *and* a `PREFIX` (`LIKE 'foo%'`) filter from the same
        # index under a non-`C` collation - the default opclass cannot serve
        # `LIKE` at all outside `C`/`POSIX`, and a second index just for
        # `PREFIX` would double write amplification on `property_value` for
        # no reason (issue #54 review).
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON public.property_value "
            "((value #>> '{{}}') text_pattern_ops) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    if desired.expression is ValueExpression.NUMERIC_SCALAR:
        # `public.nptc_numeric_or_null`, not the bare function name - the
        # same `search_path` exposure the table reference above closes
        # (issue #54 review): an unqualified function call resolves through
        # the indexer role's own `search_path` at `CREATE INDEX` time, not
        # necessarily `public` first.
        return sql.SQL(
            "CREATE INDEX CONCURRENTLY {name} ON public.property_value "
            "(public.nptc_numeric_or_null(value #>> '{{}}')) WHERE property_key = {key}"
        ).format(name=sql.Identifier(desired.name), key=sql.Literal(desired.property_key))
    raise AssertionError(f"unhandled ValueExpression: {desired.expression!r}")  # pragma: no cover


def drop_statement(name: str) -> sql.Composed:
    """`IF EXISTS`: the reconciler's own drop-orphaned step may race a
    concurrent reconciliation that already dropped the same index (see the
    advisory lock in `property_reconciler`, which makes that vanishingly
    rare but not impossible to reason about as a no-op). Schema-qualified
    (`public.<name>`) for the same `search_path` reason `create_statement`
    qualifies its table reference - `DROP INDEX` accepts a schema-qualified
    object name, unlike `CREATE INDEX`."""
    return sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {name}").format(
        name=sql.Identifier("public", name)
    )


def comment_statement(name: str, property_key: str) -> sql.Composed:
    """Carries the property key an index's own name never does (ADR-0012),
    resolving data-model.md's name-to-key traceability caveat for an
    operator staring at `pg_indexes`. Schema-qualified for the same reason
    `drop_statement` is."""
    return sql.SQL("COMMENT ON INDEX {name} IS {key}").format(
        name=sql.Identifier("public", name), key=sql.Literal(property_key)
    )


def _expression_marker(expression: ValueExpression) -> str:
    """A substring of `pg_get_indexdef()`'s own rendering that appears only
    for this expression and no other - verified directly against
    `postgres:18.6`'s actual `pg_get_indexdef` output for each of the three
    shapes, not assumed from `create_statement`'s input syntax (Postgres
    reformats it: extra parentheses, an added `::text[]` cast on the `#>>`
    argument).

    **Must be exclusive, not merely present** (issue #54 review, second
    pass): the bare fragment `value #>> '{}'::text[]` is a substring of the
    `NUMERIC_SCALAR` rendering too (`nptc_numeric_or_null` wraps the same
    `#>>` expression), so using it for `TEXT_SCALAR` let a `decimal`/
    `positiveInt` -> `string`/`url` amendment go undetected - the untested
    direction of the two, since the reverse (`string` -> `decimal`) already
    failed the *absence* of `nptc_numeric_or_null` and was caught. Anchoring
    on `text_pattern_ops` instead - the opclass only `TEXT_SCALAR`'s
    `create_statement` ever specifies - fixes both directions in one move,
    and as a side effect also catches an index built before the
    `text_pattern_ops` switch (default btree opclass, issue #54 review's own
    finding 3): its `pg_get_indexdef` output has no `text_pattern_ops` at
    all, so it now correctly reads as stale and gets rebuilt."""
    if expression is ValueExpression.RAW_JSONB:
        return "jsonb_path_ops"
    if expression is ValueExpression.TEXT_SCALAR:
        return "text_pattern_ops"
    if expression is ValueExpression.NUMERIC_SCALAR:
        return "nptc_numeric_or_null("
    raise AssertionError(f"unhandled ValueExpression: {expression!r}")  # pragma: no cover


def matches_indexdef(desired: DesiredIndex, indexdef: str) -> bool:
    """True when `indexdef` (from `pg_get_indexdef`, the reconciler's own
    actual-state query) already reflects `desired`'s expression and property
    key - false when a `property_definition` row's `datatype` was amended
    *after* its index was built (issue #54 review): `datatype` is an
    ordinary mutable, audited column, unlike the immutable `index_seq` the
    index name is derived from, so the name alone cannot notice the change.
    (`key` is *not* mutable in practice - `PropertyDefinition.key` is
    `@validates`-guarded and CHECK-constrained to never change once set,
    FR-12 - but the same comparison catches a raw-SQL rename too, as
    defence in depth, and correctly finds nothing to object to in the
    ordinary case where `key` never moves.) A `False` result tells the
    reconciler the existing index's predicate/expression can never serve
    the property as it is configured today, and it must be dropped and
    rebuilt - not merely re-commented.

    The property key literal is compared via plain f-string interpolation
    into a substring check, not a parameterised query - safe here because
    `property_key`'s own CHECK constraint (`^[a-z][a-z0-9_]{0,62}$`) already
    rules out a quote character, unlike the general case NFR-22 guards
    against."""
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
    """False for a generated index (ADR-0012): these are reconciler-managed
    runtime state, not schema history, so Alembic autogenerate must never
    propose dropping one just because it isn't in `Base.metadata`. Wired
    into every `context.configure(...)` call in `backend/migrations/env.py`,
    and into `test_db_migrations.py`'s own `MigrationContext.configure` -
    that test runs `compare_metadata` against the shared session-scoped
    database the reconciler's own integration tests also write to."""
    return not (type_ == "index" and name is not None and GENERATED_INDEX_NAME_RE.match(name))
