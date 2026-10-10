"""`nptc.submissions.quota.enforce_submission_quota` service-layer tests (FR-43, NFR-24, NFR-08).

Every count is taken for a user this test created, and audit events are read for that user's id,
because `backend/tests` shares one Postgres container (see `CLAUDE.md`). Rows are backdated with an
explicit `created_at` taken from the database clock, never the test machine's.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.permissions import QUOTAS, Role, SubmissionQuota, effective_quota
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.db.models.user import User
from nptc.submissions.quota import QuotaLimit, QuotaRefusal, enforce_submission_quota

_REFUSED_ACTION = "submission.quota_refused"


def _user(session: Session) -> tuple[User, AuditContext]:
    user = User(username=f"quota-{uuid.uuid4()}", display_name="Quota Tester")
    session.add(user)
    session.flush()
    ctx = AuditContext(
        actor_user_id=user.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )
    return user, ctx


def _entry(session: Session) -> CatalogueEntry:
    entry = CatalogueEntry(
        business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
        preferred_term=f"Quota entry {uuid.uuid4()}",
        status="active",
    )
    session.add(entry)
    session.flush()
    return entry


def _submit(
    session: Session,
    user: User,
    count: int,
    *,
    kind: SubmissionKind = SubmissionKind.NEW_TEST,
    age: timedelta = timedelta(0),
) -> None:
    """Adds `count` stored submissions for `user`, each created `age` before the database's now."""
    created_at = session.execute(select(func.now())).scalar_one() - age
    entry_id = _entry(session).id if kind is SubmissionKind.AMENDMENT else None
    for _ in range(count):
        is_new_test = kind is SubmissionKind.NEW_TEST
        session.add(
            Submission(
                kind=kind.value,
                preferred_term=f"Quota submission {uuid.uuid4()}",
                submitter_id=user.id,
                entry_id=entry_id,
                reference_url="https://example.org/evidence" if is_new_test else None,
                reference_checked_at=created_at if is_new_test else None,
                reference_status=200 if is_new_test else None,
                created_at=created_at,
            )
        )
    session.flush()


def _refusal_events(session: Session, user: User) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == _REFUSED_ACTION, AuditEvent.entity_id == str(user.id))
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _enforce(
    session: Session,
    ctx: AuditContext,
    quota: SubmissionQuota,
    kind: SubmissionKind = SubmissionKind.NEW_TEST,
) -> QuotaRefusal | None:
    return enforce_submission_quota(session, ctx, quota=quota, kind=kind)


# --- the lifetime limit (Provisional) ----------------------------------------------------------


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_provisional_user_with_room_is_not_refused(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 4)

    assert _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL]) is None
    assert _refusal_events(app_session, user) == []


@pytest.mark.req("FR-43")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_provisional_users_sixth_submission_is_refused_and_audited(
    app_session: Session,
) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 5)

    refusal = _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    assert refusal == QuotaRefusal(QuotaLimit.LIFETIME, 5, None)
    (event,) = _refusal_events(app_session, user)
    assert event.entity_type == "app_user"
    assert event.actor_user_id == user.id
    assert event.before is None
    assert event.after == {
        "limit": "lifetime",
        "maximum": 5,
        "count": 5,
        "submission_kind": "new_test",
    }


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_the_lifetime_limit_counts_old_submissions_however_long_ago(
    app_session: Session,
) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 5, age=timedelta(days=400))

    refusal = _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    assert refusal is not None
    assert refusal.limit is QuotaLimit.LIFETIME
    assert refusal.retry_after_seconds is None


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_new_test_and_an_amendment_share_one_counter(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 3, kind=SubmissionKind.NEW_TEST)
    _submit(app_session, user, 2, kind=SubmissionKind.AMENDMENT)

    new_test = _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL], SubmissionKind.NEW_TEST)
    amendment = _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL], SubmissionKind.AMENDMENT)

    assert new_test is not None
    assert amendment is not None
    kinds = [event.after["submission_kind"] for event in _refusal_events(app_session, user)]  # type: ignore[index]
    assert kinds == ["new_test", "amendment"]


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_another_users_submissions_do_not_count(app_session: Session) -> None:
    user, ctx = _user(app_session)
    other, _ = _user(app_session)
    _submit(app_session, other, 5)
    _submit(app_session, user, 1)

    assert _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL]) is None


