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
| `auth-context.tsx` | The session: tokens in memory, the cold-load probe, renewal, sign-in/out, callback completion |
| `session.ts` | The context object, its value type, `AuthStatus`, and `useAuth` |
| `auth-status.ts` | The `useAuthStatus()` seam ADR-0020 reserved, unchanged in shape |

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

## Server side (`backend/src/nptc/api/`)

| Module | Responsibility |
|---|---|
| `app.py` | `create_app()`: CORS (exactly one origin), exception handlers, routers |
| `dependencies.py` | `current_principal`, `permission_dep`, the per-request `AuditContext`, the session and verifier |
| `errors.py` | `TokenError` → 401; `AuthorisationError` → its own `http_status` |
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

### Audit attribution has two phases

`resolve_user_for_claims` emits `user_identity.created` (and, on a first login,
`user_role.granted`) for a user whose internal id does not exist until those inserts run.
So the resolution uses a bootstrap `AuditContext` with `actor_user_id=None` — carrying the
IP and user agent, so the event is still attributable to a request. Writes made *after*
resolution use `audit_context`, which carries the resolved `user_id` (NFR-08).

`request.client.host` is not always an IP (Starlette's `TestClient` reports
`"testclient"`; a unix-socket deployment reports a path), and `AuditContext.actor_ip`
feeds a parser that raises on anything else — so a non-IP value is recorded as `None`
rather than fabricated or allowed to 500 the request.

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

## What is tested where

- `backend/tests/test_keycloak_pkce_login.py` — the real round trip against the pinned
  Keycloak image, including the two checks Keycloak owns (mismatched `code_verifier`,
  replayed code) and that logout ends the SSO session.
- `backend/tests/test_api_auth_session.py` — the dependency chain over HTTP.
- `backend/tests/test_api_error_mapping.py` — the 401/403/409 table above.
- `frontend/src/auth/*.test.ts(x)` — the browser's own half: `state` validation, the
  single-use transaction, renewal, and what each route renders per status.
- `frontend/scripts/assert-no-secret-in-bundle.mjs` — NFR-01 against the built assets.
