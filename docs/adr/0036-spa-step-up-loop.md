# ADR-0036: SPA step-up re-authentication loop

**Status:** Accepted
**Date:** 2026-09-08

## Context

NFR-06's mandatory-MFA-for-administrators loop was built at both ends and unjoined in the
middle (issue #184). The API already answers an administrator whose MFA is unsatisfied
with `403` + `WWW-Authenticate: Bearer error="insufficient_user_authentication",
acr_values="2"` (RFC 9470, issue #41's `nptc.api.errors`), and the SPA's
`buildAuthorizeUrl` already accepted an `acrValues` option (ADR-0021). Nothing reacted to
the challenge: an administrator signed in with a password alone simply saw a refusal, and
four user guides plus a `LoadFailure` branch in the catalogue edit page told them, in
prose, to sign out and sign back in.

Three design questions had to be settled before writing any code, and each is recorded
here because a future change touching this area should not relitigate them without
reading why.

## Decision

### Detection lives at one seam: the query/mutation cache, not per screen

`createQueryClient`'s `QueryCache`/`MutationCache` both take an `onStepUpChallenge`
callback (`frontend/src/api/query-client.ts`). Every failed query and mutation passes
through this cache regardless of which screen made the call, so a step-up challenge is
recognised once, centrally, the same way `frontend/src/api/conflicts.ts` already narrows
409 bodies in one place rather than at each call site.

`ApiError` (`frontend/src/api/unwrap.ts`) had to grow a `headers: Headers` field to make
this possible: the challenge is otherwise discarded at `unwrap`'s single throw site.
`nptc.api.app.create_app`'s CORS middleware also had to add
`expose_headers=["WWW-Authenticate"]` — without it, a browser's `fetch`/XHR hides the
header from JavaScript on every cross-origin response (`vite dev` included), and the whole
feature does nothing outside of a same-origin deployment, silently, with no failing test
to catch it.

### Silent before interactive, and only a refused *read* is retried automatically

`StepUpController` (`frontend/src/auth/step-up.tsx`, mounted once in `RootLayout`) always
tries a silent `prompt=none` + the challenge's own `acr_values` first
(`AuthContextValue.stepUp`), reusing the existing hidden-iframe renewal
(`silent-renew.ts`). Only when that cannot be satisfied without interaction does it show a
dialog explaining what is about to happen, then falls back to `signIn({ acrValues,
redirect })` — an ordinary top-level redirect through Keycloak, using `signIn`'s existing
transaction machinery to carry the return path.

A refused **query** is refetched in place once the step-up succeeds — no navigation, no
lost page state. A refused **mutation** is never replayed automatically, silent step-up or
not: the user resubmits, and by then MFA is satisfied. Two reasons, not one:

1. **The user never re-confirmed the write.** Replaying a mutation the moment step-up
   completes would submit a request the user's last affirmative act was for a *different*
   authentication context. FR-38's whole optimistic-locking discipline exists because a
   write should reflect what the user meant *now*, not a queued intention from a few
   seconds ago.
2. **There is nowhere safe to hold the payload across an interactive redirect.**
   `sessionStorage`-ing a pending catalogue-entry write so it can be replayed after the
   Keycloak round trip is itself a question NFR-26/NFR-35 (no secrets or personal
   information logged or cached carelessly) would need answering — and the silent path,
   which is the common case, does not even need to ask it. Scoping the feature to "never
   replay a mutation" avoids the question entirely rather than answering it once and
   reopening it every time a new write route is added.

### `mfa_satisfied` is surfaced pre-emptively, minimally

`useSession()` (`frontend/src/api/queries.ts`) reads `GET /api/v1/auth/me`, and
`StepUpBanner` (shown on every `/admin/*` screen via the new `AdminLayout`) offers to
verify before the user walks into a refusal at all. This is the one place a literal
`acr_values="2"` appears on the frontend (`PRE_EMPTIVE_STEP_UP_ACR_VALUES` in
`step-up-banner.tsx`): there is no challenge yet to read a value from, since no refusal
has happened. Everywhere else — the reactive path this ADR is mostly about — the value
always comes from the server's own challenge, never a constant, so changing the realm's
LoA mapping needs no frontend change. The banner's own constant is a narrower promise,
and changing `AuthSettings.mfa_acr_values` needs this one literal updated to match.

"Verify now" does not redirect on its own (PR #284 review, round 1): it calls
`requestStepUp`, which hands the same acr value to `StepUpController` as a `{ kind:
"no-retry" }` context — the same shape a refused mutation's challenge carries, since
neither has a query to retry. A click gets the identical silent-first/dialog/
interactive-fallback treatment a reactive challenge would, rather than the unconditional,
unannounced `signIn` redirect the banner used before: a click the SSO session can satisfy
silently closes the banner with no navigation at all, and only one that genuinely needs
Keycloak's help shows the same "you're about to be sent to sign in again" dialog. Because
that silent attempt can take up to `SILENT_RENEW_TIMEOUT_MS` (`silent-renew.ts`, 10s),
`requestStepUp` returns the attempt's own promise and the banner tracks its own pending
state ("Checking…", disabled) rather than leaving the click looking inert for the wait
(PR #284 review, round 2).

### The backend challenge itself stopped being a literal too

`nptc.api.errors._STEP_UP_CHALLENGE` was `'Bearer error="insufficient_user_authentication",
acr_values="2"'` — a second hard-coded copy of the same fact the frontend was being asked
not to hard-code. `register_exception_handlers` now takes an `AuthSettings` and builds the
challenge from `mfa_acr_values`, so both ends read the one configured value.

## Consequences

- NFR-06 moves to `implemented`.
- The four identical MFA caveat blockquotes (`docs/user/binding-a-code.md`,
  `editing-an-entry.md`, `editing-designations.md`, `editing-registry-properties.md`) and
  the `LoadFailure` 401/403 special case in `admin-catalogue-edit.tsx` are deleted — the
  reactive controller now handles what they used to describe by hand.
- Every further admin screen (starting with #267's admin list) gets step-up handling for
  free through the same `QueryCache`/`MutationCache` seam; no screen needs its own
  MFA-refusal branch.
- `createQueryClient` takes an optional `onStepUpChallenge` — `main.tsx` and
  `frontend/src/test/render-route.tsx` both pass it the same `stepUpChallengeHandler`,
  which forwards to whichever `StepUpController` the shared route tree's `RootLayout` has
  mounted, so a route test exercising a step-up challenge sees the real detection and
  retry path rather than a silent no-op.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| **Per-screen challenge handling** (each query hook or page catches its own 403 and reacts) | Exactly the "challenge handler scattered across call sites" the issue's own dependency note warned would rot. A new admin screen would need to remember to wire it, and inevitably one would not. |
| **`onResponse` middleware in `frontend/src/api/client.ts`** | Closer to the transport, but `openapi-fetch` middleware sees every response before `unwrap` has decided pass/fail, so it would have to re-implement `unwrap`'s own ok/error gating (issue #147 review) to know whether a given response is a refusal worth reacting to at all. The query/mutation cache already sits downstream of that decision. |
| **Replaying a refused mutation automatically after a successful silent step-up** | Considered and rejected — see the Decision section above. Left as a documented non-goal rather than a silent gap. |
| **Persisting form state across an interactive redirect (any mechanism)** | Same NFR-26/NFR-35 question the mutation-replay rejection raises, for no benefit in the common (silent) case. Out of scope; the interactive fallback only ever fires for a query when the silent attempt fails, and the query itself needs no persisted state to retry. |
| **A retry counter on the query itself** (`useQuery`'s own `retry` option) | Would conflate an ordinary network retry with a step-up retry, and `retry: false` is deliberately the app-wide default (`query-client.ts`) for unrelated reasons (this app's principal failure mode is an authorisation refusal, not a flaky network). The retry-once guard is instead a `Set<queryHash>` inside `StepUpController`, scoped to one challenge/retry cycle — added before the attempt starts and removed once it settles, not a permanent per-query record (PR #284 review, round 1: an unscoped guard would silently swallow a genuinely later challenge for the same query). |
| **A backend Keycloak integration test for `prompt=none` plus `acr_values=2`** | `test_keycloak_pkce_login.py` covers `prompt=none` for logout only and asserts nothing about `acr`. Whether Keycloak can satisfy LoA-2 silently within `loa-max-age` (36000s on this realm) is worth knowing but is a container test on its own, and the interactive fallback is correct either way regardless of the answer. Noted as a follow-up rather than growing this change further. |

## Follow-ups

- Confirm against the local Keycloak container whether a silent `prompt=none` +
  `acr_values=2` actually succeeds for a session that has already completed OTP once
  within `loa-max-age`. If it never does, the silent path is dead code in practice and the
  interactive dialog is what every step-up actually shows — correct either way, but it
  decides how much further the silent path is worth polishing.
- If #267 gives `/admin` a fuller shell (navigation, breadcrumbs), `StepUpBanner` moves
  from `AdminLayout` into it rather than `AdminLayout` growing unrelated concerns.
