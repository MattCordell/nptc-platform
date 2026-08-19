"""A small, test-only FastAPI app exercising `nptc.auth.authorisation.
require_permission` end to end over real HTTP (issue #44).

There is no FastAPI app anywhere in `backend/src` - this issue is
deliberately pure-library (ADR-0016's "Scope", ADR-0019) - so this module
is the *only* place `fastapi` is imported for this issue's purposes.
`fastapi`/`httpx` are already declared backend dependencies (for #41/#142/
#143's eventual app), so building a throwaway app here costs nothing new.

Not a `test_*.py` module - imported by path via `importlib`, same
convention as `auth_jwt_support.py`/`authz_support.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from nptc.auth.authorisation import require_permission
from nptc.auth.errors_authorisation import AuthorisationError
from nptc.auth.permissions import Permission
from nptc.auth.principal import Principal


def _unauthenticated() -> Principal:
    """The harness's stand-in for a real #41 `current_principal`
    dependency: with no override installed, every request has "no
    credential presented" - a 401, per NFR-20's own posture that an
    authorisation decision is never made without first establishing who
    is asking."""
    raise HTTPException(status_code=401, detail="no credential presented")


@dataclass
class AuthzTestApp:
    app: FastAPI
    client: TestClient
    #: The dependency `assert_http_forbidden` overrides per-request via
    #: `app.dependency_overrides` - FastAPI's own sanctioned mechanism for
    #: swapping a dependency in a test, so no header/global-state parsing
    #: hack is needed to inject a `Principal`.
    get_principal: Callable[[], Principal]

    def as_principal(self, principal: Principal) -> None:
        self.app.dependency_overrides[self.get_principal] = lambda: principal

    def clear_override(self) -> None:
        self.app.dependency_overrides.pop(self.get_principal, None)


def build_authz_test_app(permission: Permission, *, path: str = "/write") -> AuthzTestApp:
    """One POST route at `path`, gated by `permission` through the real
    `require_permission` - not a fake or a re-implementation of the check."""
    app = FastAPI()

    def get_principal() -> Principal:
        return _unauthenticated()

    def _check(principal: Principal = Depends(get_principal)) -> Principal:  # noqa: B008
        return require_permission(permission)(principal)

    @app.post(path)
    def _endpoint(principal: Principal = Depends(_check)) -> dict[str, bool]:  # noqa: B008
        return {"ok": True}

    # The one-line #41 adapter this issue's design defers to a future app -
    # here, standing in for it, is exactly this exception handler mapping
    # AuthorisationError.http_status to the response, per subclass
    # (PermissionDeniedError/MfaRequiredError -> 403,
    # ManualLinkRequiredError/LastAdministratorError -> 409).
    @app.exception_handler(AuthorisationError)
    def _handle_authorisation_error(_request: object, exc: AuthorisationError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"detail": str(exc)})

    return AuthzTestApp(app=app, client=TestClient(app), get_principal=get_principal)


def assert_http_forbidden(
    harness: AuthzTestApp, method: str, path: str, *, principal: Principal
) -> None:
    """Asserts the full negative-authorisation pair a real endpoint must
    get right:

    1. With `principal` installed (missing the gated permission): 403, no
       `WWW-Authenticate` header (403 is not 401 - the credential was
       fine, the permission was not), and the body names neither a role
       nor an internal UUID.
    2. With no credential at all (the override cleared): 401, not 403 -
       the pair endpoints most reliably get backwards.
    """
    from nptc.auth.permissions import Role

    harness.as_principal(principal)
    try:
        response = harness.client.request(method, path)
        assert response.status_code == 403, response.text
        assert "WWW-Authenticate" not in response.headers
        body = response.text
        if principal.user_id is not None:
            assert str(principal.user_id) not in body
        # Strip every gated permission's own value first - some
        # permission values legitimately contain a role name as a
        # substring (Permission.ROLE_GRANT_MEMBER's value contains
        # "member"), which is not itself a leak of Role.MEMBER. See
        # authz_support.py's assert_permission_refused for the same fix.
        from nptc.auth.permissions import Permission

        body_without_permissions = body
        for permission in Permission:
            body_without_permissions = body_without_permissions.replace(permission.value, "")
        for role in Role:
            assert role.value not in body_without_permissions, (
                f"response body {body!r} names role {role!r}"
            )
    finally:
        harness.clear_override()

    unauthenticated_response = harness.client.request(method, path)
    assert unauthenticated_response.status_code == 401, unauthenticated_response.text
