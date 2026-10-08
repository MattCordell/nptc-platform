"""Runs `reconcile_property_indexes()` after a registry write commits (FR-13, FR-09).

A route calls `request_reconciliation(session)`. The run starts only after `session_scope` has
committed, on a worker thread, so the request never waits for `CREATE INDEX CONCURRENTLY` and the
reconciler reads the definition the request just wrote. ADR-0012 anticipated this shape, in
preference to the general job queue.

**One worker, and requests coalesce.** A single thread runs reconciliations one after another. A
request that arrives while one is already queued and not yet started adds nothing, because that
run reads `property_definition` when it starts and so sees this request's commit too. A request
that arrives while a run is under way queues the next one.

**Another process holding the lock is retried, not dropped.** `reconcile_property_indexes()`
returns `skipped_locked` when another run holds the advisory lock, and that run may have read
`property_definition` before this commit. Retrying until the lock is free makes this commit visible
to some run.

**Failure never reaches the request.** The definition write has committed by the time this runs.
A run that is unconfigured is logged as a warning only: that is a valid deployment (see
`IndexerSettings`). Any other failure is logged at error level and recorded as a system audit
event. Both carry index names and exception type names, never an exception message, which can hold
connection details (NFR-26).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Final, Protocol

from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext, append_audit_event
from nptc.db.property_reconciler import (
    IndexerNotConfiguredError,
    ReconciliationReport,
    reconcile_property_indexes,
)
from nptc.db.session import after_commit, get_sessionmaker

__all__ = ["request_reconciliation", "run_reconciliation"]

#: Attempts to get the advisory lock, and the pause between them: about eight seconds in all, longer
#: than one reconciliation of a handful of indexes takes on the catalogue's size.
LOCK_ATTEMPTS: Final[int] = 5
LOCK_RETRY_SECONDS: Final[float] = 2.0

FAILED_ACTION: Final[str] = "property_index.reconciliation_failed"
_ENTITY_TYPE: Final[str] = "property_index"
_ENTITY_ID: Final[str] = "property_value"

_log = logging.getLogger(__name__)


class _Submitter(Protocol):
    def submit(self, fn: Callable[[], object], /) -> object: ...


_executor: _Submitter = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="property-index-reconciler"
)
_queued = False
_queued_lock = threading.Lock()


def request_reconciliation(session: Session) -> None:
    """Queues one reconciliation to start after `session` commits. A rolled-back request queues
    nothing."""
    after_commit(session, _enqueue)


def _enqueue() -> None:
    global _queued
    with _queued_lock:
        if _queued:
            return
        _queued = True
    try:
        _executor.submit(run_reconciliation)
    except BaseException:
        with _queued_lock:
            _queued = False
        raise


def run_reconciliation() -> None:
    """One reconciliation with its retry and failure handling. Never raises."""
    global _queued
    with _queued_lock:
        _queued = False
    try:
        report = _reconcile_when_unlocked()
    except IndexerNotConfiguredError:
        _log.warning(
            "property index reconciliation is not configured (NPTC_INDEXER_DATABASE_URL is "
            "unset), so this change built no index; run scripts/reconcile_property_indexes.py"
        )
        return
    except Exception as exc:
        _record_failure(f"the reconciler raised {type(exc).__name__}")
        return
    reason = _failure_reason(report)
    if reason is not None:
        _record_failure(reason)
    elif report.changed:
        _log.info("property index reconciliation changed indexes")


def _reconcile_when_unlocked() -> ReconciliationReport:
    report = reconcile_property_indexes()
    for _ in range(LOCK_ATTEMPTS - 1):
        if not report.skipped_locked:
            break
        time.sleep(LOCK_RETRY_SECONDS)
        report = reconcile_property_indexes()
    return report


def _failure_reason(report: ReconciliationReport) -> str | None:
    if report.skipped_locked:
        return f"another reconciliation held the lock through {LOCK_ATTEMPTS} attempts"
    parts = [f"{name} ({exc_type})" for name, exc_type in report.failed]
    parts += [f"{key} (no handler for its datatype)" for key in report.skipped_unknown_datatype]
    if parts:
        return "did not converge: " + ", ".join(parts)
    return None


def _record_failure(reason: str) -> None:
    _log.error("property index reconciliation failed: %s", reason)
    try:
        with get_sessionmaker()() as session:
            append_audit_event(
                session,
                AuditContext.system(),
                action=FAILED_ACTION,
                entity_type=_ENTITY_TYPE,
                entity_id=_ENTITY_ID,
                reason=reason,
            )
            session.commit()
    except Exception as exc:
        _log.error("could not record the reconciliation failure (%s)", type(exc).__name__)
