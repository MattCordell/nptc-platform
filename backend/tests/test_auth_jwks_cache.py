"""NFR-07 acceptance criteria 3 (rotation) and 4 (fails closed, does not
discard valid cached keys) for `nptc.auth.jwks.SigningKeys` (issue #43).

Offline, no Docker: a local `ThreadingHTTPServer`
(`auth_jwt_support.StubIdp`) stands in for Keycloak's JWKS endpoint, so the
server's own request counter can be asserted directly rather than assumed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from nptc.auth.errors import SigningKeyUnavailableError
from nptc.auth.jwks import SigningKeys

_support_spec = importlib.util.spec_from_file_location(
    "_test_auth_jwks_cache_support", Path(__file__).parent / "auth_jwt_support.py"
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
running_stub_idp = _support.running_stub_idp
generate_rsa_key = _support.generate_rsa_key
mint_token = _support.mint_token


@pytest.mark.req("NFR-07")
def test_rotation_verifies_through_the_same_instance() -> None:
    """`monotonic` is injected (rather than a real sleep) specifically so
    "one cooldown window later" is testable directly - two unknown `kid`s
    seen back-to-back fall in the *same* window by design (that's the
    point of the cooldown), so this fast-forwards past it rather than
    asserting instantaneous rotation."""
    clock = {"now": 0.0}

    with running_stub_idp() as stub:
        old_key = generate_rsa_key()
        stub.add_key("key-1", old_key)
        keys = SigningKeys(stub.jwks_url, monotonic=lambda: clock["now"])

        old_token = mint_token(old_key, kid="key-1", issuer=stub.issuer_url)
        keys.signing_key_for(old_token)

        new_key = generate_rsa_key()
        stub.add_key("key-2", new_key)
        new_token = mint_token(new_key, kid="key-2", issuer=stub.issuer_url)
        clock["now"] += 31.0  # past the default 30s cooldown

        # No restart, no new SigningKeys instance - the same object must
        # pick up the rotated key.
        resolved = keys.signing_key_for(new_token)

        assert resolved.key_id == "key-2"


@pytest.mark.req("NFR-07")
def test_outage_after_one_successful_fetch_still_verifies_the_known_key() -> None:
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        # A short cache lifespan forces the next lookup to re-fetch rather
        # than serve PyJWKClient's own cache, so the outage is genuinely
        # exercised rather than masked by an unexpired cache.
        keys = SigningKeys(stub.jwks_url, cache_seconds=0.01)
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        keys.signing_key_for(token)

        stub.fail = True
        resolved = keys.signing_key_for(token)

        assert resolved.key_id == "key-1"


@pytest.mark.req("NFR-07")
def test_outage_with_nothing_ever_cached_fails_closed() -> None:
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        keys = SigningKeys(stub.jwks_url)
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        stub.fail = True

        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(token)


@pytest.mark.req("NFR-07")
def test_unknown_kid_within_cooldown_issues_zero_http_requests() -> None:
    """A prior lookup - even a successful one, for a different `kid` -
    starts the cooldown, so an attacker spraying `kid`s right after a
    legitimate login is still refused pre-flight. `monotonic` is injected
    to fast-forward past the warm-up lookup's own cooldown window, so the
    first unknown-`kid` attempt below is genuinely the one under test."""
    clock = {"now": 0.0}

    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        keys = SigningKeys(
            stub.jwks_url, refresh_cooldown_seconds=60.0, monotonic=lambda: clock["now"]
        )
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)
        keys.signing_key_for(token)
        clock["now"] += 61.0
        baseline_request_count = stub.request_count

        unknown_token = mint_token(key, kid="unknown-kid", issuer=stub.issuer_url)

        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(unknown_token)
        request_count_after_first_unknown = stub.request_count
        assert request_count_after_first_unknown > baseline_request_count

        # Same unknown kid, still within the cooldown: no further request.
        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(unknown_token)

        assert stub.request_count == request_count_after_first_unknown


@pytest.mark.req("NFR-07")
def test_repeated_verification_of_the_same_token_issues_one_fetch() -> None:
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        keys = SigningKeys(stub.jwks_url)
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        for _ in range(5):
            keys.signing_key_for(token)

        assert stub.request_count == 1
