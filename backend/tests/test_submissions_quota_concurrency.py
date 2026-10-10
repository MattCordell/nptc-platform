"""Concurrency and lock order of the submission quota (FR-43, NFR-08).

The race test uses two real connections (`app_engine`), because a single session running the same
code twice cannot reproduce two transactions that both read a count before either commits. Counts are
taken for the user the fixture created, because `backend/tests` shares one Postgres container (see
`CLAUDE.md`). `pristine_audit_event` is requested to clean up the audit rows the committed writes
leave behind, not because an assertion depends on an empty table.
"""

from __future__ import annotations

import importlib.util
import random
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from nptc.audit.writer import AUDIT_APPEND_LOCK_KEY, AuditContext
from nptc.auth.permissions import QUOTAS, Role, SubmissionQuota
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.db.models.user import User
from nptc.submissions.amendment import AmendmentInput, create_amendment_submission
from nptc.submissions.quota import QuotaLimit, QuotaRefusal, enforce_submission_quota
from nptc_shared.terminology import StubTerminologyClient


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


StubReferenceChecker = _load("api_app_support").StubReferenceChecker
add_submissions = _load("quota_support").add_submissions

_REFUSED_ACTION = "submission.quota_refused"


def _context(user_id: uuid.UUID) -> AuditContext:
    return AuditContext(
        actor_user_id=user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


@pytest.fixture
def racing_user(
    pristine_audit_event: None, app_engine: Engine, owner_engine: Engine
) -> Iterator[tuple[uuid.UUID, str]]:
    """A committed user holding four stored submissions, and the key of a committed active entry
    to amend. A Provisional quota is five, so one slot is left. Declared after
    `pristine_audit_event`, so this teardown runs first and clears the submissions the user table's
    wipe would otherwise trip over."""
    with Session(app_engine) as setup:
        user = User(username=f"quota-race-{uuid.uuid4()}", display_name="Quota Racer")
        entry = CatalogueEntry(
            business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
            preferred_term=f"Quota race entry {uuid.uuid4()}",
            status="active",
        )
        setup.add_all([user, entry])
        setup.flush()
        add_submissions(setup, user.id, 4)
        user_id, business_key, entry_id = user.id, entry.business_key, entry.id
        setup.commit()
    yield user_id, business_key
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM submission WHERE submitter_id = :u"), {"u": user_id})
        connection.execute(text("DELETE FROM catalogue_entry WHERE id = :e"), {"e": entry_id})


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_two_concurrent_requests_for_the_last_slot_admit_exactly_one(
    racing_user: tuple[uuid.UUID, str], app_engine: Engine
) -> None:
    """Each request counts, waits a moment, then saves. A request that did not hold the per-user
    lock would count four in both transactions and both would save. The loser is refused either
    because the lock is busy or, if it arrives after the winner commits, because the count is full,
    so only the stored total is asserted."""
    user_id, business_key = racing_user
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    errors: dict[str, BaseException] = {}

    def attempt(name: str) -> None:
        session = Session(app_engine)
        try:
            ctx = _context(user_id)
            barrier.wait(timeout=5)
            refusal = enforce_submission_quota(
                session, ctx, quota=QUOTAS[Role.PROVISIONAL], kind=SubmissionKind.AMENDMENT
            )
            if refusal is None:
                time.sleep(0.5)
                create_amendment_submission(
                    session,
                    ctx,
                    content=AmendmentInput(
                        entry_business_key=business_key, synonyms=[f"Race synonym {name}"]
                    ),
                    profile_organisation=None,
                    terminology_client=StubTerminologyClient(),
                    reference_checker=StubReferenceChecker(),
                )
                results[name] = "accepted"
            else:
                results[name] = "refused"
            session.commit()
        except BaseException as exc:  # surfaced below, not swallowed
            session.rollback()
            errors[name] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not any(thread.is_alive() for thread in threads), (
        "a request hung - looks like a deadlock"
    )
    assert errors == {}
    assert sorted(results.values()) == ["accepted", "refused"]
    with Session(app_engine) as check:
        stored = check.execute(
            select(func.count()).select_from(Submission).where(Submission.submitter_id == user_id)
        ).scalar_one()
    assert stored == 5


# --- lock order --------------------------------------------------------------------------------


def _is_lock_or_submission_read(statement: str, parameters: object) -> bool:
    return "advisory_xact_lock" in statement or "FROM submission" in statement


def _is_user_lock(statement: str) -> bool:
    return "advisory_xact_lock" in statement and "hashtext" in statement


def _is_append_lock(statement: str, parameters: object) -> bool:
    return "advisory_xact_lock" in statement and "hashtext" not in statement


def _user(session: Session) -> tuple[User, AuditContext]:
    user = User(username=f"quota-order-{uuid.uuid4()}", display_name="Quota Order")
    session.add(user)
    session.flush()
    return user, _context(user.id)


def _enforce(session: Session, ctx: AuditContext, quota: SubmissionQuota) -> QuotaRefusal | None:
    return enforce_submission_quota(session, ctx, quota=quota, kind=SubmissionKind.NEW_TEST)


@pytest.mark.req("FR-43")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_user_lock_comes_before_the_count_and_the_audit_append_lock(
    app_session: Session, app_db: Connection, capture_statements: Any
) -> None:
    """The per-user lock is taken first, then the count is read, and only a refusal reaches the
    global audit append lock. Nothing takes them the other way round, so they cannot deadlock."""
    user, ctx = _user(app_session)
    add_submissions(app_session, user.id, 5)

    with capture_statements(app_db, keep=_is_lock_or_submission_read) as statements:
        refusal = _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    assert refusal is not None
    user_lock = next(i for i, s in enumerate(statements) if _is_user_lock(s))
    first_count = next(i for i, s in enumerate(statements) if "FROM submission" in s)
    append_lock = next(i for i, s in enumerate(statements) if _is_append_lock(s, None))
    assert user_lock < first_count < append_lock


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_request_with_room_takes_the_user_lock_and_never_the_audit_lock(
    app_session: Session, app_db: Connection, capture_statements: Any
) -> None:
    """The audit append lock is global, so a request that is let through must not take it here:
    the create function takes it later, just before its own write."""
    user, ctx = _user(app_session)
    add_submissions(app_session, user.id, 1)

    with capture_statements(app_db, keep=_is_lock_or_submission_read) as statements:
        refusal = _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    assert refusal is None
    assert any(_is_user_lock(s) for s in statements)
    assert not any(_is_append_lock(s, None) for s in statements)


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_role_with_no_limit_takes_no_lock_and_reads_nothing(
    app_session: Session, app_db: Connection, capture_statements: Any
) -> None:
    _, ctx = _user(app_session)

    with capture_statements(app_db, keep=_is_lock_or_submission_read) as statements:
        refusal = _enforce(app_session, ctx, QUOTAS[Role.REVIEWER])

    assert refusal is None
    assert statements == []


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_the_user_lock_key_is_bound_as_a_parameter_and_names_the_user(
    app_session: Session, app_db: Connection, capture_statements: Any
) -> None:
    user, ctx = _user(app_session)
    keys: list[object] = []

    def keep(statement: str, parameters: object) -> bool:
        if _is_user_lock(statement) and isinstance(parameters, dict):
            keys.append(parameters.get("key"))
        return _is_user_lock(statement)

    with capture_statements(app_db, keep=keep):
        _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    assert keys == [f"submission-quota:{user.id}"]
    assert AUDIT_APPEND_LOCK_KEY not in keys


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_request_that_finds_the_user_lock_taken_is_refused_at_once_and_not_audited(
    racing_user: tuple[uuid.UUID, str], app_engine: Engine
) -> None:
    """A waiter would hold a pooled connection for as long as the holder's network calls take, so
    the lock is tried and a busy one is a refusal. The holder here is a second open transaction."""
    user_id, _ = racing_user
    holder = Session(app_engine)
    waiter = Session(app_engine)
    try:
        assert (
            enforce_submission_quota(
                holder,
                _context(user_id),
                quota=QUOTAS[Role.PROVISIONAL],
                kind=SubmissionKind.NEW_TEST,
            )
            is None
        )

        started = time.monotonic()
        refusal = enforce_submission_quota(
            waiter,
            _context(user_id),
            quota=QUOTAS[Role.PROVISIONAL],
            kind=SubmissionKind.NEW_TEST,
        )
        waited = time.monotonic() - started
        waiter.commit()
    finally:
        holder.rollback()
        holder.close()
        waiter.close()

    assert refusal == QuotaRefusal(QuotaLimit.CONCURRENT, 1, 5)
    assert waited < 2
    with Session(app_engine) as check:
        refusals = check.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == _REFUSED_ACTION, AuditEvent.entity_id == str(user_id))
        ).scalar_one()
    assert refusals == 0
