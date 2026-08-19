# ADR-0021: Browser-side PKCE login, in-memory tokens, and silent renewal

**Status:** Accepted
**Date:** 2026-08-19

## Context

Issue #41 is the login round-trip that makes the identity chain real. Everything either
side of it has landed: ADR-0014 committed the Keycloak realm, ADR-0016 built the
server-side Bearer verifier, ADR-0019 built the permission framework, and ADR-0020
reserved the frontend seams (`useAuthStatus`, `RequireAuth`, and the `/sign-in`,
`/sign-out`, `/register`, `/auth/callback` routes). NFR-01 and NFR-02 sat at `planned`
and `in-progress` purely because nothing exercised them.

Two architectures were genuinely available, and the issue's own title ("frontend
initiation, backend callback") named the one this ADR rejects.

- **Backend callback (a BFF).** The browser redirects to Keycloak; Keycloak redirects to
  a backend `/auth/callback`, which performs the exchange and sets an httpOnly session
  cookie. The browser never sees a token.
- **Browser-side exchange (an SPA public client).** The browser performs the PKCE
  exchange itself and sends the resulting access token to the API as a Bearer credential.

**Decided with the maintainer:** the browser-side exchange, with silent `prompt=none`
renewal rather than a refresh token.

## Decision

### The browser performs the exchange

Every artifact already committed points this way, and the issue title predates all of
them:

- **The realm.** `nptc-frontend` is `publicClient: true` with
  `pkce.code.challenge.method: S256`, its `redirectUris` and `webOrigins` derive from
  `${NPTC_FRONTEND_BASE_URL}` — the *frontend's* origin — and so does its
  `post.logout.redirect.uris` (ADR-0014).
- **The route table.** `route-tree.ts` already reserved a *frontend* `/auth/callback`
  route, tagged issue #41. A backend callback would leave it vestigial.
- **The audience mapper.** `nptc-api-audience` sets `access.token.claim: true` and
  `id.token.claim: false` — the access token is minted to be sent to the API.
- **The verifier.** `nptc.auth.tokens.TokenVerifier` requires payload `typ == "Bearer"`,
  and `authenticate_identity` takes a bare token string.
- **The step-up contract.** `docs/architecture/permissions.md` pre-specifies
  `403` + `WWW-Authenticate: Bearer error="insufficient_user_authentication",
  acr_values="2"` (RFC 9470) — a Bearer mechanism a cookie session does not speak.

