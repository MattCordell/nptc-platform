"""`nptc.auth.authorisation` (issue #44, FR-44, NFR-20): `require_permission`,
`may_act_on`/`require_ownership_or_permission`, and quota resolution. Pure
unit tests - `Principal` is a frozen dataclass with no I/O, so none of this
needs a database.
"""

from __future__ import annotations

import uuid

import pytest

from nptc.auth.authorisation import (
    has_permission,
    may_act_on,
    require_ownership_or_permission,
    require_permission,
    resolve_quota,
)
from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import ROLE_PERMISSIONS, Permission, Role, permissions_for_roles
from nptc.auth.principal import ANONYMOUS, Principal

_USER_A = uuid.uuid4()
_USER_B = uuid.uuid4()


def _principal(
    *,
    user_id: uuid.UUID | None,
    roles: frozenset[Role],
    mfa_satisfied: bool = True,
    mfa_suppressed_roles: frozenset[Role] = frozenset(),
) -> Principal:
    return Principal(
        user_id=user_id,
        user_ref=None,
        status=None,
        roles=roles,
        permissions=permissions_for_roles(roles),
        mfa_satisfied=mfa_satisfied,
        mfa_suppressed_roles=mfa_suppressed_roles,
    )


def test_has_permission_matches_principal_has() -> None:
    reviewer = _principal(user_id=_USER_A, roles=frozenset({Role.REVIEWER}))
    assert has_permission(reviewer, Permission.COMMENT_INTERNAL_WRITE)
    assert not has_permission(reviewer, Permission.RELEASE_PUBLISH)


def test_require_permission_returns_the_principal_when_held() -> None:
    reviewer = _principal(user_id=_USER_A, roles=frozenset({Role.REVIEWER}))
    check = require_permission(Permission.COMMENT_INTERNAL_WRITE)
    assert check(reviewer) is reviewer


def test_require_permission_raises_permission_denied_when_not_held() -> None:
    with pytest.raises(PermissionDeniedError, match=r"release\.publish"):
        require_permission(Permission.RELEASE_PUBLISH)(ANONYMOUS)


def test_require_permission_error_names_only_the_permission() -> None:
    """Never a role, never the internal user UUID (NFR-04/NFR-26)."""
    reviewer = _principal(user_id=_USER_A, roles=frozenset({Role.REVIEWER}))
    with pytest.raises(PermissionDeniedError) as excinfo:
        require_permission(Permission.RELEASE_PUBLISH)(reviewer)
    message = str(excinfo.value)
    assert "release.publish" in message
    assert str(_USER_A) not in message
    for role in Role:
        assert role.value not in message


def test_require_permission_raises_mfa_required_when_suppressed() -> None:
    """The distinguishing case: the principal holds Administrator, but it
    is suppressed for want of MFA - this must be a distinct, actionable
    error, not a bare denial."""
    suppressed_admin = _principal(
        user_id=_USER_A,
        roles=frozenset(),
        mfa_satisfied=False,
        mfa_suppressed_roles=frozenset({Role.ADMINISTRATOR}),
    )
    with pytest.raises(MfaRequiredError):
        require_permission(Permission.RELEASE_PUBLISH)(suppressed_admin)


def test_require_permission_does_not_raise_mfa_required_for_an_unrelated_denial() -> None:
    """A principal with no suppressed roles at all gets the ordinary
    denial, not an MFA challenge for a permission MFA never gated."""
    observer = _principal(user_id=_USER_A, roles=frozenset({Role.OBSERVER}))
    with pytest.raises(PermissionDeniedError) as excinfo:
        require_permission(Permission.RELEASE_PUBLISH)(observer)
    assert not isinstance(excinfo.value, MfaRequiredError)


def test_may_act_on_grants_via_any_regardless_of_ownership() -> None:
    admin = _principal(user_id=_USER_A, roles=frozenset({Role.ADMINISTRATOR}))
    assert may_act_on(
        admin,
        own=Permission.SUBMISSION_WITHDRAW_OWN,
        any_=Permission.SUBMISSION_WITHDRAW_ANY,
        owner_user_id=_USER_B,
    )


def test_may_act_on_grants_via_own_only_when_owner_matches() -> None:
    member = _principal(user_id=_USER_A, roles=frozenset({Role.MEMBER}))
    assert may_act_on(
        member,
        own=Permission.SUBMISSION_WITHDRAW_OWN,
        any_=Permission.SUBMISSION_WITHDRAW_ANY,
        owner_user_id=_USER_A,
    )
    assert not may_act_on(
        member,
        own=Permission.SUBMISSION_WITHDRAW_OWN,
        any_=Permission.SUBMISSION_WITHDRAW_ANY,
        owner_user_id=_USER_B,
    )


def test_may_act_on_refuses_a_reviewer_withdrawing_someone_elses_submission() -> None:
    """FR-81's non-obvious seventh withheld capability: Reviewer holds
    `_OWN` but not `_ANY`."""
    reviewer = _principal(user_id=_USER_A, roles=frozenset({Role.REVIEWER}))
    assert not may_act_on(
        reviewer,
        own=Permission.SUBMISSION_WITHDRAW_OWN,
        any_=Permission.SUBMISSION_WITHDRAW_ANY,
        owner_user_id=_USER_B,
    )


def test_may_act_on_with_no_owner_never_matches_own() -> None:
    """An orphaned/system-authored resource resolves to `any_` only -
    there is no path where a null owner matches a null `principal.user_id`."""
    member = _principal(user_id=_USER_A, roles=frozenset({Role.MEMBER}))
    assert not may_act_on(
        member,
        own=Permission.SUBMISSION_WITHDRAW_OWN,
        any_=Permission.SUBMISSION_WITHDRAW_ANY,
        owner_user_id=None,
    )


def test_may_act_on_anonymous_principal_never_matches() -> None:
    assert not may_act_on(
        ANONYMOUS,
        own=Permission.SUBMISSION_WITHDRAW_OWN,
        any_=Permission.SUBMISSION_WITHDRAW_ANY,
        owner_user_id=None,
    )


def test_require_ownership_or_permission_raises_when_refused() -> None:
    member = _principal(user_id=_USER_A, roles=frozenset({Role.MEMBER}))
    with pytest.raises(PermissionDeniedError):
        require_ownership_or_permission(
            member,
            own=Permission.SUBMISSION_WITHDRAW_OWN,
            any_=Permission.SUBMISSION_WITHDRAW_ANY,
            owner_user_id=_USER_B,
        )


def test_resolve_quota_uses_the_principals_roles() -> None:
    provisional = _principal(user_id=_USER_A, roles=frozenset({Role.PROVISIONAL}))
    quota = resolve_quota(provisional)
    assert quota.lifetime_max == 5


def test_resolve_quota_honours_an_explicit_override() -> None:
    from nptc.auth.permissions import SubmissionQuota

    provisional = _principal(user_id=_USER_A, roles=frozenset({Role.PROVISIONAL}))
    override = SubmissionQuota(lifetime_max=42, per_hour_max=None)
    assert resolve_quota(provisional, override=override) == override


def test_anonymous_principal_has_exactly_the_anon_permissions() -> None:
    assert ANONYMOUS.permissions == ROLE_PERMISSIONS[Role.ANON]
    assert ANONYMOUS.user_id is None
    assert ANONYMOUS.roles == frozenset()
