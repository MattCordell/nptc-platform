"""`authz_support.assert_permission_refused` itself (issue #44) - the
library-level layer of the negative-authorisation harness, including its
own vacuity guard (a helper that can pass without checking anything is
not a guard - see the module's docstring)."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from nptc.auth.permissions import Permission, Role, permissions_for_roles
from nptc.auth.principal import Principal


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_support = _load("authz_support")
assert_permission_refused = _support.assert_permission_refused


def _principal(roles: frozenset[Role]) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        user_ref=None,
        status=None,
        roles=roles,
        permissions=permissions_for_roles(roles),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


def test_asserts_the_refusal_for_a_genuinely_missing_permission() -> None:
    observer = _principal(frozenset({Role.OBSERVER}))
    exc = assert_permission_refused(observer, Permission.RELEASE_PUBLISH)
    assert "release.publish" in str(exc)


def test_raises_assertion_error_if_the_permission_is_actually_granted() -> None:
    admin = _principal(frozenset({Role.ADMINISTRATOR}))
    with pytest.raises(AssertionError, match="was granted"):
        assert_permission_refused(admin, Permission.RELEASE_PUBLISH)


def test_the_vacuity_guard_catches_a_permission_no_role_ever_holds() -> None:
    """A hand-typo'd/orphaned Permission would otherwise make this helper
    pass forever without ever asserting anything real - the guard this
    test proves exists."""

    class _FakePermission:
        value = "not.a.real.permission"

    observer = _principal(frozenset({Role.OBSERVER}))
    with pytest.raises(AssertionError, match="not granted by any role"):
        assert_permission_refused(observer, _FakePermission())  # type: ignore[arg-type]


def test_does_not_falsely_flag_a_role_name_embedded_in_the_permission_value() -> None:
    """`Permission.ROLE_GRANT_MEMBER`'s own value contains the substring
    "member" - the leakage check must not mistake that for a leaked
    `Role.MEMBER`."""
    observer = _principal(frozenset({Role.OBSERVER}))
    exc = assert_permission_refused(observer, Permission.ROLE_GRANT_MEMBER)
    assert "role.grant.member" in str(exc)
