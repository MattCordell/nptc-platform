# Server-side JWT verification (NFR-07)

## What this is

`nptc.auth.tokens.TokenVerifier` is the one place in this repository that turns a bearer
token into an `OidcIdentityClaims` (issue #42's shape). `nptc.auth.authentication.authenticate`
is the seam into #42's `resolve_user_for_claims`: it verifies first, and only then resolves.
NFR-07's own second sentence is the design constraint everything below serves: *"Authorisation
decisions are made server-side from the internal user record, never from claims in the token.
A token proves who the user is; the database decides what they may do."* Authorisation itself
(NFR-20) is #44's; this module's entire job is producing a claims value nothing has trusted
before its signature, issuer, audience and expiry were all checked.

## Verification order

`TokenVerifier.verify` runs these checks in this order, and every failure raises a subclass of
`nptc.auth.errors.TokenError` — there is no path from "verification failed" to "treat as
valid":

| Step | Check | Raises |
|---|---|---|
| 1 | The header's `alg` is in the allowlist (`["RS256"]`), checked before any key lookup | `TokenInvalidError` |
| 2 | A signing key is obtained for the header's `kid` | `SigningKeyUnavailableError` |
| 3 | Signature verifies, `iss`/`aud`/`exp` match, required claims (`iss`,`sub`,`aud`,`exp`,`iat`) present | `TokenInvalidError` / `TokenIssuerError` / `TokenAudienceError` / `TokenClaimsError` / `TokenExpiredError` |
| 4 | The verified payload's `typ` claim is `"Bearer"` | `TokenInvalidError` |
| 5 | `sub` is non-blank | `TokenClaimsError` |

**The header selects a key; the key proves the token.** `jwt.get_unverified_header` reads the
header without checking anything — it is used only to pick which `alg` and which JWKS key to
try. No claim is ever read from the header as if it were trustworthy; every claim
`OidcIdentityClaims` carries comes from the payload *after* `jwt.decode` has verified the
signature. This is also why the `typ` discriminator (step 4) is checked against the payload,
not the header: decoding a real access token from the pinned Keycloak image
(`test_keycloak_jwt.py`) showed the JOSE header's own `typ` is `"JWT"` for both access and ID
tokens — Keycloak's Bearer/ID distinction is a payload claim. Checking an unverified header
field for a security-relevant decision would be exactly the mistake this design otherwise
avoids, so the check waits for the verified payload even though it costs one step's position
in the list above.

The algorithm allowlist (step 1) is what kills two confusions at once: `alg: none` (no
signature at all) and an RS256 realm's public key replayed as an HS256 secret (PyJWT itself
refuses a PEM-shaped HMAC key, so `test_auth_token_verification.py`'s equivalent test forges the
token by hand to actually exercise the check `_ALGORITHMS` enforces). Both are checked against
the header *before* any JWKS request, so a malicious `alg` never causes network traffic either.

## JWKS retrieval and the refresh cooldown

`nptc.auth.jwks.SigningKeys` wraps `jwt.PyJWKClient`, adding two things it does not itself
guarantee:

- **A fallback that survives cache expiry.** `PyJWKClient`'s own cache holds only the most
  recent fetch; once its `lifespan` elapses, the next lookup re-fetches and *raises* on
  failure — even for a `kid` this process has already seen and validated. `SigningKeys` keeps
  its own `dict[str, PyJWK]` of every key that has ever resolved successfully and falls back to
  it when the live fetch fails. `pyjwt>=2.13` (see `backend/pyproject.toml`'s dependency
  comment) is what stops a *failed* fetch from wiping `PyJWKClient`'s own cache
  (GHSA-fhv5-28vv-h8m8 — an earlier PyJWT wrote its fetch result in a `finally:` block, so a
  failed fetch cached `None`); this fallback map is what stops an *expired* cache plus a down
  IdP from rejecting a token this process could still verify.
