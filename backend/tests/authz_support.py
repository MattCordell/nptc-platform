"""Reusable negative-authorisation assertions (issue #44's own acceptance
criteria: "a reusable assert-forbidden helper" and "every mutating
endpoint has a negative-case test using the shared harness").

Not a `test_*.py` module - imported by path via `importlib`, the same
convention `auth_jwt_support.py` established for #43, since
`backend/tests` has no `__init__.py` (pytest runs with
`--import-mode=importlib`).

Layer 1 (library-level, works with zero HTTP endpoints in existence):
`assert_permission_refused` below. Layer 2 (HTTP-level, against a small
test-only FastAPI app) is `authz_app_support.py`. Layer 3 (does every
mutating *route* have a negative case) is `route_inventory_support.py`.
"""

from __future__ import annotations

from nptc.auth.authorisation import require_permission
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import ROLE_PERMISSIONS, Permission, Role
from nptc.auth.principal import Principal


def assert_permission_refused(
    principal: Principal, permission: Permission
) -> PermissionDeniedError:
    """Asserts `require_permission(permission)(principal)` raises, that
    the raised message names only the permission - never a role value,
    never the internal user UUID (NFR-04/NFR-26) - and, critically, that
    `permission` is genuinely held by at least one role.

    That last check is what stops this helper from rotting into an
    always-pass: without it, `assert_permission_refused(principal,
    Permission("a typo"))` would pass trivially forever, having asserted
    nothing about real authorisation behaviour. This is the same "a
    guard that can vacuously pass is not a guard" discipline
    `test_token_verification_guard.py`'s own positive control exists to
    enforce, applied to a runtime assertion instead of a static one.
    """
    assert any(permission in perms for perms in ROLE_PERMISSIONS.values()), (
        f"{permission!r} is not granted by any role - assert_permission_refused would "
        "pass vacuously; check the Permission constant is spelled correctly"
    )

    try:
        require_permission(permission)(principal)
    except PermissionDeniedError as exc:
        message = str(exc)
        assert permission.value in message, (
            f"error message {message!r} does not name {permission!r}"
        )
        if principal.user_id is not None:
            assert str(principal.user_id) not in message
        # Strip the permission's own value before scanning for a leaked
        # role name - some permission values legitimately contain a role
        # name as a substring (e.g. Permission.ROLE_GRANT_MEMBER's value
        # is "role.grant.member", containing "member"), which is not a
        # leak of Role.MEMBER.
        message_without_permission = message.replace(permission.value, "")
        for role in Role:
            assert role.value not in message_without_permission, (
                f"error message {message!r} names role {role!r}"
            )
        return exc

    raise AssertionError(
        f"expected {permission!r} to be refused for principal with roles "
        f"{sorted(r.value for r in principal.roles)}, but it was granted"
    )
