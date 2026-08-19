"""The authorisation check API (issue #44, FR-44, NFR-20, FR-80, FR-81).

Every check here inspects a `nptc.auth.principal.Principal`'s
`permissions`/`roles` - never a role-name string comparison
(`backend/tests/test_authorisation_guard.py` enforces this mechanically).
`require_permission` is a plain callable, not a FastAPI dependency: this
issue is deliberately pure-library (see docs/adr/0016's "Scope" and
ADR-0019), so nothing here imports `fastapi`. The one-line adapter a
future #41 router needs is documented in `docs/architecture/permissions.md`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import (
    MFA_REQUIRED_PERMISSIONS,
    Permission,
    SubmissionQuota,
    effective_quota,
)
from nptc.auth.principal import Principal

#: The shape `require_permission` returns - a plain callable so it
#: composes as a FastAPI dependency (`Depends(permission_dep(...))`) once
#: an app exists, without this module ever importing `Depends` itself.
PermissionCheck = Callable[[Principal], Principal]


def has_permission(principal: Principal, permission: Permission) -> bool:
    return principal.has(permission)


def require_permission(permission: Permission) -> PermissionCheck:
    """Returns `check(principal) -> principal`, raising `MfaRequiredError`
    or `PermissionDeniedError` otherwise.

    Checks `MFA_REQUIRED_PERMISSIONS` first so the denial is actionable:
    when the principal holds a role that *would* grant `permission` but it
    was suppressed for want of MFA (`principal.mfa_suppressed_roles`), the
    caller gets `MfaRequiredError`, not a bare "not permitted" - see
    `nptc.auth.principal.principal_for`'s structural suppression.
    """

    def check(principal: Principal) -> Principal:
        if principal.has(permission):
            return principal
        if permission in MFA_REQUIRED_PERMISSIONS and principal.mfa_suppressed_roles:
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
    resource) resolves to `any_` only - there is no path where a null
    owner matches a null `principal.user_id` (an anonymous principal can
    never hold `own` or `any_` in the first place, since neither is in
    `ROLE_PERMISSIONS[Role.ANON]`).

    Deliberately a comparison of internal `app_user.id` values, never
    `username` - the NFR-04 boundary type `UserRef` carries no id at all.
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
    4.3/4.4's `max 5` / `20/hr`). **Not enforced here** - there is no
    `submission` table yet to count against; exceeding the returned quota
    is a 429, a distinct refusal from everything else in this module, with
    its own audit story ("rate limited" rather than "not permitted"). The
    submissions issue owns the count and the 429 response; this is only
    the resolution rule."""
    return effective_quota(principal.roles, override=override)
