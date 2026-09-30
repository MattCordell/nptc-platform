"""The authorisation check API (FR-44, NFR-20, FR-80, FR-81).

Every check here inspects a `nptc.auth.principal.Principal`'s
`permissions`/`roles`, never a role-name string comparison
(`backend/tests/test_authorisation_guard.py` enforces this mechanically).
`require_permission` is a plain callable, not a FastAPI dependency, so nothing
in `nptc.auth` imports `fastapi`; `nptc.api.dependencies.permission_dep` adapts
it (ADR-0019, `docs/architecture/permissions.md`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import (
    Permission,
    SubmissionQuota,
    effective_quota,
    permissions_for_roles,
)
from nptc.auth.principal import Principal

PermissionCheck = Callable[[Principal], Principal]


def has_permission(principal: Principal, permission: Permission) -> bool:
    return principal.has(permission)


def require_permission(permission: Permission) -> PermissionCheck:
    """Returns `check(principal) -> principal`, raising `MfaRequiredError`
    or `PermissionDeniedError` otherwise.

    `MfaRequiredError` is raised when a role suppressed for want of MFA
    (`principal.mfa_suppressed_roles`) would have granted `permission`, so the
    denial is actionable (see `nptc.auth.principal.principal_for`).

    The test is membership in
    `permissions_for_roles(principal.mfa_suppressed_roles)`, not the coarser
    "is `permission` MFA-required, and is *some* role suppressed". The two
    coincide today, because only `ADMINISTRATOR` is suppressed and every
    MFA-required permission is Administrator-only. The coarser test would
    mislabel a plain denial as `MfaRequiredError` the moment either fact
    changes.
    """

    def check(principal: Principal) -> Principal:
        if principal.has(permission):
            return principal
        if permission in permissions_for_roles(principal.mfa_suppressed_roles):
            raise MfaRequiredError(
                f"permission {permission.value!r} requires step-up authentication"
            )
        raise PermissionDeniedError(f"permission {permission.value!r} is required")

    return check


def may_act_on(
    principal: Principal,
    *,
    own: Permission,
    any_: Permission,
    owner_user_id: uuid.UUID | None,
) -> bool:
    """Resolves a `Y (own)` / `Y (any)` matrix cell (e.g. withdrawing a
    submission). `owner_user_id=None` (an orphaned or system-authored
    resource) resolves to `any_` only: an anonymous principal holds neither
    `own` nor `any_`, so a null owner never matches a null
    `principal.user_id`.

    Compares internal `app_user.id` values, never `username`, because the
    NFR-04 boundary type `UserRef` carries no id.
    """
    if principal.has(any_):
        return True
    if not principal.has(own):
        return False
    return owner_user_id is not None and principal.user_id == owner_user_id


def require_ownership_or_permission(
    principal: Principal,
    *,
    own: Permission,
    any_: Permission,
    owner_user_id: uuid.UUID | None,
) -> Principal:
    if may_act_on(principal, own=own, any_=any_, owner_user_id=owner_user_id):
        return principal
    raise PermissionDeniedError(f"permission {own.value!r} or {any_.value!r} is required")


def resolve_quota(
    principal: Principal, *, override: SubmissionQuota | None = None
) -> SubmissionQuota:
    """The submission quota in effect for `principal` (PRD Section
    4.3/4.4's `max 5` / `20/hr`). **Not enforced here.** Exceeding the
    returned quota is a 429, a distinct refusal from everything else in this
    module, with its own audit story ("rate limited" rather than "not
    permitted"). Submissions own the count and the 429; this is only the
    resolution rule."""
    return effective_quota(principal.roles, override=override)
