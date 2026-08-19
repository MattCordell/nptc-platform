"""Granting and revoking roles (issue #44, FR-44, FR-01) - the one module
in this issue's set that takes a `Session`.

`permissions.py` is pure data, `principal.py` reads it, `authorisation.py`
checks it - this module is where a `user_role` row is actually written or
removed, always inside the caller's own transaction and always emitting an
audit event via `nptc.audit.recording.record_change` (never a bare
`session.add`/`session.delete`, which would silently skip NFR-08).

**Lock ordering, stated once so it is easy to find**: `nptc.audit.writer`
takes a fixed-key `pg_advisory_xact_lock` on every append
(`AUDIT_APPEND_LOCK_KEY`), and every function below ends with such an
append. So the order here - and in `nptc.auth.identity.close_account`,
which also revokes - is always *lock the `user_role` rows first, then let
the audit append take its own lock*. Reversing that order anywhere would
deadlock against a concurrent caller doing the same in the opposite
sequence.

`Principal` is imported only under `TYPE_CHECKING`: `nptc.auth.identity`
calls this module's *unchecked* functions (the bootstrap and
default-registration grant, and closure's revoke-all) without ever
constructing a `Principal`, and `nptc.auth.principal` itself imports from
`nptc.auth.identity` (`LinkOutcome`/`Resolution`/`UserRef`) - a real,
module-level import of `Principal` here would be a genuine import cycle.
`from __future__ import annotations` makes every annotation below a lazy
string, so this costs nothing at runtime; mypy still resolves the name
via the `TYPE_CHECKING` block.
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

#: Locks every `user_role` row naming the Administrator role for an
#: *active* user, inside the caller's transaction, so a concurrent
#: revoker/closer blocks rather than racing a `SELECT count(*)` snapshot -
#: see `assert_not_last_administrator`'s docstring for why a plain count
#: is unsafe here. `FOR UPDATE OF ur` locks only the `user_role` rows,
#: not the joined `app_user` rows.
_LOCK_ADMINISTRATOR_GRANTS_SQL = text(
    "SELECT ur.id FROM user_role ur JOIN app_user u ON u.id = ur.user_id "
    "WHERE ur.role = 'administrator' AND u.status = 'active' FOR UPDATE OF ur"
)


def roles_for_user(session: Session, user_id: uuid.UUID) -> frozenset[Role]:
    rows = session.execute(select(UserRole.role).where(UserRole.user_id == user_id)).scalars().all()
    return frozenset(Role(value) for value in rows)


def assert_not_last_administrator(session: Session, *, removing_user_id: uuid.UUID) -> None:
    """Raises `LastAdministratorError` if removing `removing_user_id`'s
    Administrator grant (by revocation, suspension, or account closure)
    would leave zero active Administrators.

    **Why a row lock, not `SELECT count(*)`.** Two concurrent callers each
    revoking one of the last two Administrators would, under a plain
    count, each independently see "one other remains" and both commit,
    leaving zero - the exact FR-01 violation this function exists to
    prevent. `FOR UPDATE OF ur` locks every qualifying `user_role` row
    (including the one about to be removed) inside this transaction; a
    second concurrent caller blocks on this same `SELECT` until the first
    commits or rolls back, and then re-evaluates against the reduced
    count. Concurrent grants (adding an Administrator) are safe by
    construction - they only ever increase the count.

    **Why an application check, not a database constraint or trigger.**
    Postgres has no per-row mechanism ("at least one row across the whole
    table satisfies X") - not a `CHECK`, not a `UNIQUE`, not an `EXCLUDE`
    constraint - and PRD Section 14.1 / ADR-0011 forbid business logic in
    triggers or stored functions precisely because both are invisible to
    tests and code review. A materialised counter row enforced by a
    trigger is the same problem in a different shape.

    Only counts grants held by `status = 'active'` users: a suspended
    Administrator's grant does not keep the floor satisfied (suspending
    the last Administrator must itself be refused), and this is also why
    `nptc.auth.identity.close_account` must call this *before* tombstoning
    - closure never calls `revoke_role`, so without this check here,
    closing your own account would be the trivial bypass of FR-01.
    """
    locked_ids = session.execute(_LOCK_ADMINISTRATOR_GRANTS_SQL).scalars().all()
    if not locked_ids:
        # removing_user_id holds no active administrator grant at all
        # (or none exists) - nothing for this guard to refuse.
        return
    holder_ids = set(
        session.execute(select(UserRole.user_id).where(UserRole.id.in_(locked_ids))).scalars().all()
    )
    if removing_user_id in holder_ids and holder_ids - {removing_user_id} == set():
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
    Provisional user to Member and no more") explicitly rather than via a
    single permission: a granter needs `Permission.ROLE_GRANT_ANY` unless
    `role` is exactly `Role.MEMBER` **and** the target currently holds
    `Role.PROVISIONAL`, in which case `Permission.ROLE_GRANT_MEMBER`
    suffices. This is what stops a Reviewer laterally promoting an
    Observer to Member (not a Provisional-to-Member promotion at all) and
    stops `ROLE_GRANT_MEMBER` from ever being read as "may grant Member to
    anyone".

    Idempotent: granting a role already held is a no-op (no audit event -
    nothing changed) rather than a unique-constraint error, since a
    concurrent double-submission of the same grant is not itself a bug
    worth surfacing.
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
    `Permission.ROLE_GRANT_ANY` unconditionally - PRD Section 4.5
    withholds every revocation power from Reviewer, including revoking the
    one role (Member) it may grant.

    FR-01's guard runs *before* the row is touched: revoking
    `Role.ADMINISTRATOR` from the last active holder raises
    `LastAdministratorError` and leaves the grant in place. See the lock
    ordering note in this module's docstring for why the guard's row lock
    must be acquired before the eventual audit append's advisory lock.

    A no-op (no audit event) if the role was never held - mirrors
    `grant_role`'s idempotence.
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
    """The bootstrap/default-grant path: no `Principal`, no permission
    check. Two, and only two, callers exist for this: `scripts/
    grant_role.py` (a human operator with out-of-band, cluster-level
    database access - there is no `Principal` to check a permission
    against before any Administrator exists) and `nptc.auth.identity.
    _create_user` (a brand-new Provisional grant on first login, which
    the PRD makes automatic, not a decision any existing user makes).

    Still idempotent, and still audited via `record_change` - the only
    thing skipped is the permission check, never NFR-08.
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
    """Used only by `nptc.auth.identity.close_account`: closure is itself
    an FR-01 removal path even though it never calls `revoke_role`, so the
    caller is responsible for running `assert_not_last_administrator`
    first (see that function's docstring) - this function only performs
    the removal and its audit trail, one row at a time so `record_change`
    can read each row's attribute history before it disappears, exactly as
    `close_account` already does for `user_identity` rows."""
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
