"""The FR-13 DDL executor: reads `property_definition`, computes the desired index set
(`nptc.db.property_indexes.desired_indexes`), diffs it against actual `pg_index` state, and creates,
drops or repairs to converge.

It is an in-process library call plus a CLI (`scripts/reconcile_property_indexes.py`), not a
separate deployable. It runs on its own `NPTC_INDEXER_DATABASE_URL` credential over an AUTOCOMMIT
connection, because `CREATE INDEX CONCURRENTLY` raises `25001` inside a transaction block. ADR-0012
records the topology, the credential and why the reconciler is desired-state rather than an event
handler.

This module does not wire the reconciler into `create_app()`: that would make the API require a DDL
credential at boot. A future write path can dispatch `reconcile_property_indexes()` as a background
task.
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

#: Session-scoped (`pg_try_advisory_lock`), not `_xact_lock` like
#: `nptc.audit.writer.AUDIT_APPEND_LOCK_KEY`, because `CREATE INDEX CONCURRENTLY` requires
#: autocommit and so there is no transaction to tie the lock to. A different key from the audit
#: lock, so the two never collide.
RECONCILE_LOCK_KEY: Final[int] = 74619328

_TRY_LOCK_SQL = text("SELECT pg_try_advisory_lock(:key)")
_UNLOCK_SQL = text("SELECT pg_advisory_unlock(:key)")

#: Reads `pg_index`, `pg_class` and `pg_namespace` rather than `pg_indexes`, because that view hides
#: `indisvalid`: a failed `CREATE INDEX CONCURRENTLY` would read as present and never be repaired.
#: `obj_description` recovers the `COMMENT ON INDEX` property key. `pg_get_indexdef` recovers the
#: actual expression and predicate, which `matches_indexdef` compares with the property's current
#: `datatype`; that column is mutable, unlike the `index_seq` the name derives from. The `~` pattern
#: is a constant mirroring `GENERATED_INDEX_NAME_RE`.
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
    """Raised by `get_indexer_engine()` when `NPTC_INDEXER_DATABASE_URL` is unset. Unconfigured
    reconciliation is a safe state, but attempting it must fail rather than fall back to a
    credential that cannot do DDL.
    """


class _UnreachableTerminologyClient:
    """Satisfies `HandlerDeps.terminology_client` without ever being called: `index_shape()`, the
    only handler method used here, needs none of FR-53's operations. Every method raises, so a
    handler change that makes `index_shape()` reach the client fails in CI. Not
    `StubTerminologyClient`, which is test-only (NFR-37).
    """

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
    """What one `reconcile_property_indexes()` call did, returned so a caller can act on it without
    re-querying `pg_index`.
    """

    created: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    repaired_invalid: tuple[str, ...] = ()
    rebuilt_stale_definition: tuple[str, ...] = ()
    repaired_comment: tuple[str, ...] = ()
    #: `(index_name, exception_type_name)` pairs for DDL that raised mid-run. One failure does not
    #: stop the other indexes converging.
    #:
    #: A name here can also appear in another field: a `CREATE` that succeeded before its `COMMENT
    #: ON INDEX` raised is in `created` and `failed`, and a rebuild whose `DROP` succeeded but whose
    #: `CREATE` raised is in `dropped` and `failed` only, which tells an operator the property now
    #: has no index. Carries the exception type name, never its message (NFR-26).
    failed: tuple[tuple[str, str], ...] = ()
    #: Property keys of filterable rows whose `datatype` has no handler in this build. One such row
    #: does not abort the run, and its existing index is not dropped as an orphan. See
    #: `nptc.db.property_indexes.UnknownDatatypeProperty`.
    skipped_unknown_datatype: tuple[str, ...] = ()
    skipped_locked: bool = False

    @property
    def changed(self) -> bool:
        """Whether this run created, dropped, rebuilt or re-commented an index. Excludes `failed`;
        use `converged` to ask whether everything succeeded.
        """
        return bool(
            self.created
            or self.dropped
            or self.repaired_invalid
            or self.rebuilt_stale_definition
            or self.repaired_comment
        )

    @property
    def converged(self) -> bool:
        """True when nothing failed and no filterable property lacked a handler, whether or not
        anything needed changing.
        """
        return not self.failed and not self.skipped_unknown_datatype


@lru_cache(maxsize=1)
def get_indexer_engine(database_url: str | None = None) -> Engine:
    """The indexer engine, built from `database_url` if given, else `NPTC_INDEXER_DATABASE_URL`.

    `database_url` lets the CLI's `--database-url` pass the DSN directly, rather than writing a
    DDL-capable credential into `os.environ` where every subprocess would inherit it. `lru_cache`
    keys on the argument, so the same `database_url` returns the same `Engine`; a different one
    evicts the previous entry, which leaks nothing because `NullPool` holds no connection.

    `NullPool` keeps no idle DDL-capable connection between runs. `AUTOCOMMIT` is required because
    `CREATE INDEX CONCURRENTLY` raises `25001` in a transaction block;
    `test_creating_a_generated_index_without_autocommit_fails_loudly` asserts it.
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
    """Converges actual `pg_index` state on every filterable property's desired index.

    Builds missing indexes, drops orphans (an un-flagged property, or one whose `index_shape()` is
    now `None`), rebuilds indexes that are `indisvalid = false` or whose definition is stale against
    an amended `datatype` or `key` (`matches_indexdef`), and repairs a missing or stale `COMMENT ON
    INDEX`.

    Idempotent. A `pg_try_advisory_lock` guards the whole run, so a concurrent caller returns
    `skipped_locked=True` instead of blocking: two runs converging on the same state are a no-op,
    not a race.

    `dry_run=True` (the CLI's `--dry-run`) returns the same report without executing DDL.
    `database_url` is forwarded to `get_indexer_engine`.
    """
    engine = get_indexer_engine(database_url)
    with engine.connect() as connection:
        locked: bool = connection.execute(_TRY_LOCK_SQL, {"key": RECONCILE_LOCK_KEY}).scalar_one()
        if not locked:
            return ReconciliationReport(skipped_locked=True)
        try:
            return _reconcile_locked(connection, dry_run=dry_run)
        finally:
            # Best-effort: if the connection died, retrying the unlock would raise again and mask
            # the original exception. The lock is session-scoped on a connection that closes anyway.
            with contextlib.suppress(Exception):
                connection.execute(_UNLOCK_SQL, {"key": RECONCILE_LOCK_KEY})


