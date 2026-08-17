"""NFR-07 `TokenVerifier.verify` rejection cases (issue #43).

One test per rejection, never a combined case - the acceptance criteria
say so explicitly, and it is the convention `test_db_user_model.py`
already follows for the same reason (a failed assertion must not mask a
second one in the same test).

Offline, no Docker: a local `ThreadingHTTPServer`
(`auth_jwt_support.StubIdp`) stands in for Keycloak's JWKS endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from nptc.auth.errors import (
    TokenAudienceError,
    TokenClaimsError,
    TokenExpiredError,
    TokenInvalidError,
    TokenIssuerError,
)
from nptc.auth.jwks import SigningKeys
from nptc.auth.tokens import TokenVerifier

_support_spec = importlib.util.spec_from_file_location(
    "_test_auth_token_verification_support", Path(__file__).parent / "auth_jwt_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
running_stub_idp = _support.running_stub_idp
generate_rsa_key = _support.generate_rsa_key
mint_token = _support.mint_token


def _b64url(payload: dict[str, Any]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=").decode("ascii")
    )


@pytest.fixture
def stub():
    with running_stub_idp() as idp:
        yield idp


@pytest.fixture
def signing_key():
    return generate_rsa_key()


@pytest.fixture
def verifier(stub, signing_key):
    stub.add_key("key-1", signing_key)
    return TokenVerifier(
        issuer=stub.issuer_url,
        audience="nptc-api",
        keys=SigningKeys(stub.jwks_url),
    )


@pytest.mark.req("NFR-07")
def test_a_well_formed_token_is_accepted(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer=stub.issuer_url)

    claims = verifier.verify(token)

    assert claims.issuer == stub.issuer_url
    assert claims.subject == "auth0|test-subject"
    assert claims.email_verified is True


@pytest.mark.req("NFR-07")
def test_altered_payload_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer=stub.issuer_url)
    header_b64, payload_b64, signature_b64 = token.split(".")
    tampered_payload = (
        payload_b64[:-4] + ("A" if payload_b64[-4] != "A" else "B") + payload_b64[-3:]
    )
    tampered_token = f"{header_b64}.{tampered_payload}.{signature_b64}"

    with pytest.raises(TokenInvalidError):
        verifier.verify(tampered_token)


@pytest.mark.req("NFR-07")
def test_token_signed_with_the_wrong_key_is_rejected(stub, verifier) -> None:
    wrong_key = generate_rsa_key()
    token = mint_token(wrong_key, kid="key-1", issuer=stub.issuer_url)

    with pytest.raises(TokenInvalidError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_alg_none_is_rejected(stub, verifier) -> None:
    token = jwt.encode(
        {
            "iss": stub.issuer_url,
            "sub": "auth0|test-subject",
            "aud": "nptc-api",
            "iat": 0,
            "exp": 9_999_999_999,
        },
        key="",
        algorithm="none",
        headers={"kid": "key-1", "typ": "Bearer"},
    )

    with pytest.raises(TokenInvalidError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_expired_token_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer=stub.issuer_url, expires_in=-10.0)

    with pytest.raises(TokenExpiredError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_wrong_issuer_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer="http://not-the-configured-issuer")

    with pytest.raises(TokenIssuerError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_wrong_audience_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer=stub.issuer_url, audience="some-other-api")

    with pytest.raises(TokenAudienceError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_audience_as_a_list_containing_the_expected_value_is_accepted(
    stub, signing_key, verifier
) -> None:
    token = mint_token(
        signing_key,
        kid="key-1",
        issuer=stub.issuer_url,
        audience=["some-other-api", "nptc-api"],
    )

    claims = verifier.verify(token)

    assert claims.subject == "auth0|test-subject"


@pytest.mark.req("NFR-07")
def test_audience_list_without_the_expected_value_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(
        signing_key,
        kid="key-1",
        issuer=stub.issuer_url,
        audience=["some-other-api", "yet-another-api"],
    )

    with pytest.raises(TokenAudienceError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_missing_sub_is_rejected(stub, signing_key, verifier) -> None:
    now = 0
    claims = {
        "iss": stub.issuer_url,
        "aud": "nptc-api",
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode(
        claims, signing_key, algorithm="RS256", headers={"kid": "key-1", "typ": "Bearer"}
    )

    with pytest.raises(TokenClaimsError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_blank_sub_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer=stub.issuer_url, subject="   ")

    with pytest.raises(TokenClaimsError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_typ_id_is_rejected(stub, signing_key, verifier) -> None:
    token = mint_token(signing_key, kid="key-1", issuer=stub.issuer_url, typ="ID")

    with pytest.raises(TokenInvalidError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_hs256_signed_with_the_rsa_public_key_as_the_hmac_secret_is_rejected(
    stub, signing_key, verifier
) -> None:
    """Not in the acceptance criteria list, but the principal failure mode
    of an algorithm allowlist done wrong: an RS256 realm's public key,
    reused as an HMAC secret, must not be accepted just because PyJWT can
    technically verify an HS256 signature against it."""
    public_pem = signing_key.public_key().public_bytes(
        encoding=Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo
    )
    now = 0
    claims = {
        "iss": stub.issuer_url,
        "sub": "auth0|test-subject",
        "aud": "nptc-api",
        "iat": now,
        "exp": now + 300,
    }
    header = {"alg": "HS256", "typ": "Bearer", "kid": "key-1"}
    # jwt.encode itself refuses a PEM-shaped key as an HMAC secret
    # (InvalidKeyError) - this constructs the forged token by hand, the
    # same three base64url segments, to actually exercise the confusion
    # attack `TokenVerifier`'s own `_ALGORITHMS` allowlist exists to stop.
    signing_input = f"{_b64url(header)}.{_b64url(claims)}".encode("ascii")
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    token = f"{signing_input.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"

    with pytest.raises(TokenInvalidError):
        verifier.verify(token)


@pytest.mark.req("NFR-07")
def test_email_verified_string_false_maps_to_false(stub, signing_key, verifier) -> None:
    token = mint_token(
        signing_key,
        kid="key-1",
        issuer=stub.issuer_url,
        extra_claims={"email_verified": "false"},
    )

    claims = verifier.verify(token)

    assert claims.email_verified is False
