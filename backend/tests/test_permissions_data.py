"""Data-level properties of `nptc.auth.permissions` that don't belong in
`test_permission_matrix.py` (which is purely "does the code match the PRD
table"): exhaustive classification, FR-80/FR-81 as statements about data
rather than endpoints, and quota resolution.

**FR-80 and FR-81 are provable without a single HTTP endpoint existing**,
because both are worded as properties of *what a role is permitted*, not
of any particular request path - see PRD Section 4.2's "MUST be enforced
as an absence of permissions, not as UI suppression" and Section 4.5's
"MUST be enforced server-side per permission". `test_observer_has_no_write_
permission_at_all` and `test_administrator_only_permissions_are_refused_
to_reviewer` are exactly that: stronger and more durable than any
per-endpoint negative test, because they fail the day someone adds a
single write permission to Observer or an admin-only permission to
Reviewer, regardless of whether any endpoint yet exists to expose it.
"""

from __future__ import annotations

from itertools import pairwise

from nptc.auth.permissions import (
    ADMINISTRATOR_ONLY,
    GRANTABLE_ROLES,
    MFA_REQUIRED_PERMISSIONS,
    PERMISSION_KIND,
    QUOTAS,
    ROLE_PERMISSIONS,
    WRITE_PERMISSIONS,
    Permission,
    Role,
    SubmissionQuota,
    effective_quota,
    permissions_for_roles,
)


def test_every_permission_is_classified_read_or_write() -> None:
    """Mirrors `nptc.audit.policy`'s own "every column classified or
    construction fails" discipline - an unclassified `Permission` would
    silently vanish from `WRITE_PERMISSIONS` and so from FR-80's check."""
    assert set(PERMISSION_KIND) == set(Permission)


def test_write_permissions_is_derived_not_hand_duplicated() -> None:
    from nptc.auth.permissions import PermissionKind

    expected = {p for p, kind in PERMISSION_KIND.items() if kind is PermissionKind.WRITE}
    assert frozenset(expected) == WRITE_PERMISSIONS


def test_every_role_has_a_permission_set() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_grantable_roles_excludes_anon() -> None:
    """ANON is a matrix column (the anonymous visitor), never a grantable
    `user_role` row - see `nptc.db.models.user_role`'s CHECK constraint,
    which must also never list 'anon'."""
    assert Role.ANON not in GRANTABLE_ROLES
    assert frozenset(Role) - {Role.ANON} == GRANTABLE_ROLES


def test_observer_has_no_write_permission_at_all() -> None:
    """FR-80 (MUST): 'The Observer role MUST have no write capability of
    any kind ... enforced as an absence of permissions'."""
    assert not (ROLE_PERMISSIONS[Role.OBSERVER] & WRITE_PERMISSIONS)


def test_administrator_only_permissions_are_refused_to_reviewer() -> None:
    """FR-81 (MUST): 'The boundary between Reviewer and Administrator MUST
    be enforced server-side per permission'. `ADMINISTRATOR_ONLY` is every
    permission Administrator holds and no other role does - if Reviewer
    held any of them, this assertion (not `ADMINISTRATOR_ONLY`'s own
    derivation) would catch it."""
    assert not (ROLE_PERMISSIONS[Role.REVIEWER] & ADMINISTRATOR_ONLY)


def test_administrator_only_covers_every_prd_section_4_5_withheld_capability() -> None:
    """The six capabilities PRD Section 4.5 explicitly withholds from
    Reviewer, plus the non-obvious seventh - a Reviewer may withdraw only
    their *own* submission, never any submission."""
    withheld = {
        Permission.SUBMISSION_TRANSITION_APPROVE,
        Permission.RELEASE_PUBLISH,
        Permission.CATALOGUE_EDIT_PUBLISHED,
        Permission.ROLE_GRANT_ANY,
        Permission.REGISTRY_MANAGE,
        Permission.EXPORT_CONFIG_MANAGE,
        Permission.USER_SUSPEND,
        Permission.USER_RATE_LIMIT_OVERRIDE,
        Permission.SUBMISSION_WITHDRAW_ANY,
    }
    assert withheld <= ADMINISTRATOR_ONLY


def test_mfa_required_permissions_is_administrator_only_derived() -> None:
    """Derived, never hand-listed - a new Administrator-only permission
    must automatically require MFA without a second edit anywhere."""
    assert MFA_REQUIRED_PERMISSIONS == ADMINISTRATOR_ONLY


