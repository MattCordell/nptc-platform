"""`nptc.auth.grants` (issue #44, FR-44, FR-01): granting/revoking roles,
the Reviewer promote-to-Member carve-out, idempotence, the audit trail
each mutation emits, and the last-administrator guard's principal failure
mode - two genuinely concurrent transactions racing to revoke the last two
administrators. Real Postgres via testcontainers throughout.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import LastAdministratorError, PermissionDeniedError
from nptc.auth.grants import grant_role, grant_role_unchecked, revoke_role, roles_for_user
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.db.models.user import User
from nptc.db.models.user_role import UserRole


def _principal(*, user_id: uuid.UUID, roles: frozenset[Role]) -> Principal:
    return Principal(
        user_id=user_id,
        user_ref=None,
        status=None,
        roles=roles,
        permissions=permissions_for_roles(roles),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


def _create_user(session: Session, username: str) -> User:
    user = User(username=username, display_name=username)
    session.add(user)
    session.flush()
    return user


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_administrator_can_grant_any_role(app_db: Connection) -> None:
    session = Session(bind=app_db)
    admin = _create_user(session, "grants-admin-1")
    target = _create_user(session, "grants-target-1")
    admin_principal = _principal(user_id=admin.id, roles=frozenset({Role.ADMINISTRATOR}))

    grant_role(
        session,
        granter=admin_principal,
        target_user_id=target.id,
        role=Role.REVIEWER,
        audit=AuditContext.system(),
    )
    session.flush()

    assert roles_for_user(session, target.id) == frozenset({Role.REVIEWER})


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_reviewer_can_promote_provisional_to_member(app_db: Connection) -> None:
    session = Session(bind=app_db)
    reviewer = _create_user(session, "grants-reviewer-1")
    target = _create_user(session, "grants-target-2")
    grant_role_unchecked(
        session,
        target_user_id=target.id,
        role=Role.PROVISIONAL,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()
    reviewer_principal = _principal(user_id=reviewer.id, roles=frozenset({Role.REVIEWER}))

    grant_role(
        session,
        granter=reviewer_principal,
        target_user_id=target.id,
        role=Role.MEMBER,
        audit=AuditContext.system(),
    )
    session.flush()

    assert Role.MEMBER in roles_for_user(session, target.id)


@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_reviewer_cannot_promote_an_observer_to_member(app_db: Connection) -> None:
    """PRD Section 4.5: 'Promote a Provisional user to Member and no
    more' - a Reviewer must not be able to laterally promote an Observer
    (who never held Provisional) to Member via the same carve-out."""
    session = Session(bind=app_db)
    reviewer = _create_user(session, "grants-reviewer-2")
    target = _create_user(session, "grants-target-3")
    grant_role_unchecked(
        session,
        target_user_id=target.id,
        role=Role.OBSERVER,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()
    reviewer_principal = _principal(user_id=reviewer.id, roles=frozenset({Role.REVIEWER}))

    with pytest.raises(PermissionDeniedError):
        grant_role(
            session,
            granter=reviewer_principal,
            target_user_id=target.id,
            role=Role.MEMBER,
            audit=AuditContext.system(),
        )


@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_reviewer_cannot_grant_reviewer(app_db: Connection) -> None:
    session = Session(bind=app_db)
    reviewer = _create_user(session, "grants-reviewer-3")
    target = _create_user(session, "grants-target-4")
    reviewer_principal = _principal(user_id=reviewer.id, roles=frozenset({Role.REVIEWER}))

    with pytest.raises(PermissionDeniedError):
        grant_role(
            session,
            granter=reviewer_principal,
            target_user_id=target.id,
            role=Role.REVIEWER,
            audit=AuditContext.system(),
        )


@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_reviewer_cannot_revoke_any_role(app_db: Connection) -> None:
    """PRD Section 4.5 withholds every revocation power from Reviewer,
    including revoking the one role (Member) it may grant."""
    session = Session(bind=app_db)
    reviewer = _create_user(session, "grants-reviewer-4")
    target = _create_user(session, "grants-target-5")
    grant_role_unchecked(
        session,
        target_user_id=target.id,
        role=Role.MEMBER,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()
    reviewer_principal = _principal(user_id=reviewer.id, roles=frozenset({Role.REVIEWER}))

    with pytest.raises(PermissionDeniedError):
        revoke_role(
            session,
            revoker=reviewer_principal,
            target_user_id=target.id,
            role=Role.MEMBER,
            audit=AuditContext.system(),
        )


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_grant_role_is_idempotent(app_db: Connection) -> None:
    session = Session(bind=app_db)
    admin = _create_user(session, "grants-admin-2")
    target = _create_user(session, "grants-target-6")
    admin_principal = _principal(user_id=admin.id, roles=frozenset({Role.ADMINISTRATOR}))

    grant_role(
        session,
        granter=admin_principal,
        target_user_id=target.id,
        role=Role.OBSERVER,
        audit=AuditContext.system(),
    )
    session.flush()
    grant_role(
        session,
        granter=admin_principal,
        target_user_id=target.id,
        role=Role.OBSERVER,
        audit=AuditContext.system(),
    )
    session.flush()

    rows = (
        session.execute(
            select(UserRole).where(
                UserRole.user_id == target.id, UserRole.role == Role.OBSERVER.value
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_revoke_role_is_idempotent_when_never_held(app_db: Connection) -> None:
    session = Session(bind=app_db)
    admin = _create_user(session, "grants-admin-3")
    target = _create_user(session, "grants-target-7")
    admin_principal = _principal(user_id=admin.id, roles=frozenset({Role.ADMINISTRATOR}))

    # No error for revoking a role never granted.
    revoke_role(
        session,
        revoker=admin_principal,
        target_user_id=target.id,
        role=Role.REVIEWER,
        audit=AuditContext.system(),
    )


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_grant_and_revoke_each_emit_an_audit_event(app_db: Connection) -> None:
    session = Session(bind=app_db)
    admin = _create_user(session, "grants-admin-4")
    target = _create_user(session, "grants-target-8")
    admin_principal = _principal(user_id=admin.id, roles=frozenset({Role.ADMINISTRATOR}))

    grant_role(
        session,
        granter=admin_principal,
        target_user_id=target.id,
        role=Role.OBSERVER,
        audit=AuditContext.system(),
    )
    session.flush()
    revoke_role(
        session,
        revoker=admin_principal,
        target_user_id=target.id,
        role=Role.OBSERVER,
        audit=AuditContext.system(),
    )
    session.flush()

    granted = session.execute(
        text("SELECT count(*) FROM audit_event WHERE action = 'user_role.granted'")
    ).scalar_one()
    revoked = session.execute(
        text("SELECT count(*) FROM audit_event WHERE action = 'user_role.revoked'")
    ).scalar_one()
    assert granted >= 1
    assert revoked >= 1


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_revoking_the_last_administrator_is_refused(
    pristine_audit_event: None, app_db: Connection
) -> None:
    """`pristine_audit_event`: `assert_not_last_administrator` counts
    Administrator grants across the *whole* `user_role` table (see its
    docstring), so this test's premise - the seeded admin is the only one
    that exists - has to be established explicitly, not inherited from
    whatever else has run in this worker/container (issue #190)."""
    session = Session(bind=app_db)
    admin = _create_user(session, "grants-lone-admin")
    grant_role_unchecked(
        session,
        target_user_id=admin.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    session.flush()
    admin_principal = _principal(user_id=admin.id, roles=frozenset({Role.ADMINISTRATOR}))

    with pytest.raises(LastAdministratorError):
        revoke_role(
            session,
            revoker=admin_principal,
            target_user_id=admin.id,
            role=Role.ADMINISTRATOR,
            audit=AuditContext.system(),
        )

    assert Role.ADMINISTRATOR in roles_for_user(session, admin.id)


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_revoking_one_of_two_administrators_succeeds(app_db: Connection) -> None:
    session = Session(bind=app_db)
    admin_a = _create_user(session, "grants-two-admin-a")
    admin_b = _create_user(session, "grants-two-admin-b")
    for user in (admin_a, admin_b):
        grant_role_unchecked(
            session,
            target_user_id=user.id,
            role=Role.ADMINISTRATOR,
            granted_by_user_id=None,
            audit=AuditContext.system(),
        )
    session.flush()
    admin_principal = _principal(user_id=admin_a.id, roles=frozenset({Role.ADMINISTRATOR}))

    revoke_role(
        session,
        revoker=admin_principal,
        target_user_id=admin_a.id,
        role=Role.ADMINISTRATOR,
        audit=AuditContext.system(),
    )
    session.flush()

    assert Role.ADMINISTRATOR not in roles_for_user(session, admin_a.id)
    assert Role.ADMINISTRATOR in roles_for_user(session, admin_b.id)


@pytest.mark.req("FR-01")
@pytest.mark.integration
def test_concurrent_revocation_of_the_last_two_administrators_leaves_exactly_one(
    pristine_audit_event: None, app_engine: Engine
) -> None:
    """The principal failure mode FR-01's guard exists to prevent: two
    concurrent revocations, each independently checking "is there another
    administrator", must not both succeed. `assert_not_last_administrator`'s
    `FOR UPDATE OF ur` row lock is what makes the second transaction
    re-evaluate against the *post-commit* state of the first, rather than
    a stale snapshot both read before either committed.

    `pristine_audit_event`: the guard counts *every* active Administrator
    in `user_role`, so `["ok", "refused"]` only holds if these two seeded
    admins are the only ones that exist - an explicit precondition rather
    than one inherited from suite ordering (issue #190). It also means a
    failed assertion below still leaves `audit_event`/`user_role`/
    `app_user` clean via the fixture's own teardown, without relying on
    this test's own cleanup running.
    """
    setup_session = Session(app_engine)
    user_a = User(username="race-admin-a", display_name="Race A")
    user_b = User(username="race-admin-b", display_name="Race B")
    setup_session.add_all([user_a, user_b])
    setup_session.flush()
    setup_session.add_all(
        [
            UserRole(user_id=user_a.id, role=Role.ADMINISTRATOR.value),
            UserRole(user_id=user_b.id, role=Role.ADMINISTRATOR.value),
        ]
    )
    setup_session.commit()
    user_a_id, user_b_id = user_a.id, user_b.id
    setup_session.close()

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def _revoke(target_user_id: uuid.UUID, key: str) -> None:
        session = Session(app_engine)
        try:
            barrier.wait(timeout=5)
            revoker = _principal(user_id=target_user_id, roles=frozenset({Role.ADMINISTRATOR}))
            revoke_role(
                session,
                revoker=revoker,
                target_user_id=target_user_id,
                role=Role.ADMINISTRATOR,
                audit=AuditContext.system(),
            )
            session.commit()
            results[key] = "ok"
        except LastAdministratorError:
            session.rollback()
            results[key] = "refused"
        finally:
            session.close()

    thread_a = threading.Thread(target=_revoke, args=(user_a_id, "a"))
    thread_b = threading.Thread(target=_revoke, args=(user_b_id, "b"))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    # No manual cleanup needed here: `pristine_audit_event`'s teardown
    # wipes `user_role`/`audit_event`/`app_user` (as the owner role,
    # which nptc_app lacks DELETE on for app_user at all - NFR-17: users
    # are never deleted, only pseudonymised) regardless of whether this
    # assertion passes, so a failure here can no longer leak a committed
    # administrator row into later tests the way an inline, non-`finally`
    # cleanup could (issue #190).
    assert sorted(results.values()) == ["ok", "refused"]
