"""NFR-07 acceptance criteria 3 (rotation) and 4 (fails closed, does not
discard valid cached keys) for `nptc.auth.jwks.SigningKeys` (issue #43).

Offline, no Docker: a local `ThreadingHTTPServer`
(`auth_jwt_support.StubIdp`) stands in for Keycloak's JWKS endpoint, so the
server's own request counter can be asserted directly rather than assumed.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import jwt
import pytest

from nptc.auth.errors import SigningKeyUnavailableError, TokenInvalidError
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
def test_malformed_token_is_rejected_directly() -> None:
    with running_stub_idp() as stub:
        keys = SigningKeys(stub.jwks_url)

        with pytest.raises(TokenInvalidError):
            keys.signing_key_for("not.a.jwt")


@pytest.mark.req("NFR-07")
def test_token_with_no_kid_is_refused() -> None:
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        keys = SigningKeys(stub.jwks_url)
        # jwt.encode refuses a non-string `kid`, so a header with none at
        # all (rather than mint_token's usual `kid=...`) is the only way
        # to produce this case.
        token = jwt.encode(
            {"iss": stub.issuer_url, "sub": "s", "aud": "nptc-api", "iat": 0, "exp": 9_999_999_999},
            key,
            algorithm="RS256",
        )

        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(token)


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
def test_revoked_key_is_refused_even_though_a_different_key_is_still_published() -> None:
    """The realistic rotation-away shape: `key-1` is revoked but `key-2`
    is still published, so the lookup takes the `PyJWKClientError` "no
    matching kid" branch, not the empty-set (`PyJWKSetError`) one covered
    separately below. Falling back here would keep accepting tokens
    signed by a key the IdP has explicitly retired - distinct from an
    unreachable endpoint (the other tests in this module), where falling
    back is exactly the point."""
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        # A short cache lifespan, plus an actual sleep past it, forces the
        # next lookup to re-fetch rather than ever serve PyJWKClient's own
        # cache, so the revocation is genuinely observed.
        keys = SigningKeys(stub.jwks_url, cache_seconds=0.01)
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        keys.signing_key_for(token)

        del stub.keys["key-1"]
        stub.add_key("key-2", generate_rsa_key())
        time.sleep(0.05)

        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(token)


@pytest.mark.req("NFR-07")
def test_revoked_key_is_refused_when_the_whole_set_is_now_empty() -> None:
    """The other, separate PyJWT exception family (`PyJWKSetError`) for
    an empty JWKS - must refuse the same way as the non-empty-but-missing
    case above, not silently fall back."""
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        keys = SigningKeys(stub.jwks_url, cache_seconds=0.01)
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        keys.signing_key_for(token)

        del stub.keys["key-1"]
        time.sleep(0.05)

        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(token)


@pytest.mark.req("NFR-07")
def test_known_kid_outage_skips_repeated_retries_within_cooldown() -> None:
    """Once a live fetch has failed for a known kid, a subsequent lookup
    within the cooldown window must not re-attempt the network call at
    all - otherwise every verification during an outage would block on
    `timeout_seconds` even though the key is already cached, turning an
    IdP outage into a request-latency outage."""
    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        keys = SigningKeys(stub.jwks_url, cache_seconds=0.01, refresh_cooldown_seconds=60.0)
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        keys.signing_key_for(token)
        time.sleep(0.05)

        stub.fail = True
        # First failure: falls back, and starts the outage cooldown.
        resolved = keys.signing_key_for(token)
        assert resolved.key_id == "key-1"
        request_count_after_first_failure = stub.request_count

        # Still within the cooldown: no further request, same cached key.
        resolved_again = keys.signing_key_for(token)

        assert resolved_again.key_id == "key-1"
        assert stub.request_count == request_count_after_first_failure


@pytest.mark.req("NFR-07")
def test_fallback_key_expires_after_max_fallback_age() -> None:
    """`_known_keys` is not a permanent fallback: once an outage outlasts
    `max_fallback_age_seconds`, a previously-valid key is treated as
    unavailable rather than trusted forever."""
    clock = {"now": 0.0}

    with running_stub_idp() as stub:
        key = generate_rsa_key()
        stub.add_key("key-1", key)
        keys = SigningKeys(
            stub.jwks_url,
            cache_seconds=0.01,
            max_fallback_age_seconds=60.0,
            monotonic=lambda: clock["now"],
        )
        token = mint_token(key, kid="key-1", issuer=stub.issuer_url)

        keys.signing_key_for(token)
        time.sleep(0.05)

        stub.fail = True
        clock["now"] += 30.0
        # Still within the fallback window - the known key still verifies.
        resolved = keys.signing_key_for(token)
        assert resolved.key_id == "key-1"

        clock["now"] += 31.0
        # Past the fallback window now - refused, not silently trusted.
        with pytest.raises(SigningKeyUnavailableError):
            keys.signing_key_for(token)


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
