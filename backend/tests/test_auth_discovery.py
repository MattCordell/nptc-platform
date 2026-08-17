"""NFR-07 OIDC discovery refusals (issue #43).

Offline, no Docker: a local `ThreadingHTTPServer` (`auth_jwt_support.StubIdp`)
stands in for Keycloak's discovery document.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from nptc.auth.discovery import resolve_jwks_url
from nptc.auth.errors import SigningKeyUnavailableError, TokenIssuerError
from nptc.auth.tokens import TokenVerifier
from nptc.settings import AuthSettings

_support_spec = importlib.util.spec_from_file_location(
    "_test_auth_discovery_support", Path(__file__).parent / "auth_jwt_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
running_stub_idp = _support.running_stub_idp


@pytest.mark.req("NFR-07")
def test_jwks_uri_taken_from_discovery() -> None:
    with running_stub_idp() as stub, httpx.Client() as client:
        jwks_url = resolve_jwks_url(stub.issuer_url, client=client)

    assert jwks_url == stub.jwks_url


@pytest.mark.req("NFR-07")
def test_issuer_mismatch_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The requested issuer must match the discovery document's own
    `issuer` field exactly - a document is fetched before anything is
    verified, so if it could name a different issuer than the one asked
    for, the whole verification chain would be unanchored."""
    requested_issuer = "http://127.0.0.1/realms/nptc"

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {
                "issuer": "http://127.0.0.1/realms/other",
                "jwks_uri": "http://127.0.0.1/realms/other/protocol/openid-connect/certs",
            }

    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", lambda _url: _Response())
        with pytest.raises(TokenIssuerError):
            resolve_jwks_url(requested_issuer, client=client)


@pytest.mark.req("NFR-07")
def test_cross_origin_jwks_uri_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_stub_idp() as stub, httpx.Client() as client:

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, str]:
                return {
                    "issuer": stub.issuer_url,
                    "jwks_uri": "http://attacker.example/certs",
                }

        monkeypatch.setattr(client, "get", lambda _url: _Response())

        with pytest.raises(TokenIssuerError):
            resolve_jwks_url(stub.issuer_url, client=client)


@pytest.mark.req("NFR-21")
def test_plain_http_non_localhost_issuer_is_refused_without_contacting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is checked *before* the request is made - a client
    whose `get` raises if called at all proves the refused issuer is
    never actually contacted over cleartext."""
    issuer = "http://keycloak.internal/realms/nptc"

    def _never_called(_url: str) -> None:
        raise AssertionError("client.get must not be called for a refused issuer")

    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", _never_called)
        with pytest.raises(TokenIssuerError):
            resolve_jwks_url(issuer, client=client)


@pytest.mark.req("NFR-07")
def test_transport_failure_is_wrapped() -> None:
    """A connection failure must map to `SigningKeyUnavailableError`, not
    propagate a raw `httpx.ConnectError` - the whole point of
    `nptc.auth.errors` is one exception family a caller can catch."""
    with httpx.Client() as client, pytest.raises(SigningKeyUnavailableError):
        resolve_jwks_url("http://127.0.0.1:1/realms/nptc", client=client)


@pytest.mark.req("NFR-07")
def test_non_json_response_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> None:
            raise ValueError("not json")

    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", lambda _url: _Response())
        with pytest.raises(SigningKeyUnavailableError):
            resolve_jwks_url("http://127.0.0.1/realms/nptc", client=client)


@pytest.mark.req("NFR-07")
def test_non_object_document_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[object]:
            return []

    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", lambda _url: _Response())
        with pytest.raises(SigningKeyUnavailableError):
            resolve_jwks_url("http://127.0.0.1/realms/nptc", client=client)


@pytest.mark.req("NFR-07")
def test_document_missing_jwks_uri_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer = "http://127.0.0.1/realms/nptc"

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"issuer": issuer}

    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", lambda _url: _Response())
        with pytest.raises(SigningKeyUnavailableError):
            resolve_jwks_url(issuer, client=client)


@pytest.mark.req("NFR-07")
def test_plain_http_localhost_issuer_is_allowed() -> None:
    with running_stub_idp() as stub, httpx.Client() as client:
        assert stub.issuer_url.startswith("http://127.0.0.1")
        jwks_url = resolve_jwks_url(stub.issuer_url, client=client)

    assert jwks_url == stub.jwks_url


@pytest.mark.req("NFR-07")
def test_explicit_jwks_url_bypasses_discovery() -> None:
    """`NPTC_JWKS_URL` set means `TokenVerifier.from_settings` never
    attempts to fetch a discovery document at all - proven here by
    pointing `oidc_issuer` at a host discovery would have to fail to
    reach, and confirming construction succeeds anyway."""
    settings = AuthSettings(
        oidc_issuer="http://issuer.invalid.example/realms/nptc",
        jwks_url="http://127.0.0.1:0/never-fetched",
    )

    verifier = TokenVerifier.from_settings(settings)

    assert verifier.issuer == settings.oidc_issuer


def test_discovery_response_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    with running_stub_idp() as stub, httpx.Client() as client:
        response = client.get(f"{stub.issuer_url}/.well-known/openid-configuration")
        body = json.loads(response.text)

        assert body["jwks_uri"] == stub.jwks_url
