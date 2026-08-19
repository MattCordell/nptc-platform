"""Deriving a `Principal` - the resolved actor and their effective
permissions - from #43's `Resolution` and the verified claims that
produced it (issue #44, NFR-20).

`Principal` is the one object a check site (`nptc.auth.authorisation`)
ever inspects. It deliberately does **not** carry the mapped `User`
instance:

- a `Session`-bound ORM object ties the `Principal`'s lifetime to that
  session, and a `Principal` is exactly the kind of object worth keeping,
  logging and passing around after the session that produced it has moved
  on or closed - a detached/expired attribute access would raise;
- `nptc.auth.identity.UserRef` is this codebase's existing structural
  NFR-04 serialisation boundary ("routing through this structurally
  instead of relying on reviewer memory that the internal UUID must never
  leak") - reusing it here keeps that guarantee in one place;
- a frozen dataclass wrapping a live ORM instance has surprising
  `__eq__`/`__repr__` semantics.

`user_id` is kept (not folded into `user_ref`) because ownership checks
(`nptc.auth.authorisation.may_act_on`) and `nptc.audit.writer.AuditContext.
actor_user_id` both need the internal UUID, and both already treat it as
an internal-only value never serialised to a response.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.auth.claims import OidcIdentityClaims
from nptc.auth.errors_authorisation import AccountClosedError, ManualLinkRequiredError
from nptc.auth.identity import LinkOutcome, Resolution, UserRef
from nptc.auth.permissions import ROLE_PERMISSIONS, Permission, Role, permissions_for_roles
from nptc.db.models.user import UserStatus
from nptc.db.models.user_role import UserRole

#: PRD Section 4.1's read surface - what a suspended account keeps and
#: what ANONYMOUS is built from below. Named once so both mean the same
#: thing in exactly one place.
_ANON_PERMISSIONS: Final[frozenset[Permission]] = ROLE_PERMISSIONS[Role.ANON]


@dataclass(frozen=True)
class Principal:
    #: `None` only for `ANONYMOUS` - every authenticated principal
    #: (including a suspended one) has a real `app_user.id`.
    user_id: uuid.UUID | None
    user_ref: UserRef | None
    status: UserStatus | None
    roles: frozenset[Role]
    permissions: frozenset[Permission]
    #: Derived inside `principal_for` from `claims.acr` - never a
    #: constructor argument a call site could hand-set `True`. See NFR-06.
    mfa_satisfied: bool
    #: Roles this principal genuinely holds (per `user_role`) but which are
    #: suppressed from `roles`/`permissions` above because `mfa_satisfied`
    #: is `False` - see `require_permission`'s `MfaRequiredError` path,
    #: which uses this to tell "not permitted" apart from "permitted, but
    #: needs step-up".
    mfa_suppressed_roles: frozenset[Role]

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions


#: The unauthenticated visitor - PRD Section 4.1. Constructed once: it
#: carries no per-request state (no user, no MFA claim to derive from).
ANONYMOUS: Final[Principal] = Principal(
    user_id=None,
    user_ref=None,
    status=None,
    roles=frozenset(),
    permissions=permissions_for_roles(frozenset()),
    mfa_satisfied=False,
    mfa_suppressed_roles=frozenset(),
)


def _roles_for_user(session: Session, user_id: uuid.UUID) -> frozenset[Role]:
    rows = session.execute(select(UserRole.role).where(UserRole.user_id == user_id)).scalars().all()
    return frozenset(Role(value) for value in rows)


def principal_for(
    session: Session,
    resolution: Resolution,
    *,
    claims: OidcIdentityClaims,
    mfa_acr_values: frozenset[str],
) -> Principal:
    """Turns #43's `Resolution` into the `Principal` an authorisation check
    inspects.

    `MANUAL_LINK_REQUIRED` (`resolution.user is None`) never degrades to
    `ANONYMOUS` - that would make "a valid token that cannot be linked"
    indistinguishable from "no token at all" in any log built from the
    result, and would let a conflicted identity browse under a shape
    nobody designed. It raises instead, so the caller (a future #41
    dependency) must decide explicitly what a 409 looks like to its
    client.
    """
    if resolution.outcome is LinkOutcome.MANUAL_LINK_REQUIRED or resolution.user is None:
        raise ManualLinkRequiredError(
            "token resolved to more than one candidate account, or to an "
            "untrusted auto-link candidate - a human must resolve this "
            "before a Principal can be derived"
        )

    user = resolution.user
    status = UserStatus(user.status)
    if status is UserStatus.CLOSED:
        # Practically unreachable - closure deletes every user_identity
        # row, so a closed user should never again resolve to EXISTING -
        # but this is the fail-closed backstop if that invariant is ever
        # violated elsewhere.
        raise AccountClosedError(f"user {user.id} is closed and may not act")

    # NFR-06: derived here, never accepted as an argument, so no call site
    # can hand-construct a Principal with mfa_satisfied=True. `acr` is an
    # assertion about *how* the user authenticated (an authentication
    # fact), not an authorisation claim - reading it here, from the
    # already-verified `claims`, is exactly what NFR-07's discipline
    # requires; nowhere else re-parses the token for it (see
    # test_token_verification_guard.py's rule 5).
    mfa_satisfied = claims.acr is not None and claims.acr in mfa_acr_values

    if status is UserStatus.SUSPENDED:
        # A write-abuse control, not a data-visibility one: a suspended
        # user keeps exactly the public read surface an anonymous visitor
        # has, and loses the Observer-only rows (pending submissions,
        # interest counts). Refusing every request outright, including
        # public GETs any stranger may make, would be both surprising and
        # a small information leak (it would tell an observer "this
        # account is suspended" via a different error shape).
        return Principal(
            user_id=user.id,
            user_ref=UserRef.from_user(user),
            status=status,
            roles=frozenset(),
            permissions=_ANON_PERMISSIONS,
            mfa_satisfied=mfa_satisfied,
            mfa_suppressed_roles=frozenset(),
        )

    granted_roles = _roles_for_user(session, user.id)
    if mfa_satisfied:
        effective_roles = granted_roles
        suppressed: frozenset[Role] = frozenset()
    else:
        # Structural enforcement of NFR-06: Administrator simply does not
        # exist in the effective role set for this request when MFA is
        # unsatisfied, so a check site that forgot about MFA entirely
        # still cannot be granted an admin-only permission.
        effective_roles = granted_roles - {Role.ADMINISTRATOR}
        suppressed = granted_roles & {Role.ADMINISTRATOR}

    return Principal(
        user_id=user.id,
        user_ref=UserRef.from_user(user),
        status=status,
        roles=effective_roles,
        permissions=permissions_for_roles(effective_roles),
        mfa_satisfied=mfa_satisfied,
        mfa_suppressed_roles=suppressed,
    )
