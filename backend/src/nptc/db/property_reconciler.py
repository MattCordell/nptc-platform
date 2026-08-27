"""The FR-13 DDL executor: reads `property_definition`, computes the
desired index set (`nptc.db.property_indexes.desired_indexes`), diffs it
against actual `pg_index` state, and creates/drops/repairs to converge
(issue #54).

**Topology: an in-process reconciler, callable as a library, plus a CLI
(`scripts/reconcile_property_indexes.py`) - not a separate deployable.**
ADR-0012 posed the executor's topology as its own open question with two
surviving candidates (a separate indexer process, or a background task in
the API process on its own autocommit connection); this module amends that
ADR to choose the second. A separate process would have to learn that
`filterable` changed by polling `property_definition` - and polling a table
is the P3 `nptc.jobs` SKIP LOCKED queue in a different hat, which ADR-0012's
own rejected-alternatives table already forbids pulling forward for this
issue. `reconcile_property_indexes()` is deliberately argument-free and
public so a future write path (#55/#138, the first issue at which one
exists at all) can dispatch it as a `starlette.background` task; this
module does not wire it into `create_app()` itself - that would make the
API require a DDL credential at boot and run DDL on every `create_app()` in
the test suite.

**A desired-state reconciler, not an event handler.** There is no
`property_definition` write path today (only `nptc.db.bootstrap`, called
only from tests), so nothing exists yet to "react" to a flag flip anyway -
but even once one does, an event handler would only ever create an index
once and never notice a `CREATE INDEX CONCURRENTLY` that failed partway,
leaving an index that exists by name but is `indisvalid = false` and is
never used by the planner (a documented CIC failure mode). Diffing against
actual state on every run repairs that for free, and gets "un-flagging
removes the index" (this issue's own acceptance criterion) for free too.

**Its own `NPTC_INDEXER_DATABASE_URL` credential, on an AUTOCOMMIT
connection.** `CREATE INDEX CONCURRENTLY` raises `25001` inside a
transaction block, and `nptc_app` (the API's own runtime role) provably
cannot do DDL at all (ADR-0012's rejected-alternatives table: granting it
ownership would also confer `DROP`/`ALTER`/`TRUNCATE`). See
`nptc.settings.IndexerSettings` for why this is a separate, optional
variable rather than a fallback to `NPTC_MIGRATION_DATABASE_URL`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.property_indexes import (
    DesiredIndex,
    comment_statement,
    create_statement,
    desired_indexes,
    drop_statement,
)
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc.settings import IndexerSettings

__all__ = [
    "RECONCILE_LOCK_KEY",
    "IndexerNotConfiguredError",
    "ReconciliationReport",
    "get_indexer_engine",
    "reconcile_property_indexes",
]

#: Fixed, arbitrary key for `pg_try_advisory_lock` - any fixed integer
#: works (advisory locks are an application-chosen namespace, unrelated to
#: any table or row), distinct from `nptc.audit.writer.
#: AUDIT_APPEND_LOCK_KEY` so the two locks can never collide. Session-scoped
#: (`pg_try_advisory_lock`/`pg_advisory_unlock`), not `_xact_lock` like the
#: audit writer's - there is no enclosing transaction here to tie the lock
#: to, since `CREATE INDEX CONCURRENTLY` requires autocommit.
RECONCILE_LOCK_KEY: Final[int] = 74619328

_TRY_LOCK_SQL = text("SELECT pg_try_advisory_lock(:key)")
_UNLOCK_SQL = text("SELECT pg_advisory_unlock(:key)")

#: `pg_index`/`pg_class`/`pg_namespace`, not `pg_indexes` (issue #54): the
#: view is blind to `indisvalid`, so a failed `CREATE INDEX CONCURRENTLY`
#: (an index that exists by name but was never usably built) would read as
#: "already present" and never be repaired. `obj_description` recovers the
#: `COMMENT ON INDEX` carrying the property key (`nptc.db.property_indexes.
#: comment_statement`) so a missing/stale comment can be repaired too. A
#: fixed literal query, no runtime data - the `~` pattern is a compile-time
#: constant mirroring `GENERATED_INDEX_NAME_RE`, never interpolated.
_ACTUAL_STATE_SQL = text(
    "SELECT c.relname AS name, i.indisvalid AS is_valid, "
    "obj_description(c.oid, 'pg_class') AS comment "
    "FROM pg_index i "
    "JOIN pg_class c ON c.oid = i.indexrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname ~ '^ix_propval_p[0-9]+_[12]$'"
)


class IndexerNotConfiguredError(RuntimeError):
    """Raised by `get_indexer_engine()` when `NPTC_INDEXER_DATABASE_URL` is
    unset - fail-closed, per `IndexerSettings`'s own empty-default posture:
    "reconciliation is not configured" is a valid, safe deployment state,
    but attempting to reconcile anyway must fail loudly rather than
    silently falling back to a credential that cannot do DDL."""


class _UnreachableTerminologyClient:
    """Satisfies `HandlerDeps.terminology_client`'s `TerminologyClient`
    Protocol shape without ever being called. `index_shape()` - the only
    handler method this module invokes - depends on nothing but a
    property's own `datatype`/`filterable`; none of FR-53's four
    terminology operations are reachable from there. Every method here
    raises rather than returning a plausible-looking fake result, so a
    future handler change that makes some `index_shape()` reach the
    terminology client fails loudly in CI, not silently in production.
    Deliberately not `nptc_shared.terminology.stub.StubTerminologyClient`:
    that type exists for tests (NFR-37), and reusing it here would make a
    production code path depend on a test-only module."""

    def expand(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("index_shape() must never call TerminologyClient.expand()")

    def lookup(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("index_shape() must never call TerminologyClient.lookup()")

    def subsumes(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("index_shape() must never call TerminologyClient.subsumes()")

    def validate_code(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("index_shape() must never call TerminologyClient.validate_code()")


def _registry() -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(HandlerDeps(terminology_client=_UnreachableTerminologyClient()))
    )


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """What one `reconcile_property_indexes()` call did - returned, not
    just logged, so a caller (the CLI, a future background-task dispatch,
    or a test) can act on or assert against the outcome without re-querying
    `pg_index` itself."""

    created: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    repaired_invalid: tuple[str, ...] = ()
    repaired_comment: tuple[str, ...] = ()
    skipped_locked: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.created or self.dropped or self.repaired_invalid)


@lru_cache(maxsize=1)
def get_indexer_engine() -> Engine:
    """The process-wide indexer engine, built from
    `NPTC_INDEXER_DATABASE_URL`.

    `poolclass=NullPool`: no idle owner-scoped connection sits in a pool
    between reconciliations - this credential can do DDL, so the fewer open
    connections carrying it, the better. `isolation_level="AUTOCOMMIT"`:
    `CREATE INDEX CONCURRENTLY` raises `25001` ("CREATE INDEX CONCURRENTLY
    cannot run inside a transaction block") otherwise - verified directly
    against `postgres:18.6`, and asserted by
    `test_db_property_indexes.py::test_create_statement_fails_loudly_
    without_autocommit`.
    """
    settings = IndexerSettings()
    if not settings.indexer_database_url:
        raise IndexerNotConfiguredError(
            "NPTC_INDEXER_DATABASE_URL is not set - index reconciliation is disabled"
        )
    return create_engine(
        settings.indexer_database_url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )


def _desired_by_name(desired: list[DesiredIndex]) -> dict[str, DesiredIndex]:
    return {index.name: index for index in desired}


def reconcile_property_indexes(*, dry_run: bool = False) -> ReconciliationReport:
    """Converges actual `pg_index` state on every filterable property's
    desired index, in one call: builds missing indexes, drops orphaned
    ones (a property that was un-flagged, or whose `index_shape()` now
    returns `None`), rebuilds any that failed into `indisvalid = false`,
    and repairs a missing or stale `COMMENT ON INDEX`.

    Argument-free by default and idempotent - safe to call from the CLI, a
    scheduled check, or (once a write path exists) a background task
    dispatched right after a `property_definition` write commits. A
    `pg_try_advisory_lock` guards the whole run: two concurrent
    reconciliations converging on the same desired state is a no-op, not a
    race to be resolved, so the loser returns `skipped_locked=True` rather
    than blocking or erroring.

    `dry_run=True` (the CLI's `--dry-run`) computes and returns the same
    report without executing any DDL - the diff against actual state is
    identical either way; only whether `create_statement`/`drop_statement`/
    `comment_statement` are actually run differs.
    """
    engine = get_indexer_engine()
    with engine.connect() as connection:
        locked = connection.execute(_TRY_LOCK_SQL, {"key": RECONCILE_LOCK_KEY}).scalar_one()
        if not locked:
            return ReconciliationReport(skipped_locked=True)
        try:
            return _reconcile_locked(connection, dry_run=dry_run)
        finally:
            connection.execute(_UNLOCK_SQL, {"key": RECONCILE_LOCK_KEY})


def _reconcile_locked(connection: Connection, *, dry_run: bool) -> ReconciliationReport:
    # A `Session`, not a bare `Connection.execute(select(PropertyDefinition))`
    # - the latter returns Core `Row`s of column values, not hydrated ORM
    # instances, and `desired_indexes()` needs real attribute access
    # (`.datatype`, `.filterable`, `.index_seq`, ...). `bind=connection`
    # keeps this on the same AUTOCOMMIT connection the DDL below runs on,
    # rather than opening a second pooled connection.
    with Session(bind=connection) as session:
        definitions = list(session.execute(select(PropertyDefinition)).scalars().all())
    desired = _desired_by_name(desired_indexes(definitions, _registry()))

    actual = {
        row.name: (row.is_valid, row.comment) for row in connection.execute(_ACTUAL_STATE_SQL).all()
    }

    raw = connection.connection.driver_connection
    assert raw is not None  # a live Connection always has a driver connection

    def _execute(cursor: Any, statement: Any) -> None:
        if not dry_run:
            cursor.execute(statement)

    created: list[str] = []
    repaired_invalid: list[str] = []
    repaired_comment: list[str] = []
    with raw.cursor() as cursor:
        for name, index in desired.items():
            if name not in actual:
                _execute(cursor, create_statement(index))
                _execute(cursor, comment_statement(name, index.property_key))
                created.append(name)
                continue
            is_valid, comment = actual[name]
            if not is_valid:
                _execute(cursor, drop_statement(name))
                _execute(cursor, create_statement(index))
                _execute(cursor, comment_statement(name, index.property_key))
                repaired_invalid.append(name)
                continue
            if comment != index.property_key:
                _execute(cursor, comment_statement(name, index.property_key))
                repaired_comment.append(name)

        dropped = [name for name in actual if name not in desired]
        for name in dropped:
            _execute(cursor, drop_statement(name))

    return ReconciliationReport(
        created=tuple(created),
        dropped=tuple(dropped),
        repaired_invalid=tuple(repaired_invalid),
        repaired_comment=tuple(repaired_comment),
    )