# --- the hourly limit (Member) -----------------------------------------------------------------


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_members_21st_submission_within_an_hour_is_refused_with_a_retry_time(
    app_session: Session,
) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 20, age=timedelta(minutes=10))

    refusal = _enforce(app_session, ctx, QUOTAS[Role.MEMBER])

    assert refusal == QuotaRefusal(QuotaLimit.HOURLY, 20, 50 * 60)
    (event,) = _refusal_events(app_session, user)
    assert event.after == {
        "limit": "hourly",
        "maximum": 20,
        "count": 20,
        "submission_kind": "new_test",
    }


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_member_with_nineteen_in_the_hour_is_not_refused(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 19, age=timedelta(minutes=10))

    assert _enforce(app_session, ctx, QUOTAS[Role.MEMBER]) is None


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_member_whose_submissions_are_over_an_hour_old_is_not_refused(
    app_session: Session,
) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 20, age=timedelta(hours=1, minutes=1))

    assert _enforce(app_session, ctx, QUOTAS[Role.MEMBER]) is None
    assert _refusal_events(app_session, user) == []


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_old_submissions_do_not_count_towards_the_hourly_limit(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 30, age=timedelta(hours=3))
    _submit(app_session, user, 19, age=timedelta(minutes=5))

    assert _enforce(app_session, ctx, QUOTAS[Role.MEMBER]) is None


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_retry_after_waits_for_the_limit_th_newest_submission_to_leave_the_window(
    app_session: Session,
) -> None:
    """Of 21 submissions in the window, the 20th newest decides: once it is an hour old, the
    count is 19. The 21st, the oldest, leaves the window sooner and does not decide."""
    user, ctx = _user(app_session)
    _submit(app_session, user, 18, age=timedelta(minutes=30))
    _submit(app_session, user, 1, age=timedelta(minutes=40))
    _submit(app_session, user, 1, age=timedelta(minutes=50))
    _submit(app_session, user, 1, age=timedelta(minutes=59))

    refusal = _enforce(app_session, ctx, QUOTAS[Role.MEMBER])

    assert refusal == QuotaRefusal(QuotaLimit.HOURLY, 20, 10 * 60)


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_retry_after_rounds_up_and_is_never_below_one_second(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 20, age=timedelta(hours=1) - timedelta(milliseconds=200))

    refusal = _enforce(app_session, ctx, QUOTAS[Role.MEMBER])

    assert refusal is not None
    assert refusal.retry_after_seconds == 1


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_quota_of_zero_an_hour_is_refused_with_no_retry_time(app_session: Session) -> None:
    _, ctx = _user(app_session)

    refusal = _enforce(app_session, ctx, SubmissionQuota(lifetime_max=None, per_hour_max=0))

    assert refusal == QuotaRefusal(QuotaLimit.HOURLY, 0, None)


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_both_limits_apply_and_the_lifetime_limit_is_named_first(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 5, age=timedelta(minutes=1))

    refusal = _enforce(app_session, ctx, SubmissionQuota(lifetime_max=5, per_hour_max=3))

    assert refusal == QuotaRefusal(QuotaLimit.LIFETIME, 5, None)


# --- roles with no limit, and the roles that hold none -----------------------------------------


@pytest.mark.req("FR-43")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.REVIEWER, Role.ADMINISTRATOR], ids=lambda r: r.value)
def test_reviewer_and_administrator_are_never_refused(app_session: Session, role: Role) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 40, age=timedelta(minutes=1))

    assert _enforce(app_session, ctx, QUOTAS[role]) is None
    assert _refusal_events(app_session, user) == []


@pytest.mark.req("FR-43")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_member_who_is_also_a_reviewer_is_not_limited(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 25, age=timedelta(minutes=1))
    quota = effective_quota(frozenset({Role.MEMBER, Role.REVIEWER}))

    assert _enforce(app_session, ctx, quota) is None


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_an_observer_quota_refuses_a_user_with_no_submissions(app_session: Session) -> None:
    _, ctx = _user(app_session)

    refusal = _enforce(app_session, ctx, QUOTAS[Role.OBSERVER])

    assert refusal == QuotaRefusal(QuotaLimit.LIFETIME, 0, None)


# --- the audit event and the caller ------------------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.req("NFR-35")
@pytest.mark.integration
def test_the_refusal_event_holds_only_counts_and_names(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 5)

    _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    (event,) = _refusal_events(app_session, user)
    assert event.after is not None
    assert set(event.after) == {"limit", "maximum", "count", "submission_kind"}


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_refusal_adds_no_submission(app_session: Session) -> None:
    user, ctx = _user(app_session)
    _submit(app_session, user, 5)

    _enforce(app_session, ctx, QUOTAS[Role.PROVISIONAL])

    count = app_session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == user.id)
    ).scalar_one()
    assert count == 5


@pytest.mark.req("FR-43")
@pytest.mark.integration
def test_a_context_with_no_human_user_is_a_programming_error(app_session: Session) -> None:
    with pytest.raises(ValueError, match="human submitter"):
        _enforce(app_session, AuditContext.system(), QUOTAS[Role.PROVISIONAL])
