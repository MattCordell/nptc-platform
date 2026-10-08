# Authentication: the login round trip

How a user becomes a `Principal` (issue #41). The design decision behind this — and the
alternatives rejected — is [ADR-0021](../adr/0021-browser-side-pkce-login.md). Token
verification itself is [token-verification.md](token-verification.md); what a resolved
principal may then do is [permissions.md](permissions.md).

## The shape

The browser performs the OIDC authorisation code exchange with PKCE. `nptc-frontend` is a
**public client** with no secret, so there is nothing for a backend to hold on its behalf
(NFR-01).

```text
browser  --(1) authorize + code_challenge (S256) -->  Keycloak
browser  <--(2) redirect to /auth/callback?code&state
browser  --(3) POST code + code_verifier ------------>  Keycloak token endpoint
browser  <--(4) access_token (aud: nptc-api) + id_token
browser  --(5) Authorization: Bearer <access_token> -->  NPTC API
```

Step 5 is where [token-verification.md](token-verification.md) takes over: signature,
issuer, audience and expiry, then identity resolution, then permission derivation.

## Browser side (`frontend/src/auth/`)

| Module | Responsibility |
|---|---|
| `config.ts` | `VITE_OIDC_ISSUER` / `VITE_OIDC_CLIENT_ID`; derives the redirect URI from the current origin so it cannot drift from the realm's `redirectUris` |
| `pkce.ts` | `code_verifier`, S256 `code_challenge`, `state`, `nonce` — all from `crypto.getRandomValues`/`crypto.subtle` |
| `discovery.ts` | The realm's `.well-known/openid-configuration`, refusing a document that names a different issuer |
| `transaction.ts` | The in-flight `{state, code_verifier, nonce, redirect}`, in `sessionStorage`, **keyed by `state`** and **read-and-deleted in one step** |
| `flow.ts` | Builds the authorize URL; validates `state` and exchanges the code; builds the logout and registration URLs |
| `silent-renew.ts` | `prompt=none` in a hidden iframe |
| `auth-context.tsx` | The session: tokens in memory, the cold-load probe, renewal, sign-in/out, callback completion, and `stepUp` (a silent re-authentication at a given LoA) |
| `session.ts` | The context object, its value type, `AuthStatus`, `StepUpOutcome`, and `useAuth` |
| `auth-status.ts` | The `useAuthStatus()` seam ADR-0020 reserved, unchanged in shape |
| `step-up.tsx` | `StepUpController` (mounted once in `RootLayout`): reacts to an RFC 9470 challenge with a silent attempt, then an interstitial dialog and interactive fallback — see [ADR-0036](../adr/0036-spa-step-up-loop.md) |

### What the browser is not allowed to decide

Nothing. NFR-20 puts every authorisation decision server-side. `AuthStatus` and the
permission list from `/api/v1/auth/me` decide what the shell *renders*; every one of
those permissions is re-checked at the endpoint that uses it. Hiding a control is
presentation, not access control.

### Four properties worth stating plainly

- **The `state` check *is* the transaction lookup.** Transactions are stored under their
  own `state`, so a callback bearing a `state` this tab never issued simply finds nothing.
  Taking it consumes it, so a replayed callback URL fails too — both without a network
  call.
- **Keyed, not a single slot, because two flows are genuinely concurrent.** A silent
  renewal can be in flight while an interactive sign-in runs. One shared slot meant the
  second to start overwrote the first, and whichever callback arrived failed its state
  check — failing a sign-in that had actually worked, with the outcome decided by
  whichever fetch resolved first.
- **Nothing is persisted but the in-flight transaction.** No token, and no refresh token,
  is ever written to storage — and none is requested. See ADR-0021 for the trade.
- **`AuthStatus` has four values, and `"restoring"` is load-bearing.** Tokens live in
  memory, so a cold load has none even with a live SSO session. Reporting `"signed-out"`
  during that first silent round trip would make `RequireAuth` redirect and `/sign-in`
  begin a full interactive login — taking a signed-in user out of the SPA to fetch a
  session they already had. `RequireAuth`, `/sign-in` and `/register` all treat
  `"restoring"` as "wait".

| `AuthStatus` | Meaning |
|---|---|
| `restoring` | The cold-load probe has not answered yet. Initial value on every load |
| `signed-in` | An access token is held |
| `signed-out` | The probe answered, and there is no session |
| `unavailable` | No configuration, or the provider could not be reached. Cleared as soon as anything succeeds — it used to be sticky for the life of the tab |

The probe lives in `AuthProvider`'s own mount effect rather than a sibling component, so
`"restoring"` is always resolved by whoever owns it; it settles in a `finally`, so a
thrown probe cannot strand the app in a status nothing would move it out of. It is skipped
on `/auth/callback`, where the code exchange about to run is what establishes the session
and a concurrent renewal would only race it.

### Silent renewal needs Keycloak to allow framing

Renewal and the cold-load probe load Keycloak's authorize endpoint in a hidden iframe. Two
conditions must hold, or every renewal waits out `SILENT_RENEW_TIMEOUT_MS` (10 s) and then
counts as "signed out".

- **Keycloak must allow the app to frame it.** Keycloak's default policy is
  `frame-ancestors 'self'`. A browser treats the app and Keycloak as different origins
  whenever their ports differ (`:5173` or `:8081` against `:8080`), so it blocks the frame.
  The realm file sets `browserSecurityHeaders.contentSecurityPolicy` to add
  `${NPTC_FRONTEND_BASE_URL}` to `frame-ancestors`. It repeats Keycloak's other defaults,
  because Keycloak replaces the whole header map when the realm file supplies one. Keycloak
  still sends `X-Frame-Options: SAMEORIGIN`; Chromium ignores it when `frame-ancestors` is
  present, and the silent renewal succeeds there.
- **The browser must send Keycloak's session cookie in the iframe.** That holds when the app
  and Keycloak share a registrable domain, as with `localhost` on any port, or
  `app.example.org` and `sso.example.org`. **Known limitation:** if a deployment serves them
  from different registrable domains, browsers that block third-party cookies refuse the
  cookie. Silent renewal then cannot succeed, so a reload or an expired token ends the
  session. The production overlay (P5) should keep both on one registrable domain.

Once a renewal has been refused with "the SSO session has ended", `getAccessToken` answers
`null` at once instead of opening another iframe for each request. A renewal *fault*, such
as an unreachable discovery document, does not set this, so a later request can still retry.
Signing in or completing the callback clears it.

Two consequences follow, and neither is a bug.

- **A timeout counts as a refusal for an anonymous visitor.** From inside the page, a
  blocked frame and a slow Keycloak look the same (`SilentRenewTimeoutError`). Treating the
  timeout as "signed out" is what stops a blocked frame costing 10 s per request. The price
  is that one slow answer on a cold load leaves the tab anonymous until it reloads. A
  signed-in user's timeout does not set the flag, so their next request retries.
- **An anonymous tab does not notice a sign-in made in another tab.** Before, each request
  would renew, find the new SSO session and switch the tab to signed in. Now the tab stays
  anonymous until it reloads or the visitor signs in from it.

## Server side (`backend/src/nptc/api/`)

| Module | Responsibility |
|---|---|
| `app.py` | `create_app()`: CORS (exactly one origin), exception handlers, routers |
| `dependencies.py` | `current_principal`, `permission_dep`, the per-request `AuditContext`, the session and verifier |
| `errors.py` | `TokenError` → 401; `AuthorisationError` → its own `http_status`; every other domain exception from the `_REFUSALS` table |
| `routers/auth.py` | `GET /api/v1/auth/me` |

`current_principal` runs the chain exactly once per request:

```text
Authorization: Bearer <token>
  -> TokenVerifier.verify        (NFR-07)
  -> resolve_user_for_claims     (NFR-04)
  -> principal_for               (NFR-06, FR-44)
```

Every failure mode raises rather than degrading to anonymous. Presenting a bad token is a
401; presenting none is anonymous. Collapsing those two would make a forged token
indistinguishable from an ordinary public request in any log built from the result.

### 401 versus 403

The pair endpoints most reliably get backwards, so it is fixed in one place:

| Situation | Status | `WWW-Authenticate` |
|---|---|---|
| No credential, permission required | **401** | `Bearer` |
| Credential unreadable, or token invalid/expired | **401** | `Bearer` |
| Authenticated, permission missing | **403** | *(none)* |
| Authenticated, permission held by an MFA-suppressed role | **403** | `Bearer error="insufficient_user_authentication", acr_values="2"` |
| Identity resolves ambiguously | **409** | *(none)* |

`permission_dep` is what converts the first row from a bare `PermissionDeniedError` into
`CredentialRequiredError`: `require_permission` sees only a `Principal`, and an anonymous
one is simply missing the permission — it cannot know a credential was never offered.

Response bodies name neither a role nor an internal identifier (FR-44, NFR-04). The
exception messages do, deliberately, and go to the log instead.

`acr_values` in the fourth row's header is built from `AuthSettings.mfa_acr_values`
(`nptc.api.errors._step_up_challenge`), not the literal `"2"` — so changing the realm's
LoA mapping (`NPTC_MFA_ACR_VALUES`) needs no code change on either side of the loop below.
The CORS middleware in `create_app` also declares `expose_headers=["WWW-Authenticate"]`;
without it a browser hides the header from JavaScript on every cross-origin response, and
the SPA reaction below does nothing, silently, whenever the SPA is not same-origin with
the API (`vite dev` included).

### The SPA's reaction to the fourth row (issue #184, NFR-06)

`ApiError` (`frontend/src/api/unwrap.ts`) carries the response headers, so
`frontend/src/api/step-up.ts`'s `asStepUpChallenge` can recognise the fourth row from any
failed query or mutation. `createQueryClient`'s `QueryCache`/`MutationCache` `onError`
(`frontend/src/api/query-client.ts`) is the one seam this is detected at, regardless of
which screen made the call.

`StepUpController` then:

1. Tries a silent `prompt=none` + the challenge's own `acr_values` first
   (`AuthContextValue.stepUp`, reusing `silent-renew.ts`'s hidden iframe).
2. On success, a refused **query** is refetched in place — no navigation — and
   `useSession`'s own query is invalidated so `StepUpBanner` stops offering to verify
   the moment it no longer needs to. A refused **mutation** is never replayed
   automatically (ADR-0036 records why); the user resubmits.
3. On failure (Keycloak needs interaction), shows a dialog explaining what is about to
   happen, then falls back to `signIn({ acrValues, redirect })` — an ordinary interactive
   redirect, using `signIn`'s existing transaction machinery to carry the return path.
   `StepUpBanner`'s own "Verify now" goes through this same silent-first/dialog path
   (`requestStepUp`), not a direct `signIn`.
4. A `Set<queryHash>` inside the controller blocks a second challenge for the same query
   only while this cycle's own step-up attempt and retry are still in flight — the hash
   is removed once that cycle settles, so a *later*, genuinely new challenge for the same
   query is still offered step-up rather than silently swallowed forever.

`useSession()` (`GET /api/v1/auth/me`) and `StepUpBanner` (shown on every `/admin/*`
screen via `AdminLayout`) let an administrator complete this step before walking into a
403 at all, not only in reaction to one.

### An unhandled error still carries CORS headers

Starlette puts `ServerErrorMiddleware` outside every other layer, `CORSMiddleware`
included. An exception that no handler in `nptc.api.errors` claims would pass through CORS
untouched, and that outer layer would write the 500. The response then had no
`Access-Control-Allow-Origin`, so a cross-origin browser hid the status and the SPA saw an
opaque network error instead of a 500 it could report.

`UnhandledErrorMiddleware` (`nptc.api.unhandled`) sits just inside `CORSMiddleware`. It
logs the exception with its traceback and returns a generic JSON 500, which CORS then
decorates like any other response. The body never carries exception text (NFR-26,
NFR-35), and only the configured frontend origin receives the header. If the response has
already started, the error propagates instead, because a second response cannot follow the
first.

### Audit attribution has two phases

`resolve_user_for_claims` emits `user_identity.created` (and, on a first login,
`user_role.granted`) for a user whose internal id does not exist until those inserts run.
So the resolution uses a bootstrap `AuditContext` with `actor_user_id=None` — carrying the
IP and user agent, so the event is still attributable to a request. Writes made *after*
resolution use `audit_context`, which carries the resolved `user_id` (NFR-08).

On a repeat login (an existing `UserIdentity` row), resolution emits `user_identity.refreshed`
when `email`/`email_verified` actually changed, and `user.renamed` when `display_name`
actually changed — each guarded so an unchanged login emits nothing. The `display_name`
assignment runs *after* the identity's own `record_change`, not before: `append_audit_event`
flushes the session before reading the chain tail, which would otherwise commit away the
attribute history the `user.renamed` diff needs (issue #167).

`request.client.host` is not always an IP (Starlette's `TestClient` reports
`"testclient"`; a unix-socket deployment reports a path), and `AuditContext.actor_ip`
feeds a parser that raises on anything else — so a non-IP value is recorded as `None`
rather than fabricated or allowed to 500 the request.

The address recorded is the one `nptc.api.rate_limit.AnonymousRateLimitMiddleware` decided,
not `request.client.host` itself: behind Caddy that would be Caddy's address, so the middleware
reads `X-Forwarded-For` from a trusted proxy (`NPTC_TRUSTED_PROXIES`, see
[`public-api.md`](public-api.md#rate-limiting-and-caching)) and `request_audit_context` takes
the result from the request. Events written before this change recorded the proxy's address.

The `correlation_id` is minted once per request and stashed on `request.state`, so the
resolution events and every later write in the same request share one — which is the only
thing a correlation id is for.

**One consequence worth knowing.** `session_scope` rolls the transaction back on any
exception, and a refusal *is* an exception (`PermissionDeniedError`, `ManualLinkRequiredError`).
So when a first login resolves a brand-new account and the very same request is then
denied, the `user_identity.created` event rolls back with the account it described. That
is correct — the account was not created either — but it means "a first login was
attempted and refused" leaves no trace in `audit_event`. Keycloak's own event store
(NFR-11) is where that attempt is recorded.

### Two requests, one new user

A new user's first page load sends several requests at once, and each carries the same
token. All of them can read `user_identity`, find nothing, and insert. The database
settles it with `uq_user_identity_issuer`: the first insert commits and the others fail.
Left alone, the failure surfaced as a 500 on whichever request lost.

`resolve_user_for_claims` recovers by re-reading, not by locking. Both inserts that can
race run inside a `SAVEPOINT`: the first-login insert in `_create_user`, and the auto-link
insert. A `uq_user_identity_issuer` violation rolls back only that `SAVEPOINT`, including
its `user_identity.created` and `user_role.granted` audit events (NFR-08). The loser then
re-reads the identity, which under READ COMMITTED sees the winner's committed row, and
returns it as `EXISTING`. The race leaves one user, one identity and one set of audit
events.

Two details matter:

- When both requests carry the same `preferred_username`, the loser first collides on
  `uq_app_user_username`, retries with a suffix, and only then hits the identity
  constraint. Recovery works on either order.
- If the re-read finds no row, the winner rolled back and the conflict has no
  explanation. The original `IntegrityError` is re-raised rather than retried. Every
  other constraint violation is also re-raised unchanged.

Isolation stays READ COMMITTED because the audit writer requires it. Advisory locks or
`SERIALIZABLE` are not needed here.

## The realm's browser flow

ADR-0021 restructured it into one conditional subflow per level of authentication:

```text
nptc browser forms
  nptc browser forms conditional password   CONDITIONAL
    conditional-level-of-authentication  (nptc loa-1 condition)
    auth-username-password-form          REQUIRED
  nptc browser forms conditional otp        CONDITIONAL
    conditional-level-of-authentication  (nptc loa-2 condition)
    auth-otp-form                        REQUIRED
```

with a realm-level `acr.loa.map` of `{"1": 1, "2": 2}`. Without a satisfiable LoA 1,
Keycloak resolves every request to the flow's highest level and demands OTP enrolment of
every user — which is what the committed realm did before #41, contradicting NFR-02. See
ADR-0021 for the evidence.

## The login theme

Keycloak's own pages are the only place a password is typed (NFR-01), so they carry the
platform's look through a login theme named `nptc` in `deploy/keycloak/themes/nptc/login/`.

- **Plain files, no build step.** The theme is FreeMarker templates and CSS on top of
  Keycloak's `keycloak.v2` theme (`parent=keycloak.v2`). It uses no Keycloakify and no Java
  provider, which this repository does not build or ship. Keycloak's own markup and element
  IDs, such as `kc-form-login`, stay as they are.
- **How it loads.** `deploy/compose.yml` mounts the directory read-only at
  `/opt/keycloak/themes/nptc`, and `nptc-realm.json` sets `"loginTheme": "nptc"`. No console
  step is involved (NFR-03).
- **What the pages show.** A deep-teal side panel with a serif headline and three factual
  lines sits beside the form, as the design guide asks for sign-in
  ([design-system.md](design-system.md)). The panel carries no statistics, because the
  platform has none to show. The theme is light only, because the design guide defines no
  dark palette.
- **Registration notice.** The registration page shows the collection notice (NFR-14) and
  links to the SPA's `/privacy` and `/terms`. The links take their address from the realm
  attribute `nptcFrontendBaseUrl`, which comes from `NPTC_FRONTEND_BASE_URL`. A client's own
  `baseUrl` is not used, because the `account-console` client's points at Keycloak. The page
  has no acceptance checkbox: acceptance is recorded by the platform after sign-in, not by
  Keycloak ([ADR-0043](../adr/0043-terms-acceptance-storage.md)). The notice says only that
  the retention period is still being settled, because OI-15 has not closed.
- **Three copied templates.** The theme overrides `template.ftl` (the side panel),
  `register.ftl` (the notice, and no stock terms checkbox) and `login-config-totp.ftl`. The
  stock TOTP template points both labels at an id that does not exist, so its inputs have no
  accessible name. **When the Keycloak image tag changes, compare these three files with
  the new image's `keycloak.v2` originals** and carry over any fix.
- **Tokens are repeated, not shared.** Keycloak serves its own static files, so the SPA's
  stylesheet and fonts are not reachable. `resources/css/nptc.css` repeats the colour,
  radius, focus-ring and font tokens from `frontend/src/styles/app.css`, and
  `resources/fonts/` holds copies of the three font files with their OFL licences. A test
  fails if a repeated token differs from the SPA's.
- **To change it.** Edit the files and reload the page. `start-dev` does not cache themes,
  so no restart is needed. Then run `uv run pytest backend/tests/test_keycloak_login_theme.py`
  (no Docker) and, for page changes, `backend/tests/test_keycloak_pkce_login.py`.

The TOTP setup page appears on step-up, not on an ordinary sign-in. To see it, request
`acr_values=2` with an account that has no authenticator yet.

## What is tested where

- `backend/tests/test_keycloak_login_theme.py` — without Docker: the realm's `loginTheme`
  names the directory compose mounts, the theme's tokens match `app.css`, each font has its
  licence, and the registration template has the notice and no checkbox.
- `backend/tests/test_keycloak_pkce_login.py` — the real round trip against the pinned
  Keycloak image, including the two checks Keycloak owns (mismatched `code_verifier`,
  replayed code) and that logout ends the SSO session. It also fetches the themed sign-in,
  registration, TOTP setup and error pages and asserts one `h1`, labelled inputs, the
  registration notice and links, and no acceptance checkbox.
- `backend/tests/test_api_auth_session.py` — the dependency chain over HTTP.
- `backend/tests/test_api_error_mapping.py` — the 401/403/409 table above, the
  `AuthSettings`-derived `acr_values`, the CORS `expose_headers` assertion, and that an
  unhandled error is a CORS-readable generic 500 for the frontend origin only.
- `backend/tests/test_auth_identity_concurrency.py` — two real sessions racing the same
  new subject, forced so the loser always blocks on the winner's insert, on the
  first-login and auto-link paths.
- `frontend/src/auth/*.test.ts(x)` — the browser's own half: `state` validation, the
  single-use transaction, renewal, and what each route renders per status; `stepUp`'s own
  silent-success and never-degrades-the-session-on-refusal behaviour.
- `frontend/src/api/step-up.test.ts` — the RFC 9470 challenge parser and guard.
- `frontend/src/pages/admin-catalogue-edit.test.tsx` — the step-up controller end to end
  against a real admin route: the interactive fallback, the silent-success retry, the
  retry-once guard, and that a refused mutation is never replayed.
- `frontend/src/shell/step-up-banner.test.tsx` — the pre-emptive banner's visibility and
  its own redirect.
- `frontend/scripts/assert-no-secret-in-bundle.mjs` — NFR-01 against the built assets.
