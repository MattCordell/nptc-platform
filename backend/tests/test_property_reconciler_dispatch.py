"""`nptc.db.property_reconciler_dispatch` (FR-13, FR-09): when a queued reconciliation runs, how it
retries a held lock, and what an operator sees when it does not converge.

`reconcile_property_indexes` has its own tests against a database (`test_db_property_indexes.py`),
so these stub it and check only the dispatcher's decisions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from nptc.db import property_reconciler_dispatch as dispatch
from nptc.db.models.audit import AuditEvent
from nptc.db.property_reconciler import IndexerNotConfiguredError, ReconciliationReport

_SECRET = "postgresql://nptc_indexer:hunter2@db/nptc"


class _Reconciler:
    """Stands in for `reconcile_property_indexes`: returns or raises each outcome in turn, then
    repeats the last."""

    def __init__(self, *outcomes: ReconciliationReport | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> ReconciliationReport:
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def failures(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The reasons `_record_failure` was given, without writing an audit event."""
    recorded: list[str] = []
    monkeypatch.setattr(dispatch, "_record_failure", recorded.append)
    monkeypatch.setattr(dispatch, "LOCK_RETRY_SECONDS", 0.0)
    return recorded


def _use(
    monkeypatch: pytest.MonkeyPatch, *outcomes: ReconciliationReport | Exception
) -> _Reconciler:
    reconciler = _Reconciler(*outcomes)
    monkeypatch.setattr(dispatch, "reconcile_property_indexes", reconciler)
    return reconciler


_LOCKED = ReconciliationReport(skipped_locked=True)
_CONVERGED = ReconciliationReport(created=("ix_propval_p1_1",))


@pytest.mark.req("FR-13")
def test_a_converged_run_records_nothing(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> None:
    reconciler = _use(monkeypatch, _CONVERGED)

    dispatch.run_reconciliation()

    assert reconciler.calls == 1
    assert failures == []


@pytest.mark.req("FR-13")
def test_an_unconfigured_indexer_logs_a_warning_and_records_no_failure(
    monkeypatch: pytest.MonkeyPatch, failures: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    _use(monkeypatch, IndexerNotConfiguredError("NPTC_INDEXER_DATABASE_URL is not set"))

    with caplog.at_level(logging.WARNING, logger=dispatch.__name__):
        dispatch.run_reconciliation()

    assert failures == []
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "NPTC_INDEXER_DATABASE_URL" in caplog.text


@pytest.mark.req("FR-13")
def test_a_held_lock_is_retried_until_a_run_gets_it(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> None:
    """A skipped run may have read `property_definition` before this commit, so giving up would
    leave this commit's change unreconciled."""
    reconciler = _use(monkeypatch, _LOCKED, _LOCKED, _CONVERGED)

    dispatch.run_reconciliation()

    assert reconciler.calls == 3
    assert failures == []


@pytest.mark.req("FR-13")
def test_a_lock_held_through_every_attempt_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> None:
    reconciler = _use(monkeypatch, _LOCKED)

    dispatch.run_reconciliation()

    assert reconciler.calls == dispatch.LOCK_ATTEMPTS
    assert len(failures) == 1
    assert "lock" in failures[0]


@pytest.mark.req("FR-13")
def test_a_raised_exception_is_a_failure_that_names_only_its_type(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> None:
    _use(monkeypatch, ConnectionError(f"could not connect to {_SECRET}"))

    dispatch.run_reconciliation()

    assert failures == ["the reconciler raised ConnectionError"]


@pytest.mark.req("FR-13")
def test_an_index_that_failed_to_build_is_a_failure_naming_the_index_and_error_type(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> None:
    _use(monkeypatch, ReconciliationReport(failed=(("ix_propval_p7_1", "InsufficientPrivilege"),)))

    dispatch.run_reconciliation()

    assert failures == ["did not converge: ix_propval_p7_1 (InsufficientPrivilege)"]


@pytest.mark.req("FR-13")
def test_a_filterable_property_with_no_handler_is_a_failure_naming_its_key(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> None:
    _use(monkeypatch, ReconciliationReport(skipped_unknown_datatype=("orphaned_property",)))

    dispatch.run_reconciliation()

    assert failures == ["did not converge: orphaned_property (no handler for its datatype)"]


@pytest.mark.req("FR-13")
def test_queued_requests_coalesce_until_the_run_starts(
    reconciliation_submissions: list[Callable[[], object]],
    monkeypatch: pytest.MonkeyPatch,
    failures: list[str],
) -> None:
    """Two rapid amendments need one run, not two: the run reads `property_definition` when it
    starts, so it sees both commits. A request that arrives once a run has started queues another,
    because that run may already have read."""
    _use(monkeypatch, _CONVERGED)
    dispatch._enqueue()
    dispatch._enqueue()
    assert len(reconciliation_submissions) == 1

    reconciliation_submissions[0]()
    dispatch._enqueue()

    assert len(reconciliation_submissions) == 2


@pytest.mark.req("FR-13")
def test_a_submit_that_raises_does_not_stop_later_requests_from_queueing(
    monkeypatch: pytest.MonkeyPatch,
    reconciliation_submissions: list[Callable[[], object]],
) -> None:
    """A worker that refuses new work (the interpreter is shutting down) must not leave `_queued`
    set, or every later request in this process would coalesce into a run that never starts."""

    class _Refusing:
        def submit(self, fn: Callable[[], object], /) -> None:
            raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(dispatch, "_executor", _Refusing())
    with pytest.raises(RuntimeError):
        dispatch._enqueue()

    class _Recording:
        def submit(self, fn: Callable[[], object], /) -> None:
            reconciliation_submissions.append(fn)

    monkeypatch.setattr(dispatch, "_executor", _Recording())
    dispatch._enqueue()

    assert len(reconciliation_submissions) == 1


@pytest.mark.req("FR-13")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_failure_is_recorded_as_a_system_audit_event(
    app_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def factory() -> sessionmaker[Session]:
        return sessionmaker(bind=app_db, join_transaction_mode="create_savepoint")

    monkeypatch.setattr(dispatch, "get_sessionmaker", factory)
    reason = "the reconciler raised ConnectionError (dispatch audit test)"

    dispatch._record_failure(reason)

    with Session(bind=app_db) as session:
        event = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == dispatch.FAILED_ACTION, AuditEvent.reason == reason
            )
        ).scalar_one()
    assert event.actor_user_id is None
    assert event.entity_type == "property_index"
    assert event.entity_id == "property_value"


@pytest.mark.req("FR-13")
def test_a_failure_to_write_the_audit_event_is_logged_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse() -> Any:
        raise ConnectionError(f"could not connect to {_SECRET}")

    monkeypatch.setattr(dispatch, "get_sessionmaker", refuse)

    with caplog.at_level(logging.ERROR, logger=dispatch.__name__):
        dispatch._record_failure("did not converge")

    assert "ConnectionError" in caplog.text
    assert "hunter2" not in caplog.text
