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
from nptc.settings import ApiSettings
from nptc_shared.terminology import TerminologyConfigError

#: A `TerminologyConfig.from_env` numeric variable, named here so the tests
#: below assert the *value* never reaches a response body (NFR-26).
_TX_CONFIG_VAR = "NPTC_TX_TIMEOUT_SECONDS"

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
                "tx-config": TerminologyConfigError(
                    f"{_TX_CONFIG_VAR}='not-a-number' is not a valid number"
                ),
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


@pytest.mark.req("FR-20")
def test_a_malformed_terminology_config_fails_app_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real fix for a deployment typo: fail at start-up, not per request.

    `get_terminology_client` builds `TerminologyConfig.from_env()`, which
    refuses a malformed numeric variable rather than falling back to the
    default. Left to the first request that needed a terminology client,
    that refusal would be an unhandled 500 on a *public* read endpoint -
    and, because `lru_cache` does not cache a raised exception, a fresh one
    on every request for as long as nobody noticed. `create_app` therefore
    builds the client itself, which turns the same typo into a start-up
    failure.

    Issue #52 split what was `get_datatype_registry` into
    `get_terminology_client` (still `lru_cache`d process-wide - an
    `OntoserverClient` owns an HTTP pool) and a request-scoped
    `get_datatype_registry` (it now wires a `DatabaseLocalCodeLookup`
    against the request's own `Session`, per FR-10/#56, so it can no
    longer be a single process-lifetime instance). This test moves to the
    piece that still fails at start-up.

    No `integration` mark and no database: this is entirely about where the
    exception is raised.
    """
    from nptc.api.app import create_app
    from nptc.api.dependencies import get_terminology_client

    monkeypatch.setenv(_TX_CONFIG_VAR, "not-a-number")
    # The cache is process-wide, so it is cleared on the way in (another test
    # may have populated it with a good config) and on the way out (so this
    # test's bad config does not become everybody's).
    get_terminology_client.cache_clear()
    try:
        with pytest.raises(TerminologyConfigError):
            create_app(settings=ApiSettings(frontend_base_url="http://localhost:5173"))
    finally:
        get_terminology_client.cache_clear()


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_terminology_config_error_reaching_a_request_is_a_handled_500(
    api: ApiTestApp,
) -> None:
    """The safety net behind the start-up check above.

    A path that bypasses `create_app`'s warm-up - a test app, a dependency
    override, a lazily built client added later - must still produce a
    deliberate 500 with a logged cause rather than an unhandled traceback.
    The response names neither the variable nor its value: a configuration
    fault is not something a caller can act on, and echoing deployment
    details into a public response body is exactly what NFR-26 forbids.
    """
    response = api.client.get(f"{API_PREFIX}/_test/raises/tx-config")

    assert response.status_code == 500, response.text
    assert response.json()["detail"]
    assert _TX_CONFIG_VAR not in response.text
    assert "not-a-number" not in response.text