NFR-01 is satisfied exactly either way ("the OIDC authorisation code flow with PKCE, and
never handles credentials"). PKCE is what makes a secretless exchange safe for a public
client, and `frontend/scripts/assert-no-secret-in-bundle.mjs` asserts the absence of a
secret against the built assets rather than by inspection.

### Tokens live in memory only; no refresh token is requested

The access token and ID token are held in React state inside `AuthProvider` and are never
written to `localStorage` or `sessionStorage`. Only the in-flight login transaction (the
`state`, `code_verifier`, `nonce` and return path) touches `sessionStorage`, and it is
read-and-deleted in one step so a replayed callback finds nothing.

No refresh token is requested at all. A refresh token in the browser is the one
long-lived credential genuinely worth stealing through XSS; `prompt=none` against
Keycloak's own SSO cookie does the same job — surviving both an expiring access token and
a page reload — without one.

### The `nonce` is sent but not validated

`buildAuthorizeUrl` generates and sends a `nonce`, and Keycloak binds it into the ID
token — but nothing checks it on return, and this is deliberate rather than an oversight.

A `nonce` check is only meaningful against an ID token whose signature has been verified,
and this application never verifies or trusts an ID token. The access token is the
credential and the API verifies it server-side (NFR-07); the ID token is used for exactly
one thing, `id_token_hint` on logout, where a forged one buys an attacker nothing but
their own logout. Decoding an unverified ID token in order to compare a claim would look
like a security check while providing none.

It is still sent, so that the check is available to whoever first has a reason to trust an
ID token client-side.

### Silent renewal via `prompt=none` in a hidden iframe

`src/auth/silent-renew.ts` performs a `prompt=none` authorization request in a hidden
iframe. It resolves with a code (renewing the session) or raises
`InteractionRequiredError` (the SSO session has ended, so the user is simply signed out).
It is injected into `AuthProvider` as a parameter, because jsdom performs no real
navigation and the real implementation can only ever hit its own timeout under test.

### The realm's browser flow is restructured into LoA 1 and LoA 2

**This is a correction to ADR-0014's realm, found by the first test to actually drive a
login.** As committed, `nptc browser forms` ran `auth-username-password-form` (REQUIRED)
followed by a CONDITIONAL subflow whose only condition was
`conditional-level-of-authentication` at level 2. With no satisfiable LoA-1 branch
anywhere in the flow, Keycloak resolved *every* authorization request to LoA 2, required
`auth-otp-form`, and — a new user having no OTP credential — forced `CONFIGURE_TOTP`.

Verified against the pinned image: this happened with no `acr_values`, with
`acr_values=1`, and with `acr_values=2` alike, and adding an `acr.loa.map` alone did not
change it. Setting `default.acr.values` to `["1"]` makes Keycloak refuse to boot
(`Default ACR values need to contain values specified in the ACR-To-Loa mapping or number
levels from set realm browser flow`), because level 1 existed nowhere in the flow.

The effect was that every user had to enrol TOTP before they could log in at all, which
contradicts NFR-02's "From the user's perspective this is ordinary username and password
registration" and is stricter than NFR-06, which makes MFA mandatory for *administrators*.

The fix is Keycloak's documented step-up structure: the password form moves into its own
CONDITIONAL subflow gated at `loa-condition-level: 1`, the OTP subflow stays gated at
level 2, and a realm-level `acr.loa.map` of `{"1": 1, "2": 2}` makes both levels
nameable. Confirmed against a real container: an ordinary sign-in now completes and
exchanges, while `acr_values=2` still demands OTP.

## Consequences

- NFR-01 and NFR-02 move to `implemented`. `backend/tests/test_keycloak_pkce_login.py`
  drives a real PKCE round trip against the pinned Keycloak image, covering the two
  acceptance criteria that belong to the authorisation server rather than to us: a
  mismatched `code_verifier` is refused, and a replayed authorisation code fails.
- `nptc.api` exists: an app factory, the `current_principal`/`permission_dep`
  dependencies ADR-0016 and ADR-0019 deferred, the auth-error-to-HTTP mapping, and
  `GET /api/v1/auth/me`. `nptc.db.session` exists for the same reason.
- CORS is now load-bearing. The browser holds the token and calls the API cross-origin,
  so `ApiSettings.frontend_base_url` names exactly one allowed origin — never `*`.
- Two new `VITE_*` variables, the first in this repo: `VITE_OIDC_ISSUER` and
  `VITE_OIDC_CLIENT_ID`. Both are public facts about a public client, and the build
  asserts no third, secret-shaped one has crept in.

### Accepted residual risk

**A logout does not invalidate an already-issued access token.** RP-initiated logout ends
the Keycloak SSO session, so returning to `/sign-in` genuinely re-prompts rather than
silently resuming — that half is asserted in the integration test. But a token minted
before the logout stays cryptographically valid until its own `exp`; the realm's
`accessTokenLifespan` is 300s, so the window is at most five minutes.

Closing it entirely requires either the BFF rejected above or a token-introspection call
on every request. Both were judged disproportionate at this stage, and this is recorded
here rather than left for someone to discover.

**Tokens in memory remain exposed to XSS** for the lifetime of the tab. This is the
trade the BFF alternative would have removed. It is mitigated by holding nothing in
persistent storage, by requesting no refresh token, by the 300s token lifespan, and by
NFR-23's restrictive CSP.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| **Backend callback (BFF) with an httpOnly session cookie** | Fully closes the post-logout window above, and is what the issue title literally asked for. But it contradicts the committed realm (redirect URIs, web origins and post-logout URI all name the frontend), makes ADR-0020's reserved `/auth/callback` route dead, supersedes the RFC 9470 Bearer step-up contract already specified in `docs/architecture/permissions.md`, and adds a session table, a migration, a cookie secret and CSRF defence. The maintainer chose the smaller change that the rest of the system was already built for. |
| **Refresh token held in browser memory** | Fewer round trips than `prompt=none`, and a common SPA pattern. Rejected because it puts a long-lived credential in JavaScript — precisely the asset the in-memory-only decision exists to avoid — for a saving measured in one redirect per five minutes. |
| **Re-prompting the user when the access token expires** | Simplest and safest, but a visible interruption every five minutes of activity is not usable for a curation tool whose users work in long sessions. |
| **Persisting tokens in `localStorage`** | Would survive a reload without any renewal round trip. Rejected: it makes a stolen storage entry outlive the tab, and `prompt=none` already restores a session on cold load. |
| **Setting `default.acr.values: ["1"]` on `nptc-frontend`** | The obvious-looking fix for the TOTP-for-everyone defect. Keycloak refuses to start with it, because level 1 appears nowhere in the browser flow — which is the underlying problem the LoA-1 subflow actually fixes. |
| **Leaving the realm as committed (MFA for every user)** | Would have avoided touching ADR-0014's realm. Rejected because it contradicts NFR-02 and makes registration materially harder than the PRD describes, for a security property NFR-06 only asks of administrators. |

## Follow-ups

- **NFR-14/NFR-45** (privacy notice and versioned terms, presented with positive
  acceptance at registration) are not satisfied: `/register` hands off to Keycloak's own
  registration page, which captures no acceptance. This needs its own issue.
- **The `acr` claim is absent from tokens issued at LoA 1.** It is present when the
  step-up flow runs, which is all `principal_for` needs (an absent `acr` correctly yields
  `mfa_satisfied: false`), but the SPA's own step-up loop — reacting to an
  `insufficient_user_authentication` challenge by re-authenticating with `acr_values=2` —
  is still a frontend follow-up, as `docs/architecture/permissions.md` already notes.
- **ADR-0019's manual verification** that "the token's `acr` claim reads `2` after OTP
  completion" remains manual; scripting a TOTP enrolment round trip was again judged
  disproportionate. What is now automated is that `acr_values=2` demands OTP at all.
