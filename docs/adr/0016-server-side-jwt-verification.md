# ADR-0016: Server-side JWT verification

**Status:** Accepted
**Date:** 2026-08-17

## Context

Issue #40 (ADR-0014) landed a Keycloak realm with a `nptc-api` audience mapper specifically so
this issue would have a real `aud` to verify. Issue #42 (ADR-0015) landed `OidcIdentityClaims`
and `resolve_user_for_claims`, with an explicit comment that this issue produces one of those
from a verified token. Nothing in the repository had ever looked at a JWT before this issue.

**NFR-07 (MUST)** is the requirement: *"All tokens validated server-side on every request:
signature against the realm's JWKS, issuer, audience, and expiry. Authorisation decisions are
made server-side from the internal user record, never from claims in the token. A token proves
who the user is; the database decides what they may do."* The failure this exists to prevent is
blunt: a client editing a token it already holds — extending its own expiry, swapping its
`sub`, presenting `alg: none` — and the backend believing it.

**NFR-06** is also claimed by the originating issue, but the PRD's NFR-06 is MFA (TOTP)
available, and mandatory for administrators. Per the maintainer's decision, this issue covers
only the "available" half (realm-as-code assertions, offline and integration) and moves NFR-06
to `in-progress`; the mandatory-for-administrators half is #44's, since that is where a role
exists to make it mandatory *for*.

**Scope**, confirmed with the maintainer: the verifier plus the join to #42's resolution
(`authenticate(session, token) -> Resolution`) — no FastAPI app, router, dependency or
`session.py`. Those are #41/#142/#143.

## Decision

**`pyjwt[crypto]` plus a thin wrapper, not a hand-rolled JWKS cache and not `python-jose`/
`joserfc`.** PyJWT's `PyJWKClient` already does the two things worth not reimplementing —
parsing a JWK Set and matching `kid` to key — leaving exactly two gaps worth a wrapper over:
surviving cache expiry during an IdP outage, and a refresh cooldown against `kid`-spraying (see
`nptc.auth.jwks.SigningKeys`, and `docs/architecture/token-verification.md`). `python-jose` is
unmaintained upstream past 2021; `joserfc` is capable but would be this repository's only
consumer, with no compensating benefit over PyJWT's much wider adoption and simpler API for the
one thing this issue needs (`decode` plus a `PyJWKClient`).

**`pyjwt[crypto]>=2.13`, not "a recent PyJWT".** Before 2.13, `PyJWKClient.fetch_data` wrote its
result in a `finally:` block, so a failed fetch cached `None` and wiped a perfectly good JWKS
(GHSA-fhv5-28vv-h8m8) — precisely acceptance criterion 4 ("does not fail open", "does not
discard valid cached keys"). The floor is stated with a comment in `backend/pyproject.toml`
naming the CVE, so a future contributor relaxing it sees why not to.

**OIDC discovery, not string-concatenating Keycloak's own `/protocol/openid-connect/certs`
path.** Discovery is one HTTP round trip on `TokenVerifier` construction (not per-request — the
resolved `jwks_url` is cached in the constructed `SigningKeys`), and it is what lets
`NPTC_JWKS_URL` be genuinely optional rather than a required, always-set variable duplicating
information the issuer's own document already carries. `nptc.auth.discovery.resolve_jwks_url`
refuses a document whose own `issuer` doesn't match, whose `jwks_uri` is cross-origin, or whose
issuer is plain `http` on a non-localhost host (NFR-21) — a discovery document is fetched
*before* anything is verified, so if it could redirect trust anywhere, the whole chain would be
unanchored.

**`leeway=0.0`, as a constructor argument, not a hard-coded zero.** NFR-07's acceptance criteria
require an expired token to be rejected, and Keycloak's own `accessTokenLifespan` is 300s (the
committed realm) — there is no clock-skew argument worth a silent grace window by default. Kept
as a constructor parameter (not a bare `0` inline) so a deployment with a known, measured skew
can raise it deliberately, with the change visible at the call site rather than buried in a
default.

