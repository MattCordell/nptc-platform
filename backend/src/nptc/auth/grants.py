"""Granting and revoking roles (FR-44, FR-01): the one module in `nptc.auth`
that writes `user_role` rows.

Every write runs inside the caller's transaction and emits an audit event via
`nptc.audit.recording.record_change` (NFR-08), never a bare
`session.add`/`session.delete`, which would silently skip the audit.

**Lock ordering.** `nptc.audit.writer` takes a fixed-key
`pg_advisory_xact_lock` on every append (`AUDIT_APPEND_LOCK_KEY`), and every
function below ends with such an append. So the order here, and in
`nptc.auth.identity.close_account`, is always: lock the `user_role` rows first,
then let the audit append take its own lock. Reversing it anywhere would
deadlock against a concurrent caller doing the opposite (ADR-0019).

`Principal` is imported only under `TYPE_CHECKING`: `nptc.auth.principal`
imports `nptc.auth.identity`, which imports this module, so a module-level
import would be a cycle.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import LastAdministratorError, PermissionDeniedError
from nptc.auth.permissions import GRANTABLE_ROLES, Permission, Role
from nptc.db.models.user_role import UserRole

if TYPE_CHECKING:
    from nptc.auth.principal import Principal

#: Locks every `user_role` row naming Administrator for an *active* user, so a
#: concurrent revoker or closer blocks instead of racing a `SELECT count(*)`
#: snapshot (see `assert_not_last_administrator`). `FOR UPDATE OF ur` locks
#: only the `user_role` rows, not the joined `app_user` rows. It selects
#: `ur.user_id` so the caller needs no second query to recover the holder.
_LOCK_ADMINISTRATOR_GRANTS_SQL = text(
    "SELECT ur.id, ur.user_id FROM user_role ur JOIN app_user u ON u.id = ur.user_id "
    "WHERE ur.role = 'administrator' AND u.status = 'active' FOR UPDATE OF ur"
)


def roles_for_user(session: Session, user_id: uuid.UUID) -> frozenset[Role]:
    # `Role(value)` raises ValueError for a row outside GRANTABLE_ROLES. That is
    # unreachable while user_role's `role` CHECK constraint and GRANTABLE_ROLES
    # agree (test_permissions_data.py asserts they do), but a divergence would
    # surface as an unhandled 500 on every request for the affected user.
    rows = session.execute(select(UserRole.role).where(UserRole.user_id == user_id)).scalars().all()
    return frozenset(Role(value) for value in rows)


def assert_not_last_administrator(session: Session, *, removing_user_id: uuid.UUID) -> None:
    """Raises `LastAdministratorError` if removing `removing_user_id`'s
    Administrator grant (by revocation, suspension, or account closure)
    would leave zero active Administrators (FR-01).

    The guard takes a row lock instead of `SELECT count(*)`, and is an
    application check instead of a constraint or trigger; ADR-0019 ("FR-01's
    last-administrator guard") records why. Concurrent grants are safe,
    because they only increase the count.

    Only grants held by `status = 'active'` users count, so suspending the
    last Administrator must itself be refused. For the same reason
    `nptc.auth.identity.close_account` must call this *before* tombstoning:
    closure never calls `revoke_role`, so without this call, closing your own
    account would bypass FR-01.

    **TODO (suspend path, not yet written): it MUST call this function before
    setting `app_user.status` to `'suspended'`.** No suspend endpoint exists
    and `UserStatus.SUSPENDED` is only read today
    (`nptc.auth.principal.principal_for`). `FOR UPDATE OF ur` locks only
    `user_role` rows, not the joined `app_user` row, so a suspend that writes
    `app_user.status` without this lock races a concurrent `revoke_role` or
    `close_account` on a *different* administrator: neither transaction's lock
    is visible to the other's `u.status = 'active'` join until commit.
    """
    holder_ids = {
        row["user_id"] for row in session.execute(_LOCK_ADMINISTRATOR_GRANTS_SQL).mappings()
    }
    if holder_ids == {removing_user_id}:
        raise LastAdministratorError(
            "refusing to remove the last active administrator's role grant"
        )


def grant_role(
    session: Session,
    *,
    granter: Principal,
    target_user_id: uuid.UUID,
    role: Role,
    audit: AuditContext,
) -> UserRole:
    """Grants `role` to `target_user_id`.

    Enforces the Reviewer carve-out (PRD Section 4.5: "Promote a
    Provisional user to Member and no more") explicitly: a granter needs
    `Permission.ROLE_GRANT_ANY` unless `role` is exactly `Role.MEMBER`
    **and** the target currently holds `Role.PROVISIONAL`, in which case
    `Permission.ROLE_GRANT_MEMBER` suffices. This stops a Reviewer promoting
    an Observer to Member, and stops `ROLE_GRANT_MEMBER` being read as "may
    grant Member to anyone".

    Idempotent: granting a role already held is a no-op with no audit event
    (nothing changed), not a unique-constraint error, because a concurrent
    double submission of the same grant is not a bug worth surfacing.
    """
    if role not in GRANTABLE_ROLES:
        raise ValueError(f"{role!r} is not a grantable role")

    if role is Role.MEMBER and Role.PROVISIONAL in roles_for_user(session, target_user_id):
        required: Permission = Permission.ROLE_GRANT_MEMBER
    else:
        required = Permission.ROLE_GRANT_ANY
    if not granter.has(required):
        raise PermissionDeniedError(f"permission {required.value!r} is required")

    existing = session.execute(
        select(UserRole).where(UserRole.user_id == target_user_id, UserRole.role == role.value)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    grant = UserRole(
        user_id=target_user_id,
        role=role.value,
        granted_by_user_id=granter.user_id,
    )
    session.add(grant)
    record_change(
        session,
        audit,
        action="user_role.granted",
        instance=grant,
        kind=ChangeKind.CREATED,
    )
    return grant


def revoke_role(
    session: Session,
    *,
    revoker: Principal,
    target_user_id: uuid.UUID,
    role: Role,
    audit: AuditContext,
) -> None:
    """Revokes `role` from `target_user_id`. Requires
    `Permission.ROLE_GRANT_ANY` unconditionally: PRD Section 4.5 withholds
    every revocation power from Reviewer, including revoking Member, the one
    role it may grant.

    FR-01's guard runs *before* the row is touched: revoking
    `Role.ADMINISTRATOR` from the last active holder raises
    `LastAdministratorError` and leaves the grant in place. The guard's row
    lock must come before the audit append's advisory lock (see the lock
    ordering in this module's docstring).

    A no-op (no audit event) if the role was never held, mirroring
    `grant_role`.
    """
    if not revoker.has(Permission.ROLE_GRANT_ANY):
        raise PermissionDeniedError(f"permission {Permission.ROLE_GRANT_ANY.value!r} is required")

    if role is Role.ADMINISTRATOR:
        assert_not_last_administrator(session, removing_user_id=target_user_id)

    grant = session.execute(
        select(UserRole).where(UserRole.user_id == target_user_id, UserRole.role == role.value)
    ).scalar_one_or_none()
    if grant is None:
        return

    record_change(
        session,
        audit,
        action="user_role.revoked",
        instance=grant,
        kind=ChangeKind.DELETED,
    )
    session.delete(grant)


def grant_role_unchecked(
    session: Session,
    *,
    target_user_id: uuid.UUID,
    role: Role,
    granted_by_user_id: uuid.UUID | None,
    audit: AuditContext,
) -> UserRole:
    """The bootstrap and default-grant path: no `Principal`, no permission
    check. Two callers only: `scripts/grant_role.py` (an operator with
    out-of-band database access, because no `Principal` can hold a
    permission before any Administrator exists) and
    `nptc.auth.identity._create_user` (the automatic Provisional grant on
    first login, which no existing user decides).

    Still idempotent and still audited via `record_change`: only the
    permission check is skipped, never NFR-08.
    """
    if role not in GRANTABLE_ROLES:
        raise ValueError(f"{role!r} is not a grantable role")

    existing = session.execute(
        select(UserRole).where(UserRole.user_id == target_user_id, UserRole.role == role.value)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    grant = UserRole(
        user_id=target_user_id,
        role=role.value,
        granted_by_user_id=granted_by_user_id,
    )
    session.add(grant)
    record_change(
        session,
        audit,
        action="user_role.granted",
        instance=grant,
        kind=ChangeKind.CREATED,
    )
    return grant


def revoke_all_roles_unchecked(
    session: Session, *, target_user_id: uuid.UUID, audit: AuditContext
) -> None:
    """Used only by `nptc.auth.identity.close_account`. Closure is an FR-01
    removal path even though it never calls `revoke_role`, so the caller must
    run `assert_not_last_administrator` first. This function only removes the
    grants and audits them, one row at a time so `record_change` can read each
    row's attribute history before it disappears."""
    grants = (
        session.execute(select(UserRole).where(UserRole.user_id == target_user_id)).scalars().all()
    )
    for grant in grants:
        record_change(
            session,
            audit,
            action="user_role.revoked",
            instance=grant,
            kind=ChangeKind.DELETED,
        )
        session.delete(grant)
