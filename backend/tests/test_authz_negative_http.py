"""HTTP-level negative-authorisation tests (issue #44, FR-80, FR-81,
NFR-20) against the throwaway `authz_app_support.build_authz_test_app`
harness - real HTTP over `require_permission`, exercisable today even
though `nptc/api/` has no real routes yet.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from nptc.auth.permissions import Permission, Role, permissions_for_roles
from nptc.auth.principal import ANONYMOUS, Principal


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered in sys.modules *before* exec_module: @dataclass's own
    # machinery looks up `sys.modules[cls.__module__]` to resolve forward
    # references, which fails with a bare AttributeError if the module
    # was never registered - a module built via module_from_spec alone
    # never is.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_app_support = _load("authz_app_support")
build_authz_test_app = _app_support.build_authz_test_app
assert_http_forbidden = _app_support.assert_http_forbidden


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


@pytest.mark.req("NFR-20")
def test_a_principal_without_the_permission_gets_403_and_no_credential_gets_401() -> None:
    harness = build_authz_test_app(Permission.CATALOGUE_EDIT_PUBLISHED)
    observer = _principal(frozenset({Role.OBSERVER}))
    assert_http_forbidden(harness, "POST", "/write", principal=observer)


@pytest.mark.req("FR-80")
def test_anonymous_principal_is_refused_every_write_route() -> None:
    """FR-80's spirit, at the HTTP layer: Observer/Anonymous have no
    write capability, and a route gated on any write permission refuses
    both alike."""
    harness = build_authz_test_app(Permission.SUBMISSION_CREATE)
    assert_http_forbidden(harness, "POST", "/write", principal=ANONYMOUS)


@pytest.mark.req("FR-81")
def test_reviewer_is_refused_an_administrator_only_route() -> None:
    harness = build_authz_test_app(Permission.RELEASE_PUBLISH)
    reviewer = _principal(frozenset({Role.REVIEWER}))
    assert_http_forbidden(harness, "POST", "/write", principal=reviewer)


@pytest.mark.req("NFR-20")
def test_a_principal_with_the_permission_succeeds() -> None:
    harness = build_authz_test_app(Permission.RELEASE_PUBLISH)
    admin = _principal(frozenset({Role.ADMINISTRATOR}))
    harness.as_principal(admin)
    try:
        response = harness.client.post("/write")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
    finally:
        harness.clear_override()