**The algorithm allowlist is checked against the header explicitly, before any key lookup —
not relied on implicitly via `jwt.decode(algorithms=...)` alone.** PyJWT would also refuse
`alg: none` and an RS256-key-as-HMAC-secret confusion once `algorithms=` is passed to `decode`,
but stating the check explicitly, first, means a disallowed `alg` never even triggers a JWKS
request — and it is what `test_token_verification_guard.py`'s AST guard can mechanically verify
(no `algorithms=` argument anywhere except the module constant or a literal free of `none`/
`HS*`), rather than relying on every future call site remembering to pass the right argument to
`jwt.decode`.

**Refusing, never degrading, when key material is unavailable.** `SigningKeyUnavailableError`
is a rejection, not a fallback outcome — there is no code path in `nptc.auth` from "no key could
be obtained" to "accept the token anyway". This is the load-bearing distinction between the
JWKS fallback cache (which *does* let a previously-seen key keep verifying during an outage,
deliberately) and every other failure mode, which refuses outright.

**The `typ` discriminator is checked against the verified payload, not the unverified JOSE
header.** The original plan assumed Keycloak stamps the JOSE header's own `typ` as `Bearer` for
access tokens and `ID` for ID tokens. Decoding a real token from the pinned Keycloak image in
`test_keycloak_jwt.py` showed this is wrong for the pinned version: the header's `typ` is
`"JWT"` for both; the `Bearer`/`ID` discriminator is a *payload* claim. Checking a
security-relevant field before the signature is verified would have been exactly the kind of
mistake this design otherwise avoids, so the check was moved to run against the payload after
`jwt.decode` succeeds — caught only because the acceptance criteria required a real
Keycloak-minted token, not a locally-signed stand-in, to be the final proof.

**NFR-06's split**: this issue delivers the "available" half only — `otpPolicyType: totp` (from
ADR-0014) plus a new, explicit `requiredActions` entry for `CONFIGURE_TOTP`, asserted offline
and against a real container's admin API. Today TOTP availability was an *inherited* Keycloak
default; ADR-0014's whole point is that realm behaviour lives in the file, so this pins it
rather than asserting against a default a future Keycloak upgrade could silently change.
Mandatory-for-administrators is left to #44, which is where an administrator role first exists
to make it mandatory *for*.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| `python-jose` | Unmaintained upstream since 2021; PyJWT is actively maintained and already the wider-adopted choice for this exact job. |
| `joserfc` | Capable, but this repository would be its only consumer with no benefit over PyJWT's simpler `decode`/`PyJWKClient` API for what this issue needs. |
| A hand-rolled JWKS cache | `PyJWKClient` already does JWK-Set parsing and `kid` matching correctly; only the outage-survival and refresh-cooldown gaps were worth writing, as a thin wrapper. |
| String-concatenating `{issuer}/protocol/openid-connect/certs` instead of discovery | Works for Keycloak specifically, but duplicates information the issuer's own discovery document already states, and gives up the issuer/cross-origin refusals discovery lets this issue check for free. |
| A non-zero default `leeway` | NFR-07's acceptance criteria require an expired token to be rejected; Keycloak's own 300s access-token lifespan leaves no argument for a default grace window. Kept as a constructor argument for a deployment with a measured, deliberate skew. |
| Relying on `jwt.decode(algorithms=...)` alone to enforce the algorithm allowlist | Correct, but implicit - a future call site could pass the wrong list and nothing would catch it. Checking the header explicitly, first, is both defence in depth and something `test_token_verification_guard.py` can verify mechanically. |
| Checking `typ` against the JOSE header (the original plan) | Empirically wrong against the pinned Keycloak image - both access and ID tokens carry `typ: "JWT"` in the header; the Bearer/ID discriminator is a payload claim, so the check was moved to run only after the signature verifies. |

## Consequences

- NFR-07 moves to `implemented`. NFR-06 moves to `in-progress`, with a note naming #44 for the
  mandatory-for-administrators half. NFR-20 (authorisation server-side) stays `planned` — #44's.
- #44 (permission framework) builds directly on this: it consumes the `Resolution` `authenticate`
  produces and is where an actual authorisation decision, and NFR-06's mandatory-MFA-for-admin
  enforcement, both land.
- #41/#142/#143 (the FastAPI app, `session.py`, the router layer) are the next consumers of
  `TokenVerifier`/`authenticate` — this issue deliberately stops short of wiring either into a
  request.
- `docs/architecture/token-verification.md` records the verification order and the header/
  payload `typ` correction in detail, so a future reader does not have to re-derive it from a
  failing test against a real container.