#: `may_act_on` treats holding `SUBMISSION_WITHDRAW_ANY` as strictly
#: stronger than `SUBMISSION_WITHDRAW_OWN` (it checks `any_` first and
#: short-circuits), so Administrator holding only `_ANY` - not also
#: `_OWN`, exactly matching the PRD table's literal cell - is not a real
#: capability loss relative to Reviewer's `_OWN`. Excluded from the
#: monotonicity chain below rather than silently miscompared: the own/any
#: relationship is already asserted directly by
#: `test_permission_matrix.py` and `test_administrator_only_permissions_
#: are_refused_to_reviewer` above.
_WITHDRAW_PERMISSIONS = frozenset(
    {Permission.SUBMISSION_WITHDRAW_OWN, Permission.SUBMISSION_WITHDRAW_ANY}
)


def _normalise_withdraw(perms: frozenset[Permission]) -> frozenset[Permission]:
    return perms - _WITHDRAW_PERMISSIONS


def test_role_permissions_is_monotonically_increasing() -> None:
    """PRD Section 4's prose is "Adds to X" at every step (Observer ->
    Provisional -> Member -> Reviewer -> Administrator) - this is the one
    property `test_permission_matrix.py`'s cell-by-cell check doesn't
    directly state, so it is asserted here instead. Not baked into
    `ROLE_PERMISSIONS`'s own representation (see that module's docstring
    on why each role is an explicit literal set) - this is a test
    property, not a code-level assumption a future non-monotone role
    addition would be silently unable to express."""
    chain = [
        Role.ANON,
        Role.OBSERVER,
        Role.PROVISIONAL,
        Role.MEMBER,
        Role.REVIEWER,
        Role.ADMINISTRATOR,
    ]
    for lower, higher in pairwise(chain):
        lower_perms = _normalise_withdraw(ROLE_PERMISSIONS[lower])
        higher_perms = _normalise_withdraw(ROLE_PERMISSIONS[higher])
        assert lower_perms <= higher_perms, f"{higher.value} does not add to {lower.value}"


def test_permissions_for_roles_always_includes_anon() -> None:
    """A user with zero grants (or every grant suppressed for want of
    MFA) is never *less* capable than an anonymous visitor."""
    assert permissions_for_roles(frozenset()) == ROLE_PERMISSIONS[Role.ANON]
    assert ROLE_PERMISSIONS[Role.ANON] <= permissions_for_roles(frozenset({Role.ADMINISTRATOR}))


def test_every_role_has_a_quota() -> None:
    assert set(QUOTAS) == set(Role)


def test_effective_quota_prefers_the_explicit_override() -> None:
    override = SubmissionQuota(lifetime_max=999, per_hour_max=999)
    assert effective_quota(frozenset({Role.PROVISIONAL}), override=override) == override


def test_effective_quota_for_no_roles_is_the_anon_floor() -> None:
    assert effective_quota(frozenset()) == QUOTAS[Role.ANON]


def test_effective_quota_uncaps_when_any_held_role_is_uncapped() -> None:
    """None beats any integer in each dimension - a Member who is also a
    Reviewer must not be capped by the Member row (PRD Section 4.4/4.5)."""
    quota = effective_quota(frozenset({Role.MEMBER, Role.REVIEWER}))
    assert quota == SubmissionQuota(lifetime_max=None, per_hour_max=None)


def test_effective_quota_resolves_per_dimension_independently() -> None:
    """Provisional is uncapped per-hour; Member is uncapped for its
    lifetime total - holding both (an edge case: ordinary promotion
    revokes Provisional when granting Member, but nothing enforces that
    at this layer) resolves to fully uncapped, because "None beats any
    integer" is applied to each dimension independently rather than
    picking one role's quota wholesale. Worth stating plainly rather than
    leaving as a surprise: this is the resolution *rule* as designed, not
    a bug - `effective_quota` is unit-tested here but not yet enforced
    anywhere (see the module docstring), so this has no live effect until
    the submissions issue wires it in."""
    quota = effective_quota(frozenset({Role.PROVISIONAL, Role.MEMBER}))
    assert quota == SubmissionQuota(lifetime_max=None, per_hour_max=None)
