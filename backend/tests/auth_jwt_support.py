"""Shared, offline JWT/JWKS test support for issue #43 (NFR-07).

Not a test module itself (no ``test_`` prefix, so pytest never collects
it) - imported by ``test_auth_discovery.py``, ``test_auth_jwks_cache.py``
and ``test_auth_token_verification.py`` the same way those files import
``conftest.py``'s helpers: by file path, since ``backend/tests`` has no
``__init__.py`` and pytest runs with ``--import-mode=importlib``.

Serves a real ``ThreadingHTTPServer`` bound to ``127.0.0.1`` rather than a
mock transport: ``PyJWKClient`` fetches over ``urllib``, so a real local
endpoint is both the only option and lets tests assert on the server's own
request counter (the JWKS-outage and refresh-cooldown cases need exactly
that).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jwt.algorithms import RSAAlgorithm

REALM_PATH = "/realms/nptc"
DISCOVERY_PATH = f"{REALM_PATH}/.well-known/openid-configuration"
CERTS_PATH = f"{REALM_PATH}/protocol/openid-connect/certs"


def generate_rsa_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StubIdp:
    """A local, in-process stand-in for Keycloak's discovery document and
    JWKS endpoint. Every JWKS request increments `request_count`; setting
    `fail = True` makes the JWKS endpoint (not discovery) return a 500."""

    def __init__(self) -> None:
        self.keys: dict[str, RSAPrivateKey] = {}
        self.request_count = 0
        self.fail = False
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def _write_json(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == DISCOVERY_PATH:
                    self._write_json(
                        200,
                        {"issuer": stub.issuer_url, "jwks_uri": stub.jwks_url},
                    )
                    return
                if self.path == CERTS_PATH:
                    stub.request_count += 1
                    if stub.fail:
                        self._write_json(500, {"error": "jwks unavailable"})
                        return
                    keys = [
                        {
                            **RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
                            "kid": kid,
                            "use": "sig",
                            "alg": "RS256",
                        }
                        for kid, key in stub.keys.items()
                    ]
                    self._write_json(200, {"keys": keys})
                    return
                self._write_json(404, {"error": "not found"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def issuer_url(self) -> str:
        return f"{self.base_url}{REALM_PATH}"

    @property
    def jwks_url(self) -> str:
        return f"{self.base_url}{CERTS_PATH}"

    def add_key(self, kid: str, key: RSAPrivateKey) -> None:
        self.keys[kid] = key

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@contextmanager
def running_stub_idp() -> Iterator[StubIdp]:
    stub = StubIdp()
    try:
        yield stub
    finally:
        stub.close()


def mint_token(
    key: RSAPrivateKey,
    *,
    kid: str,
    issuer: str,
    audience: str | list[str] = "nptc-api",
    subject: str = "auth0|test-subject",
    alg: str = "RS256",
    typ: str = "Bearer",
    expires_in: float = 300.0,
    extra_claims: dict[str, object] | None = None,
    extra_headers: dict[str, object] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "iat": now,
        "exp": now + int(expires_in),
        "email": "test-user@example.test",
        "email_verified": True,
        "preferred_username": "test-user",
        "name": "Test User",
        # Keycloak's own `typ` discriminator (Bearer/ID) is a *payload*
        # claim, not the JOSE header's `typ` - confirmed empirically
        # against the pinned Keycloak image (see test_keycloak_jwt.py).
        "typ": typ,
    }
    if extra_claims:
        claims.update(extra_claims)

    headers: dict[str, object] = {"kid": kid}
    if extra_headers:
        headers.update(extra_headers)

    return jwt.encode(claims, key, algorithm=alg, headers=headers)