def _reconcile_locked(connection: Connection, *, dry_run: bool) -> ReconciliationReport:
    # A `Session`, not `Connection.execute(select(...))`: `desired_indexes()` needs hydrated ORM
    # attributes, not Core rows. `bind=connection` keeps it on the AUTOCOMMIT connection the DDL
    # uses.
    with Session(bind=connection) as session:
        definitions = list(session.execute(select(PropertyDefinition)).scalars().all())
    desired_list, unknown_datatype = desired_indexes(definitions, _registry())
    desired = _desired_by_name(desired_list)
    # Held back from the orphan sweep: a filterable property whose `datatype` this build cannot look
    # up is not un-flagged, so its existing index must not be dropped.
    protected_names = {row.name for row in unknown_datatype}

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
            # A per-index `try/except`, so one failure does not stop the others. Safe under
            # AUTOCOMMIT: a failed statement leaves no aborted transaction behind.
            try:
                if name not in actual:
                    _execute(cursor, create_statement(index))
                    # Recorded before the comment is attempted, so a `COMMENT ON INDEX` that raises
                    # still reports the index as created.
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
                        # The DROP already succeeded, so the index is gone: record it in `dropped`
                        # before the exception reaches the outer handler that records `failed`.
                        dropped.append(name)
                        raise
                    repaired_invalid.append(name)
                    _execute(cursor, comment_statement(name, index.property_key))
                    continue
                if not matches_indexdef(index, indexdef):
                    # Valid but no longer matching the property's current `datatype`. Only the index
                    # definition, not its name or validity, shows that drift. `key` is compared as
                    # defence in depth.
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
                    # The connection died, not one statement: stop rather than log a `failed` entry
                    # per remaining index for one root cause. The next run picks up the rest.
                    break

        orphaned = [name for name in actual if name not in desired and name not in protected_names]
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
        skipped_unknown_datatype=tuple(row.property_key for row in unknown_datatype),
    )
