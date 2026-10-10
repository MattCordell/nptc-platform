"""The per-user submission quota (FR-43, NFR-24, NFR-08).

`enforce_submission_quota` counts what the user has already submitted and, if the role's quota is
used up, records the refusal and returns it. A new test and an amendment share one counter, and a
row counts whatever its kind or state, because a withdrawn submission still used a slot.

**A refusal is returned, never raised.** `nptc.db.session.session_scope` rolls back on any
exception, so an audit event written before a raised error would vanish with the request. The route
turns the returned value into a normal 429 response, the session commits, and the event stays.

**The lock and the count come before any network call.** An over-limit user is refused before the
terminology lookup and the reference fetch. The lock is per user, so it only makes that user's other
submissions wait. It is released at commit, which is after the caller's insert, so two requests for
the last slot cannot both count the slot as free.

**Lock order.** The per-user lock comes before the audit append lock, on the refusal path and on
the success path, and nothing takes them in the reverse order. The audit append lock stays global,
so it is never taken first here.

**The hourly window is a rolling hour on the database clock.** `now()` is the transaction's start,
which is also what `created_at` takes, so the application server's clock never enters.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import ColumnElement, and_, func, select, text
from sqlalchemy.orm import Session

from nptc.audit.recording import record_quota_refusal
from nptc.audit.writer import AuditContext
from nptc.auth.permissions import SubmissionQuota
from nptc.db.models.submission import Submission, SubmissionKind

__all__ = ["QuotaLimit", "QuotaRefusal", "enforce_submission_quota"]

_WINDOW = timedelta(hours=1)

#: `hashtext` is undocumented and has no cross-version stability contract, which is harmless
#: because the value is never stored (see `nptc.catalogue.collisions`).
_ACQUIRE_USER_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:key))")


class QuotaLimit(StrEnum):
    """Which limit was reached. A fixed set, so the API can name it without echoing anything."""

    LIFETIME = "lifetime"
    HOURLY = "hourly"


@dataclass(frozen=True)
class QuotaRefusal:
    """A used-up quota. `retry_after_seconds` is `None` when waiting does not help: a lifetime
    limit never lifts, and an hourly limit of zero never admits anyone."""

    limit: QuotaLimit
    maximum: int
    retry_after_seconds: int | None


def enforce_submission_quota(
    session: Session,
    ctx: AuditContext,
    *,
    quota: SubmissionQuota,
    kind: SubmissionKind,
) -> QuotaRefusal | None:
    """`None` when the user may submit, or the refusal after recording it in the audit trail.

    The lifetime limit is checked first, because it decides whether a wait helps at all. Takes
    no lock and runs no query when the quota has no limit.
    """
    user_id = ctx.actor_user_id
    if user_id is None:
        raise ValueError("a submission needs a human submitter, but the audit context has none")
    if quota.lifetime_max is None and quota.per_hour_max is None:
        return None

    session.execute(_ACQUIRE_USER_LOCK_SQL, {"key": f"submission-quota:{user_id}"})

    if quota.lifetime_max is not None:
        lifetime_count = _count(session, Submission.submitter_id == user_id)
        if lifetime_count >= quota.lifetime_max:
            refusal = QuotaRefusal(QuotaLimit.LIFETIME, quota.lifetime_max, None)
            _record(session, ctx, user_id, refusal, lifetime_count, kind)
            return refusal

    if quota.per_hour_max is not None:
        hourly_count = _count(session, _in_window(user_id))
        if hourly_count >= quota.per_hour_max:
            refusal = QuotaRefusal(
                QuotaLimit.HOURLY,
                quota.per_hour_max,
                _seconds_until_room(session, user_id, quota.per_hour_max),
            )
            _record(session, ctx, user_id, refusal, hourly_count, kind)
            return refusal

    return None


def _in_window(user_id: uuid.UUID) -> ColumnElement[bool]:
    return and_(Submission.submitter_id == user_id, Submission.created_at > func.now() - _WINDOW)


def _count(session: Session, condition: ColumnElement[bool]) -> int:
    return session.execute(
        select(func.count()).select_from(Submission).where(condition)
    ).scalar_one()


def _seconds_until_room(session: Session, user_id: uuid.UUID, maximum: int) -> int | None:
    """Whole seconds until the `maximum`-th newest submission in the window turns an hour old,
    which is the moment the count drops below `maximum`. Rounded up, never below 1, so a client that
    waits that long is not refused again. `None` for a maximum of zero, which no wait changes."""
    if maximum < 1:
        return None
    boundary = session.execute(
        select(Submission.created_at, func.now())
        .where(_in_window(user_id))
        .order_by(Submission.created_at.desc())
        .offset(maximum - 1)
        .limit(1)
    ).one()
    created_at, now = boundary
    return max(1, math.ceil((created_at + _WINDOW - now).total_seconds()))


def _record(
    session: Session,
    ctx: AuditContext,
    user_id: uuid.UUID,
    refusal: QuotaRefusal,
    count: int,
    kind: SubmissionKind,
) -> None:
    record_quota_refusal(
        session,
        ctx,
        user_id=user_id,
        limit=refusal.limit.value,
        maximum=refusal.maximum,
        count=count,
        submission_kind=kind.value,
    )