- **A refresh cooldown.** `PyJWKClient` re-fetches the whole JWKS on any unrecognised `kid`, so
  an unauthenticated caller spraying random `kid` values makes the backend hammer Keycloak. An
  unknown `kid` seen within `NPTC_JWKS_REFRESH_COOLDOWN_SECONDS` of the last refresh attempt is
  refused with zero HTTP requests; genuine key rotation still lands within one cooldown window.

Never a bypass: there is no code path from "no key could be obtained" to "accept the token
anyway".

## OIDC discovery

`nptc.auth.discovery.resolve_jwks_url` fetches `{issuer}/.well-known/openid-configuration` and
returns its `jwks_uri`, refusing three ways a discovery document could misdirect trust before
anything is verified:

- the document's own `issuer` does not match the issuer requested.
- `jwks_uri` is not same-origin (scheme/host/port) with the issuer.
- the issuer is plain `http` on a non-localhost host (NFR-21) — Keycloak's dev stack is
  http-on-localhost, so that one case is allowed and documented, not a loophole.

`NPTC_JWKS_URL` skips discovery entirely, for air-gapped deployments and so the offline test
suite only ever needs one local HTTP endpoint per test.

## Fail-closed configuration

`AuthSettings.oidc_issuer` defaults to `""`, matching `trusted_issuers`' existing posture: an
empty issuer cannot construct a `TokenVerifier` at all
(`TokenVerifier.from_settings` raises `ValueError`), so a missing configuration refuses every
token rather than silently accepting one whose issuer was never actually checked.
`oidc_audience` defaults to `"nptc-api"` because that value is fixed by the committed realm
(ADR-0014), not by a deployment.

## The seam into #42

```python
def authenticate(session, token, *, verifier, trusted_issuers):
    claims = verifier.verify(token)  # raises on any failure
    return resolve_user_for_claims(session, claims, trusted_issuers=trusted_issuers)
```

`authenticate` never reaches `resolve_user_for_claims` with an unverified claim —
`test_auth_authenticate.py::test_an_invalid_token_raises_and_leaves_no_rows_behind` is the
concrete form of that: an invalid token must not create or touch a single `app_user`/
`user_identity` row.

## Testing

- `test_token_verification_guard.py` — an `ast` guard, no network, with its own positive
  control: `jwt.decode` is only ever called from `nptc/auth/tokens.py`; `jwt.get_unverified_header`
  only from `tokens.py`/`jwks.py`; no call anywhere passes `verify_signature: False` or
  `verify=False`; every `algorithms=` argument is the module constant or a literal list free of
  `none`/`HS*`.
- `test_auth_discovery.py`, `test_auth_jwks_cache.py`, `test_auth_token_verification.py` —
  offline, no Docker: a local `ThreadingHTTPServer` (`auth_jwt_support.py`) stands in for
  Keycloak's discovery document and JWKS endpoint, so the server's own request counter can be
  asserted directly for the rotation, outage and cooldown cases.
- `test_auth_authenticate.py` (`@pytest.mark.integration`) — a real Postgres via the existing
  `app_db` fixture, joined with a locally-signed token.
- `test_keycloak_jwt.py` (`@pytest.mark.integration`) — a **real** Keycloak-minted token: the
  pinned image, a disposable-container-only `directAccessGrantsEnabled` flip and test user (the
  committed realm file is never touched — its own offline test still asserts
  `directAccessGrantsEnabled: false`), a password grant, and a `TokenVerifier` built from the
  container's own discovery document. Proves the audience mapper, issuer and JWKS wiring
  against the real thing, not a locally-signed stand-in — and is what surfaced the header-`typ`
  correction above; nothing offline could have.

## Not implemented here

- Authorisation (NFR-20) — every check here answers "who is this", never "may they do this".
  That is #44's permission framework, keyed off the `Resolution` this module produces.
- A FastAPI dependency wiring `authenticate` into a request — #41/#142/#143's `session.py` and
  router layer.
- Token refresh and session revocation lists.
