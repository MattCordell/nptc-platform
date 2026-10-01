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

`get_terminology_client` is the one exception, overridden onto a
`StubTerminologyClient` (issue #240) - real terminology-server traffic has
no place in this suite at all (NFR-37), unlike the auth chain above, which
this harness deliberately exercises for real against a local, in-process
IdP.

**Each request runs inside its own `SAVEPOINT`, mirroring
`nptc.db.session.session_scope`'s commit-on-success/rollback-on-exception
contract without touching the outer transaction the fixture rolls back at
teardown** (issue #219 review) - `_scoped_session` below is the override,
not a bare `lambda: session`. Before this, every request in a test shared
one open transaction with no per-request boundary at all, so a route that
raised partway through a multi-write sequence left its partial writes
flushed and visible to the rest of that same test - `session_scope` never
actually ran in a test, only in production, and a test asserting "this
failed request wrote nothing" could not tell a real rollback from an
artifact of never having tried to roll back. `Session.begin_nested()` is
the same SAVEPOINT precedent `nptc.auth.identity._create_user` already
uses for a bounded retry.
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
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.api.app import API_PREFIX, create_app
from nptc.api.dependencies import (
    get_api_settings,
    get_auth_settings,
    get_session,
    get_terminology_client,
    get_token_verifier,
)
from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.jwks import SigningKeys
from nptc.auth.permissions import Role
from nptc.auth.tokens import TokenVerifier
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc.settings import ApiSettings, AuthSettings
from nptc_shared.terminology import StubTerminologyClient

# Registered in sys.modules before exec_module - see
# test_authz_negative_http.py for why @dataclass requires it.
_support_spec = importlib.util.spec_from_file_location(
    "auth_jwt_support", Path(__file__).parent / "auth_jwt_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_jwt_support = importlib.util.module_from_spec(_support_spec)
sys.modules["auth_jwt_support"] = _jwt_support
_support_spec.loader.exec_module(_jwt_support)

_hermetic_spec = importlib.util.spec_from_file_location(
    "hermetic_settings_support", Path(__file__).parent / "hermetic_settings_support.py"
)
assert _hermetic_spec is not None and _hermetic_spec.loader is not None
_hermetic = importlib.util.module_from_spec(_hermetic_spec)
sys.modules["hermetic_settings_support"] = _hermetic
_hermetic_spec.loader.exec_module(_hermetic)

hermetic_api_settings = _hermetic.hermetic_api_settings

StubIdp = _jwt_support.StubIdp
running_stub_idp = _jwt_support.running_stub_idp
mint_token = _jwt_support.mint_token
generate_rsa_key = _jwt_support.generate_rsa_key
shared_rsa_key = _jwt_support.shared_rsa_key

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
    #: The `StubTerminologyClient` `get_terminology_client` is overridden
    #: onto for this app (issue #240) - a test seeds `expand`/`lookup`/etc.
    #: responses on this directly, and can inspect `.requests` for the
    #: "exactly one upstream request" assertions FR-26/FR-52 both need.
    terminology: StubTerminologyClient

    def set_api_settings(self, **fields: Any) -> ApiSettings:
        """Replaces the `ApiSettings` this app serves for the rest of the
        test, keeping every field not named in `fields` - the same override
        `create_app` installs, so routes and helpers all see the new object."""
        current = self.app.dependency_overrides[get_api_settings]()
        api_settings = hermetic_api_settings(**{**current.model_dump(), **fields})
        self.app.dependency_overrides[get_api_settings] = lambda: api_settings
        return api_settings

    @property
    def issuer(self) -> str:
        return str(self.idp.issuer_url)

    def token(self, **kwargs: Any) -> str:
        """A token this app's verifier will accept, unless a kwarg makes
        it unacceptable on purpose."""
        kwargs.setdefault("issuer", self.issuer)
        kwargs.setdefault("audience", AUDIENCE)
        return str(mint_token(self.key, kid=KID, **kwargs))

    def token_for_role(self, *, subject: str, role: Role, with_mfa: bool = True) -> str:
        """Signs `subject` in through the real auth chain, grants `role`, and
        returns a token carrying the `acr` claim the realm maps to LoA-2
        unless `with_mfa` is `False`."""
        bootstrap = self.token(subject=subject)
        self.get("/auth/me", token=bootstrap)
        # By `subject`, not "the newest `User` row": `created_at` is
        # server-side `now()`, so two users provisioned inside one transaction
        # can tie on it, and the wrong one would be granted `role`.
        user = self.session.execute(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.subject == subject)
        ).scalar_one()
        grant_role_unchecked(
            self.session,
            target_user_id=user.id,
            role=role,
            granted_by_user_id=None,
            audit=AuditContext.system(),
        )
        self.session.flush()
        extra_claims = {"acr": "2"} if with_mfa else {}
        return self.token(subject=subject, extra_claims=extra_claims)

    def admin_token(self, *, subject: str, with_mfa: bool = True) -> str:
        return self.token_for_role(subject=subject, role=Role.ADMINISTRATOR, with_mfa=with_mfa)

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
    api_settings: ApiSettings | None = None,
) -> Iterator[ApiTestApp]:
    """Yields a `TestClient` over the production app.

    `api_settings` defaults to `hermetic_api_settings()`, never an
    env-reading `ApiSettings()`.

    A generator (not a plain function) so the `StubIdp`'s HTTP server is
    shut down deterministically rather than at GC time.
    """
    with running_stub_idp() as idp:
        key = shared_rsa_key()
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

        def _scoped_session() -> Iterator[Session]:
            """Per-request `SAVEPOINT` - see the module docstring. Deliberately
            not `session_scope()` itself: that opens its own `Session` on its
            own connection, which would step outside this fixture's shared,
            rolled-back-at-teardown transaction entirely."""
            with session.begin_nested():
                yield session

        # issue #240: overridden here, in the builder, rather than per test
        # file - the precedent is `get_token_verifier` immediately below.
        # `get_terminology_client` is `@lru_cache`d exactly like it and
        # FastAPI keys overrides on the function object, so doing it once
        # here makes every present and future app test offline **by
        # construction** (NFR-37) rather than by a test author remembering
        # to override it themselves. `create_app`'s own eager
        # `get_terminology_client()` call still runs first and builds a
        # real, unused `OntoserverClient` - harmless, since construction
        # opens no socket (see that function's own docstring).
        terminology_client = StubTerminologyClient()

        # `auth_settings=settings`, not left to `create_app`'s
        # `get_auth_settings()` default: that default is process-wide
        # `@lru_cache`d, so a test overriding `mfa_acr_values` here would
        # otherwise build a step-up challenge from whichever `AuthSettings`
        # happened to be cached first, not from this test's own settings.
        app = create_app(settings=api_settings or hermetic_api_settings(), auth_settings=settings)
        app.dependency_overrides[get_session] = _scoped_session
        app.dependency_overrides[get_token_verifier] = lambda: verifier
        app.dependency_overrides[get_auth_settings] = lambda: settings
        app.dependency_overrides[get_terminology_client] = lambda: terminology_client

        # raise_server_exceptions=False so a handler-mapped error is
        # observed as the HTTP response a real client would see, not
        # re-raised into the test.
        with TestClient(app, raise_server_exceptions=False) as client:
            yield ApiTestApp(
                app=app,
                client=client,
                idp=idp,
                key=key,
                session=session,
                terminology=terminology_client,
            )
