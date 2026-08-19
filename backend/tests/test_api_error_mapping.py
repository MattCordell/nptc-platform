"""How `nptc.api.errors` renders the two auth error families over HTTP
(issue #41, FR-44, NFR-04, NFR-06, NFR-20).

`backend/tests/authz_app_support.py` proved `require_permission` against a
throwaway app and its own hand-written handler, standing in for "the
one-line #41 adapter this issue's design defers to a future app". This
module is that adapter's own test, against the production
`create_app()`/`register_exception_handlers` pair.

The negative case is the point (CLAUDE.md: the negative case needs its own
test, not just the positive path).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends
from sqlalchemy.engine import Connection

from nptc.api.app import API_PREFIX
from nptc.api.dependencies import permission_dep
from nptc.auth.errors_authorisation import (
    AccountClosedError,
    LastAdministratorError,
    ManualLinkRequiredError,
)
from nptc.auth.permissions import Permission, Role
from nptc.auth.principal import Principal

# Registered in sys.modules before exec_module - see
# test_authz_negative_http.py for why @dataclass requires it.
_support_spec = importlib.util.spec_from_file_location(
    "api_app_support", Path(__file__).parent / "api_app_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
sys.modules["api_app_support"] = _support
_support_spec.loader.exec_module(_support)

build_api_test_app = _support.build_api_test_app
ApiTestApp = _support.ApiTestApp

#: An Administrator-only permission, so it is also MFA-required
#: (MFA_REQUIRED_PERMISSIONS is derived as exactly ADMINISTRATOR_ONLY).
_ADMIN_PERMISSION = Permission.ROLE_GRANT_MEMBER


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    for harness in build_api_test_app(app_db):
        # A route gated by the real permission_dep adapter. Added here
        # rather than shipped in nptc.api so the production app keeps
        # exactly one route until an issue actually adds a second.
        @harness.app.post(f"{API_PREFIX}/_test/gated")
        def _gated(_p: Principal = Depends(permission_dep(_ADMIN_PERMISSION))) -> dict[str, bool]:  # noqa: B008
            return {"ok": True}

        @harness.app.get(f"{API_PREFIX}/_test/raises/{{error}}")
        def _raises(error: str) -> dict[str, bool]:
            raise {
                "manual-link": ManualLinkRequiredError("two candidate accounts matched"),
                "last-admin": LastAdministratorError("would leave zero administrators"),
                "closed": AccountClosedError("user is closed and may not act"),
            }[error]

        yield harness


def _post_gated(api: ApiTestApp, token: str | None) -> object:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return api.client.post(f"{API_PREFIX}/_test/gated", headers=headers)


@pytest.mark.req("NFR-20")
@pytest.mark.integration
def test_no_credential_is_401_not_403(api: ApiTestApp) -> None:
    """The pair endpoints most reliably get backwards."""
    response = _post_gated(api, None)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_authenticated_but_unpermitted_is_403_with_no_challenge(api: ApiTestApp) -> None:
    """403 is not 401: the credential was fine, the permission was not -
    so there must be no `WWW-Authenticate` header inviting a retry."""
    response = _post_gated(api, api.token(subject="sub-denied"))

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_denial_body_names_neither_a_role_nor_an_internal_id(api: ApiTestApp) -> None:
    """FR-44/NFR-04: a refusal must not leak the role model or an internal
    identifier to the client."""
    response = _post_gated(api, api.token(subject="sub-no-leak"))
    body = response.text

    # Strip each permission value first: some legitimately contain a role
    # name as a substring (ROLE_GRANT_MEMBER contains "member"), which is
    # not itself a leak - the same fix authz_app_support.py applies.
    stripped = body
    for permission in Permission:
        stripped = stripped.replace(permission.value, "")
    for role in Role:
        assert role.value not in stripped, f"response body {body!r} names role {role!r}"


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_mfa_suppressed_role_yields_an_rfc_9470_step_up_challenge(api: ApiTestApp) -> None:
    """An Administrator without MFA is not merely denied - they are told
    how to proceed, per docs/architecture/permissions.md."""
    from nptc.auth.grants import grant_role_unchecked
    from nptc.db.models.user import User

    # Sign in once so the account exists, then make it an Administrator.
    token = api.token(subject="sub-admin-no-mfa")
    api.get("/auth/me", token=token)
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=_audit_context(),
    )
    api.session.flush()

    # Same token: no `acr`, so ADMINISTRATOR is suppressed.
    response = _post_gated(api, token)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    assert 'acr_values="2"' in challenge


@pytest.mark.req("NFR-06")
@pytest.mark.integration
def test_administrator_with_mfa_is_allowed_through(api: ApiTestApp) -> None:
    """The positive half of the pair above: the same principal, with an
    `acr` the realm maps to LoA-2, is permitted."""
    from nptc.auth.grants import grant_role_unchecked
    from nptc.db.models.user import User

    api.get("/auth/me", token=api.token(subject="sub-admin-mfa"))
    user = api.session.query(User).order_by(User.created_at.desc()).first()
    assert user is not None
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=_audit_context(),
    )
    api.session.flush()

    response = _post_gated(api, api.token(subject="sub-admin-mfa", extra_claims={"acr": "2"}))

    assert response.status_code == 200, response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [("manual-link", 409), ("last-admin", 409), ("closed", 403)],
)
def test_http_status_comes_from_the_classvar_not_a_handwritten_ladder(
    api: ApiTestApp, error: str, expected_status: int
) -> None:
    """The two 409s must not be flattened into 403. Reading
    `exc.http_status` is what keeps that true when a subclass is added."""
    response = api.client.get(f"{API_PREFIX}/_test/raises/{error}")

    assert response.status_code == expected_status, response.text


def _audit_context() -> object:
    from nptc.audit.writer import AuditContext

    return AuditContext.system()
