"""Builds the *real* `nptc.api.app.create_app()` over a stub IdP and the
testcontainers database connection (issue #41).

Not a `test_*.py` module - imported by path via `importlib`, the same
convention as `auth_jwt_support.py`/`authz_app_support.py`.

The point of this harness is that nothing about the auth chain is faked.
`get_session` is overridden onto the fixture's connection (so the test's
rollback semantics hold) and `get_token_verifier` onto a verifier pointed
at the local `StubIdp` - but the verifier, the identity resolution and
the permission derivation are all the production ones. A test that passes
here has exercised `TokenVerifier.verify` -> `resolve_user_for_claims` ->
`principal_for` for real.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.api.app import API_PREFIX, create_app
from nptc.api.dependencies import get_auth_settings, get_session, get_token_verifier
from nptc.auth.jwks import SigningKeys
from nptc.auth.tokens import TokenVerifier
from nptc.settings import ApiSettings, AuthSettings

# Registered in sys.modules before exec_module - see
# test_authz_negative_http.py for why @dataclass requires it.
_support_spec = importlib.util.spec_from_file_location(
    "auth_jwt_support", Path(__file__).parent / "auth_jwt_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_jwt_support = importlib.util.module_from_spec(_support_spec)
sys.modules["auth_jwt_support"] = _jwt_support
_support_spec.loader.exec_module(_jwt_support)

StubIdp = _jwt_support.StubIdp
running_stub_idp = _jwt_support.running_stub_idp
mint_token = _jwt_support.mint_token
generate_rsa_key = _jwt_support.generate_rsa_key

KID = "test-key-1"
AUDIENCE = "nptc-api"
FRONTEND_ORIGIN = "http://localhost:5173"


@dataclass
class ApiTestApp:
    app: FastAPI
    client: TestClient
    idp: StubIdp
    key: RSAPrivateKey
    session: Session

    @property
    def issuer(self) -> str:
        return str(self.idp.issuer_url)

    def token(self, **kwargs: Any) -> str:
        """A token this app's verifier will accept, unless a kwarg makes
        it unacceptable on purpose."""
        kwargs.setdefault("issuer", self.issuer)
        kwargs.setdefault("audience", AUDIENCE)
        return str(mint_token(self.key, kid=KID, **kwargs))

    def request(self, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> Any:
        """The general form `get`/`post` below are thin wrappers over -
        issue #219 is the first caller needing a verb other than GET."""
        headers = dict(kwargs.pop("headers", {}))
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self.client.request(method, f"{API_PREFIX}{path}", headers=headers, **kwargs)

    def get(self, path: str, *, token: str | None = None, **kwargs: Any) -> Any:
        return self.request("GET", path, token=token, **kwargs)

    def post(self, path: str, *, token: str | None = None, **kwargs: Any) -> Any:
        return self.request("POST", path, token=token, **kwargs)


def build_api_test_app(
    connection: Connection,
    *,
    trusted_issuers: frozenset[str] | None = None,
    mfa_acr_values: frozenset[str] = frozenset({"2"}),
) -> Iterator[ApiTestApp]:
    """Yields a `TestClient` over the production app.

    A generator (not a plain function) so the `StubIdp`'s HTTP server is
    shut down deterministically rather than at GC time.
    """
    with running_stub_idp() as idp:
        key = generate_rsa_key()
        idp.add_key(KID, key)
        issuer = idp.issuer_url

        settings = AuthSettings(
            oidc_issuer=issuer,
            oidc_audience=AUDIENCE,
            jwks_url=idp.jwks_url,
            trusted_issuers=trusted_issuers if trusted_issuers is not None else frozenset({issuer}),
            mfa_acr_values=mfa_acr_values,
        )
        verifier = TokenVerifier(
            issuer=issuer,
            audience=AUDIENCE,
            keys=SigningKeys(idp.jwks_url),
        )
        session = Session(bind=connection)

        app = create_app(settings=ApiSettings(frontend_base_url=FRONTEND_ORIGIN))
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_token_verifier] = lambda: verifier
        app.dependency_overrides[get_auth_settings] = lambda: settings

        # raise_server_exceptions=False so a handler-mapped error is
        # observed as the HTTP response a real client would see, not
        # re-raised into the test.
        with TestClient(app, raise_server_exceptions=False) as client:
            yield ApiTestApp(app=app, client=client, idp=idp, key=key, session=session)
