"""The PRD Section 4.7 permission matrix, as code (issue #44, FR-44).

**Permissions and role -> permission mappings are code here, not database
rows.** A permission constant referenced by a check site must exist as a
Python symbol, so a database-sourced permission table could only ever be a
*shadow* of this enum - one that can silently disagree with it, the exact
drift `nptc.db.roles` was built to prevent for privilege grants. "Who may
publish a release" belongs in `git blame` and PR review, not an unreviewed
`INSERT` - especially with no admin UI in this issue's scope. `mypy
--strict` typechecks a `Permission` reference at every call site; a
database-sourced string cannot be. Only *grants* (which user holds which
role) are database rows - see `nptc.auth.grants` and
`nptc.db.models.user_role`.

**`ROLE_PERMISSIONS` is written as an explicit literal `frozenset` per
role, never `MEMBER = PROVISIONAL | {...}`.** The matrix is not monotone -
a Provisional user may create submissions but may not register interest,
so a later, more-privileged role does not simply add to an earlier one.
Writing each role flat lets a reviewer diff this table against PRD Section
4.7 row by row; monotonicity (where it does hold) is asserted as a test
property in `test_permissions_data.py`, not baked into the representation
as an unexamined assumption.

**The three matrix qualifiers are three different kinds of thing, and
each gets its own mechanism** - collapsing them into one is the design
trap this module exists to avoid:

- ``Y (own)`` / ``Y (any)`` (withdraw a submission) is a *resource-scope*
  distinction: two permissions, ``SUBMISSION_WITHDRAW_OWN`` and
  ``SUBMISSION_WITHDRAW_ANY``, checked via
  ``nptc.auth.authorisation.may_act_on`` against the resource's owner. A
  single ``SUBMISSION_WITHDRAW`` permission plus an ownership ``if`` at
  the call site would be exactly the hard-coded authorisation check FR-44
  forbids, merely relocated.
- ``max 5`` / ``20/hr`` (submission creation) is not a permission at all -
  both Provisional and Member hold the same ``SUBMISSION_CREATE``
  permission. It is a numeric budget, carried by ``SubmissionQuota``/
  ``QUOTAS`` below. This module defines and unit-tests the data and its
  resolution rule; it does **not** enforce it - there is no ``submission``
  table yet to count against, exceeding a quota is a 429, not a 403, and
  it is a different audit story ("rate limited") from "not permitted".
- Reviewer's "promote Provisional to Member and no more" is the
  ``ROLE_GRANT_MEMBER`` / ``ROLE_GRANT_ANY`` split, resolved by
  `nptc.auth.grants.grant_role`.

Deliberately never attached to a permission set: a predicate or lambda
expressing one of the above. That would destroy the "permissions are
inspectable data" property `test_permission_matrix.py`, the FR-80/FR-81
property tests, and `test_authorisation_guard.py` all depend on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Role(StrEnum):
    """PRD Section 4, ordered by privilege. ``ANON`` is a matrix column
    (the anonymous visitor), never a grantable row - see `GRANTABLE_ROLES`.
    """

    ANON = "anon"
    OBSERVER = "observer"
    PROVISIONAL = "provisional"
    MEMBER = "member"
    REVIEWER = "reviewer"
    ADMINISTRATOR = "administrator"


#: Roles a `user_role` row may ever name - everything except ANON, which
#: is never granted (an ungranted user simply *is* anonymous).
GRANTABLE_ROLES: Final[frozenset[Role]] = frozenset(Role) - {Role.ANON}


class Permission(StrEnum):
    """One constant per PRD Section 4.7 capability (or capability group).
    Dotted, stable values - see the module docstring for why this is code,
    not data."""

    CATALOGUE_BROWSE = "catalogue.browse"
    RELEASE_RETRIEVE = "release.retrieve"
    SUBMISSION_VIEW = "submission.view"
    INTEREST_VIEW_COUNTS = "interest.view_counts"
    SUBMISSION_CREATE = "submission.create"
    AMENDMENT_PROPOSE = "amendment.propose"
    INTEREST_REGISTER = "interest.register"
    SUBMISSION_WITHDRAW_OWN = "submission.withdraw.own"
    SUBMISSION_WITHDRAW_ANY = "submission.withdraw.any"
    SUBMITTER_IDENTITY_VIEW = "submitter_identity.view"
    INTEREST_IDENTITIES_VIEW = "interest.identities.view"
    COMMENT_INTERNAL_READ = "comment.internal.read"
    COMMENT_INTERNAL_WRITE = "comment.internal.write"
    SUBMISSION_TRANSITION_REVIEW = "submission.transition.review"
    VALIDATION_RUN = "validation.run"
    VALIDATION_ACKNOWLEDGE = "validation.acknowledge"
    ROLE_GRANT_MEMBER = "role.grant.member"
    SUBMISSION_TRANSITION_APPROVE = "submission.transition.approve"
    CATALOGUE_EDIT_PUBLISHED = "catalogue.edit_published"
    RELEASE_PUBLISH = "release.publish"
    REGISTRY_READ = "registry.read"
    REGISTRY_MANAGE = "registry.manage"
    EXPORT_CONFIG_MANAGE = "export_config.manage"
    ROLE_GRANT_ANY = "role.grant.any"
    USER_SUSPEND = "user.suspend"
    USER_RATE_LIMIT_OVERRIDE = "user.rate_limit.override"
    AUDIT_READ = "audit.read"


class PermissionKind(StrEnum):
    """FR-80's own wording: Observer must have "no write capability of any
    kind", which is a statement about *data* (is a permission read-shaped
    or write-shaped), not about endpoints. Every `Permission` member must
    be classified - `test_permissions_data.py` asserts exhaustiveness, the
    same "every column classified or construction fails" discipline
    `nptc.audit.policy` already applies to model columns."""

    READ = "read"
    WRITE = "write"


PERMISSION_KIND: Final[Mapping[Permission, PermissionKind]] = {
    Permission.CATALOGUE_BROWSE: PermissionKind.READ,
    Permission.RELEASE_RETRIEVE: PermissionKind.READ,
    Permission.SUBMISSION_VIEW: PermissionKind.READ,
    Permission.INTEREST_VIEW_COUNTS: PermissionKind.READ,
    Permission.SUBMISSION_CREATE: PermissionKind.WRITE,
    Permission.AMENDMENT_PROPOSE: PermissionKind.WRITE,
    Permission.INTEREST_REGISTER: PermissionKind.WRITE,
    Permission.SUBMISSION_WITHDRAW_OWN: PermissionKind.WRITE,
    Permission.SUBMISSION_WITHDRAW_ANY: PermissionKind.WRITE,
    Permission.SUBMITTER_IDENTITY_VIEW: PermissionKind.READ,
    Permission.INTEREST_IDENTITIES_VIEW: PermissionKind.READ,
    Permission.COMMENT_INTERNAL_READ: PermissionKind.READ,
    Permission.COMMENT_INTERNAL_WRITE: PermissionKind.WRITE,
    Permission.SUBMISSION_TRANSITION_REVIEW: PermissionKind.WRITE,
    Permission.VALIDATION_RUN: PermissionKind.WRITE,
    Permission.VALIDATION_ACKNOWLEDGE: PermissionKind.WRITE,
    Permission.ROLE_GRANT_MEMBER: PermissionKind.WRITE,
    Permission.SUBMISSION_TRANSITION_APPROVE: PermissionKind.WRITE,
    Permission.CATALOGUE_EDIT_PUBLISHED: PermissionKind.WRITE,
    Permission.RELEASE_PUBLISH: PermissionKind.WRITE,
    Permission.REGISTRY_READ: PermissionKind.READ,
    Permission.REGISTRY_MANAGE: PermissionKind.WRITE,
    Permission.EXPORT_CONFIG_MANAGE: PermissionKind.WRITE,
    Permission.ROLE_GRANT_ANY: PermissionKind.WRITE,
    Permission.USER_SUSPEND: PermissionKind.WRITE,
    Permission.USER_RATE_LIMIT_OVERRIDE: PermissionKind.WRITE,
    Permission.AUDIT_READ: PermissionKind.READ,
}

#: Derived, never hand-listed twice - see `test_permissions_data.py`'s
#: exhaustiveness assertion against `Permission`.
WRITE_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    p for p, kind in PERMISSION_KIND.items() if kind is PermissionKind.WRITE
)

# PRD Section 4.7, reproduced cell by cell - each role an explicit literal
# frozenset (see module docstring for why). `test_permission_matrix.py`
# parses the PRD's own markdown table and asserts this mapping reproduces
# it exactly; that test, not this comment, is the source of truth for
# "matches the PRD".
ROLE_PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = {
    Role.ANON: frozenset(
        {
            Permission.CATALOGUE_BROWSE,
            Permission.RELEASE_RETRIEVE,
        }
    ),
    Role.OBSERVER: frozenset(
        {
            Permission.CATALOGUE_BROWSE,
            Permission.RELEASE_RETRIEVE,
            Permission.SUBMISSION_VIEW,
            Permission.INTEREST_VIEW_COUNTS,
        }
    ),
    Role.PROVISIONAL: frozenset(
        {
            Permission.CATALOGUE_BROWSE,
            Permission.RELEASE_RETRIEVE,
            Permission.SUBMISSION_VIEW,
            Permission.INTEREST_VIEW_COUNTS,
            Permission.SUBMISSION_CREATE,
            Permission.AMENDMENT_PROPOSE,
            Permission.REGISTRY_READ,
            Permission.SUBMISSION_WITHDRAW_OWN,
        }
    ),
    Role.MEMBER: frozenset(
        {
            Permission.CATALOGUE_BROWSE,
            Permission.RELEASE_RETRIEVE,
            Permission.SUBMISSION_VIEW,
            Permission.INTEREST_VIEW_COUNTS,
            Permission.SUBMISSION_CREATE,
            Permission.AMENDMENT_PROPOSE,
            Permission.INTEREST_REGISTER,
            Permission.SUBMISSION_WITHDRAW_OWN,
            Permission.REGISTRY_READ,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Permission.CATALOGUE_BROWSE,
            Permission.RELEASE_RETRIEVE,
            Permission.SUBMISSION_VIEW,
            Permission.INTEREST_VIEW_COUNTS,
            Permission.SUBMISSION_CREATE,
            Permission.AMENDMENT_PROPOSE,
            Permission.INTEREST_REGISTER,
            Permission.SUBMISSION_WITHDRAW_OWN,
            Permission.REGISTRY_READ,
            Permission.SUBMITTER_IDENTITY_VIEW,
            Permission.INTEREST_IDENTITIES_VIEW,
            Permission.COMMENT_INTERNAL_READ,
            Permission.COMMENT_INTERNAL_WRITE,
            Permission.SUBMISSION_TRANSITION_REVIEW,
            Permission.VALIDATION_RUN,
            Permission.VALIDATION_ACKNOWLEDGE,
            Permission.ROLE_GRANT_MEMBER,
        }
    ),
    # Holds SUBMISSION_WITHDRAW_ANY only, not also _OWN - PRD Section 4.7
    # marks Administrator's cell "Y (any)", not "Y (own)" in addition.
    # This is not a functional gap: `may_act_on` checks `any_` first and
    # returns True immediately when held, so an Administrator's own
    # submissions are already covered without a redundant grant.
    Role.ADMINISTRATOR: frozenset(
        {
            Permission.CATALOGUE_BROWSE,
            Permission.RELEASE_RETRIEVE,
            Permission.SUBMISSION_VIEW,
            Permission.INTEREST_VIEW_COUNTS,
            Permission.SUBMISSION_CREATE,
            Permission.AMENDMENT_PROPOSE,
            Permission.INTEREST_REGISTER,
            Permission.SUBMISSION_WITHDRAW_ANY,
            Permission.REGISTRY_READ,
            Permission.SUBMITTER_IDENTITY_VIEW,
            Permission.INTEREST_IDENTITIES_VIEW,
            Permission.COMMENT_INTERNAL_READ,
            Permission.COMMENT_INTERNAL_WRITE,
            Permission.SUBMISSION_TRANSITION_REVIEW,
            Permission.VALIDATION_RUN,
            Permission.VALIDATION_ACKNOWLEDGE,
            Permission.ROLE_GRANT_MEMBER,
            Permission.SUBMISSION_TRANSITION_APPROVE,
            Permission.CATALOGUE_EDIT_PUBLISHED,
            Permission.RELEASE_PUBLISH,
            Permission.REGISTRY_MANAGE,
            Permission.EXPORT_CONFIG_MANAGE,
            Permission.ROLE_GRANT_ANY,
            Permission.USER_SUSPEND,
            Permission.USER_RATE_LIMIT_OVERRIDE,
            Permission.AUDIT_READ,
        }
    ),
}


def permissions_for_roles(roles: frozenset[Role]) -> frozenset[Permission]:
    """The union of every held role's permissions, plus `Role.ANON`'s
    unconditionally - a user with zero grants is never *less* capable than
    an anonymous visitor (see `nptc.auth.principal.principal_for`)."""
    result = set(ROLE_PERMISSIONS[Role.ANON])
    for role in roles:
        result |= ROLE_PERMISSIONS[role]
    return frozenset(result)


#: Every permission held by Administrator and no other role - PRD Section
#: 4.5's explicit withheld-from-Reviewer list, plus this issue's NFR-06
#: hook (`MFA_REQUIRED_PERMISSIONS` below). Derived, not hand-listed, so a
#: new Administrator-only permission is picked up automatically by both.
_OTHER_ROLE_PERMISSIONS: Final[frozenset[Permission]] = frozenset().union(
    *(perms for role, perms in ROLE_PERMISSIONS.items() if role is not Role.ADMINISTRATOR)
)
ADMINISTRATOR_ONLY: Final[frozenset[Permission]] = (
    ROLE_PERMISSIONS[Role.ADMINISTRATOR] - _OTHER_ROLE_PERMISSIONS
)

#: NFR-06: which permissions require the acting Administrator to have
#: authenticated with MFA (see `nptc.auth.principal.principal_for` and
#: `nptc.auth.authorisation.require_permission`). Equal to
#: `ADMINISTRATOR_ONLY` today - every capability only an Administrator
#: holds is exactly the set NFR-06 exists to protect - kept as a distinct
#: name so a future, narrower policy is a one-line change here, not a
#: search-and-replace at every call site.
MFA_REQUIRED_PERMISSIONS: Final[frozenset[Permission]] = ADMINISTRATOR_ONLY


@dataclass(frozen=True)
class SubmissionQuota:
    """A role's submission budget (PRD Section 4.3/4.4). `None` means
    uncapped. Not a permission - see the module docstring."""

    lifetime_max: int | None
    per_hour_max: int | None


#: Only Provisional and Member carry a stated numeric budget; every other
#: role that can create submissions is uncapped in both dimensions.
QUOTAS: Final[Mapping[Role, SubmissionQuota]] = {
    Role.ANON: SubmissionQuota(lifetime_max=0, per_hour_max=0),
    Role.OBSERVER: SubmissionQuota(lifetime_max=0, per_hour_max=0),
    Role.PROVISIONAL: SubmissionQuota(lifetime_max=5, per_hour_max=None),
    Role.MEMBER: SubmissionQuota(lifetime_max=None, per_hour_max=20),
    Role.REVIEWER: SubmissionQuota(lifetime_max=None, per_hour_max=None),
    Role.ADMINISTRATOR: SubmissionQuota(lifetime_max=None, per_hour_max=None),
}


def effective_quota(
    roles: frozenset[Role], *, override: SubmissionQuota | None = None
) -> SubmissionQuota:
    """The most permissive quota across every held role - `None` beats any
    integer in each dimension, so a Member who is also a Reviewer is not
    capped by the Member row. `override` is the seam for FR-41's per-user
    rate-limit override (deferred past this issue - see ADR-0019's
    Consequences: no column exists yet for it to read)."""
    if override is not None:
        return override
    if not roles:
        return QUOTAS[Role.ANON]

    lifetime_max: int | None = 0
    per_hour_max: int | None = 0
    first = True
    for role in roles:
        quota = QUOTAS[role]
        if first:
            lifetime_max, per_hour_max = quota.lifetime_max, quota.per_hour_max
            first = False
            continue
        if lifetime_max is not None:
            lifetime_max = (
                None if quota.lifetime_max is None else max(lifetime_max, quota.lifetime_max)
            )
        if per_hour_max is not None:
            per_hour_max = (
                None if quota.per_hour_max is None else max(per_hour_max, quota.per_hour_max)
            )
    return SubmissionQuota(lifetime_max=lifetime_max, per_hour_max=per_hour_max)
