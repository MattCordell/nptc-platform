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

import contextlib
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
    matches_indexdef,
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
#: comment_statement`) so a missing/stale comment can be repaired too.
#: `pg_get_indexdef` recovers the index's actual expression/predicate, which
#: `nptc.db.property_indexes.matches_indexdef` diffs against the property's
#: *current* `datatype` - an ordinary mutable, audited column, unlike the
#: immutable `index_seq` the index name is derived from - so an index built
#: before an amendment can look "present and valid" while serving nothing a
#: current `filter_clause` renders (issue #54 review; `key` is compared too,
#: as defence in depth, though `PropertyDefinition.key` is itself immutable
#: once set - see `matches_indexdef`'s own docstring). A fixed literal
#: query, no runtime data - the `~` pattern is a compile-time constant
#: mirroring `GENERATED_INDEX_NAME_RE`, never
#: interpolated.
_ACTUAL_STATE_SQL = text(
    "SELECT c.relname AS name, i.indisvalid AS is_valid, "
    "obj_description(c.oid, 'pg_class') AS comment, "
    "pg_get_indexdef(i.indexrelid) AS indexdef "
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
    rebuilt_stale_definition: tuple[str, ...] = ()
    repaired_comment: tuple[str, ...] = ()
    #: `(index_name, exception_type_name)` pairs for a DDL statement that
    #: raised mid-run (issue #54 review) - one failure no longer aborts the
    #: whole convergence, so every other desired/orphaned index still gets a
    #: chance to converge in the same run. A name here does not exclude it
    #: from `created`/`dropped`/`repaired_invalid`/`rebuilt_stale_definition`:
    #: e.g. a `CREATE INDEX` that succeeded followed by a `COMMENT ON INDEX`
    #: that raised reports the name in both `created` and `failed`, rather
    #: than in neither; a rebuild whose `DROP` succeeded but whose `CREATE`
    #: then raised reports the name in `dropped` and `failed` (never in
    #: `repaired_invalid`/`rebuilt_stale_definition`, since the replacement
    #: was never actually built) - accurately telling an operator the
    #: property currently has *no* index, not merely that something went
    #: wrong. Never carries the exception's own message text (NFR-26) -
    #: only its type name, matching the CLI's existing posture.
    failed: tuple[tuple[str, str], ...] = ()
    skipped_locked: bool = False

    @property
    def changed(self) -> bool:
        """Whether this run did anything - created, dropped, rebuilt, or
        repaired a comment. Deliberately excludes `failed` (issue #54
        review, third pass): a name that only ever failed was never
        actually changed, so folding `failed` in here made `changed` answer
        neither "did this run alter anything" nor "did everything
        converge". Use `converged` for the latter question - a caller that
        cares whether it is safe to treat this run as fully successful
        should check `converged`, not infer it from `changed`."""
        return bool(
            self.created
            or self.dropped
            or self.repaired_invalid
            or self.rebuilt_stale_definition
            or self.repaired_comment
        )

    @property
    def converged(self) -> bool:
        """True when nothing failed to converge this run - independent of
        whether anything needed to change. False exactly when `failed` is
        non-empty (issue #54 review, third pass): the caller #55/#138 will
        add (a background task dispatched right after a `property_
        definition` write commits) needs this question answered directly,
        not reconstructed from `changed`."""
        return not self.failed


@lru_cache(maxsize=1)
def get_indexer_engine(database_url: str | None = None) -> Engine:
    """The indexer engine, built from `database_url` if given, else
    `NPTC_INDEXER_DATABASE_URL`.

    `database_url` lets a caller (the CLI's `--database-url`) supply the DSN
    directly rather than smuggling it in via `os.environ` (issue #54
    review): writing a DDL-capable credential into the process environment
    makes it visible to any subprocess this process ever spawns, purely to
    get it past this function's own `IndexerSettings()` read. `lru_cache`
    still applies, keyed on the argument - calling this twice with the same
    `database_url` (including the default `None`) returns the same `Engine`,
    matching the "one process-wide engine" posture `nptc.db.session.
    get_engine` also takes; a *different* `database_url` simply gets its own
    cached `Engine` (evicting the previous one under `maxsize=1`), which
    costs nothing extra since `NullPool` never held an open connection for
    the evicted entry to leak.

    `poolclass=NullPool`: no idle owner-scoped connection sits in a pool
    between reconciliations - this credential can do DDL, so the fewer open
    connections carrying it, the better. `isolation_level="AUTOCOMMIT"`:
    `CREATE INDEX CONCURRENTLY` raises `25001` ("CREATE INDEX CONCURRENTLY
    cannot run inside a transaction block") otherwise - verified directly
    against `postgres:18.6`, and asserted by
    `test_db_property_indexes.py::test_create_statement_fails_loudly_
    without_autocommit`.
    """
    if database_url is None:
        settings = IndexerSettings()
        database_url = settings.indexer_database_url
        if not database_url:
            raise IndexerNotConfiguredError(
                "NPTC_INDEXER_DATABASE_URL is not set - index reconciliation is disabled"
            )
    return create_engine(
        database_url,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )


def _desired_by_name(desired: list[DesiredIndex]) -> dict[str, DesiredIndex]:
    return {index.name: index for index in desired}


def reconcile_property_indexes(
    *, dry_run: bool = False, database_url: str | None = None
) -> ReconciliationReport:
    """Converges actual `pg_index` state on every filterable property's
    desired index, in one call: builds missing indexes, drops orphaned
    ones (a property that was un-flagged, or whose `index_shape()` now
    returns `None`), rebuilds any that failed into `indisvalid = false` (or
    whose definition has gone stale against an amended `datatype`/`key` -
    see `nptc.db.property_indexes.matches_indexdef`), and repairs a missing
    or stale `COMMENT ON INDEX`.

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

    `database_url`, forwarded to `get_indexer_engine`, lets a caller supply
    the DSN directly instead of `NPTC_INDEXER_DATABASE_URL`.
    """
    engine = get_indexer_engine(database_url)
    with engine.connect() as connection:
        locked = connection.execute(_TRY_LOCK_SQL, {"key": RECONCILE_LOCK_KEY}).scalar_one()
        if not locked:
            return ReconciliationReport(skipped_locked=True)
        try:
            return _reconcile_locked(connection, dry_run=dry_run)
        finally:
            # Best-effort: if `_reconcile_locked` raised because the
            # connection itself died, retrying the unlock on it would raise
            # again and mask the original exception (issue #54 review). The
            # lock is session-scoped on a connection that is about to close
            # anyway (`NullPool`), so losing it here costs nothing a process
            # exit/reconnect wouldn't already reclaim.
            with contextlib.suppress(Exception):
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
        row.name: (row.is_valid, row.comment, row.indexdef)
        for row in connection.execute(_ACTUAL_STATE_SQL).all()
    }

    raw = connection.connection.driver_connection
    assert raw is not None  # a live Connection always has a driver connection

    def _execute(cursor: Any, statement: Any) -> None:
        if not dry_run:
            cursor.execute(statement)

    created: list[str] = []
    dropped: list[str] = []
    repaired_invalid: list[str] = []
    rebuilt_stale_definition: list[str] = []
    repaired_comment: list[str] = []
    failed: list[tuple[str, str]] = []
    with raw.cursor() as cursor:
        for name, index in desired.items():
            # One index's failure must not abort every other index's own
            # convergence in this run (issue #54 review) - a per-index
            # `try/except`, not one around the whole loop. Safe under
            # AUTOCOMMIT: each statement is its own implicit transaction, so
            # a failed one leaves no aborted transaction behind for the next
            # statement on this connection to inherit.
            try:
                if name not in actual:
                    _execute(cursor, create_statement(index))
                    # Recorded as created before the comment is even
                    # attempted: a `COMMENT ON INDEX` that then raises must
                    # still report this index as created, not as neither
                    # created nor failed.
                    created.append(name)
                    _execute(cursor, comment_statement(name, index.property_key))
                    continue
                is_valid, comment, indexdef = actual[name]
                if not is_valid:
                    _execute(cursor, drop_statement(name))
                    try:
                        _execute(cursor, create_statement(index))
                    except Exception:
                        # The DROP already succeeded - the index is
                        # genuinely gone now, not merely "something went
                        # wrong" (issue #54 review, third pass). Recorded
                        # here, scoped to just the CREATE, rather than an
                        # append-then-undo around both statements: an
                        # operator reading `failed` alone cannot tell
                        # whether the property still has *an* index, just a
                        # stale one, or none at all, so `dropped` must say
                        # so before the exception propagates to the outer
                        # handler that records `failed`.
                        dropped.append(name)
                        raise
                    repaired_invalid.append(name)
                    _execute(cursor, comment_statement(name, index.property_key))
                    continue
                if not matches_indexdef(index, indexdef):
                    # The index is `indisvalid` but no longer matches this
                    # property's current `datatype` - a mutable, audited
                    # column unrelated to the immutable `index_seq` the
                    # index name is derived from, so only the actual index
                    # *definition* (not its name or validity) can notice the
                    # drift (issue #54 review; `key` is compared too as
                    # defence in depth, though it is not actually mutable in
                    # practice - see `matches_indexdef`'s own docstring).
                    _execute(cursor, drop_statement(name))
                    try:
                        _execute(cursor, create_statement(index))
                    except Exception:
                        dropped.append(name)  # see the repaired_invalid branch above
                        raise
                    rebuilt_stale_definition.append(name)
                    _execute(cursor, comment_statement(name, index.property_key))
                    continue
                if comment != index.property_key:
                    _execute(cursor, comment_statement(name, index.property_key))
                    repaired_comment.append(name)
            except Exception as exc:
                failed.append((name, type(exc).__name__))
                if raw.closed:
                    # The connection itself died, not just this one
                    # statement (issue #54 review, minor) - every remaining
                    # desired index would otherwise get its own noisy
                    # `failed` entry for the same root cause. Stop here; the
                    # unattempted names are simply not in this run's report
                    # at all, and the next run picks them up normally.
                    break

        orphaned = [name for name in actual if name not in desired]
        for name in orphaned:
            if raw.closed:
                break
            try:
                _execute(cursor, drop_statement(name))
                dropped.append(name)
            except Exception as exc:
                failed.append((name, type(exc).__name__))
                if raw.closed:
                    break

    return ReconciliationReport(
        created=tuple(created),
        dropped=tuple(dropped),
        repaired_invalid=tuple(repaired_invalid),
        rebuilt_stale_definition=tuple(rebuilt_stale_definition),
        repaired_comment=tuple(repaired_comment),
        failed=tuple(failed),
    )
