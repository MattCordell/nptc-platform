# Frontend routing and layout shell

Issue #146. See [ADR-0020](../adr/0020-frontend-router.md) for the router choice and
rejected alternatives; this document is the implementation reference.

## Scope

Lands with #146: the route table, the layout shell (header, primary navigation, `<main>`
landmark, footer, skip link), router-level not-found and error surfaces, and a structural
`RequireAuth` seam for authenticated and admin routes. Deliberately absent, and left to
their own issues: data loading and the generated API client (#147), the accessible
component/styling baseline (#148 — `shell.css` is a placeholder it should replace), and
real sign-in (#41 — see "Authentication is structural" below). No route is code-split yet
(see ADR-0020's consequences).

## The route table is the single source of URL shapes

`frontend/src/router/route-tree.ts` declares every route the platform serves, hand-written
and code-based (no file-based routing, no generated `routeTree.gen.ts` — see ADR-0020). A
new screen adds a route here; it does not invent a path anywhere else. Full inventory:

| URL | Driving FRs |
|---|---|
| `/` | landing |
| `/catalogue` (+ `q`, `page`, `sort` search params) | FR-14, FR-15, FR-16, FR-18 |
| `/catalogue/$businessKey` | FR-17, FR-19 |
| `/catalogue/$businessKey/history` | FR-19, FR-35 |
| `/catalogue/code/$systemToken/$code` | FR-17 |
| `/catalogue/lookup?system=&code=` | FR-17 |
| `/releases`, `/releases/compare?from=&to=`, `/releases/$releaseId` | FR-56–FR-61 |
| `/exports`, `/about`, `/terms` | FR-62–FR-69, FR-78, NFR-45 |
| `/sign-in?redirect=`, `/sign-out`, `/register`, `/auth/callback` | issue #41 |
| `/submissions`, `/submissions/new`, `/submissions/$submissionId` | FR-23–FR-31 |
| `/interest`, `/account` | FR-32–FR-34 |
| `/admin`, `/admin/catalogue{,/new,/$businessKey/edit}` | FR-36–FR-39 |
| `/admin/properties{,/new,/$propertyKey}` | FR-08–FR-13 |
| `/admin/users{,/$userId}` | FR-40–FR-43 |
| `/admin/validation{,/$findingId}` | FR-45–FR-55 |
| `/admin/releases{,/new}`, `/admin/exports/config`, `/admin/audit` | FR-56–FR-61, FR-78, NFR-08–NFR-13 |

Every route not yet implemented mounts `pages/placeholder.tsx`'s `createPlaceholderPage`
factory rather than one bespoke file per stub, so swapping in a real screen is a one-line
route-table edit plus a new page file.

There is deliberately no `/admin/submissions`: the reviewer queue is `/submissions`, and
what a given user sees there is decided server-side. That is NFR-20 expressed in the route
table rather than left as a comment.

## The URL contract (FR-17, issue #140)

Three forms resolve the same entry:

- `/catalogue/{business_key}` — e.g. `/catalogue/NPTC-000247`. `business_key` is the
  public identifier (FR-03); the internal UUID never appears in a route (PRD §6.2).
- `/catalogue/code/{system_token}/{code}` — `sct` is the registered alias for
  `http://snomed.info/sct`.
- `/catalogue/lookup?system={uri}&code={code}` — for callers holding the full system URI.

Search result state (`q`, `page`, `sort`) is encoded entirely in `/catalogue`'s URL, so a
pasted search link reproduces the identical result set and filter state.

## Codes are strings, always

A code is a string end to end (FR-06): never `Number()`'d, and an 18-digit SCTID exceeds
`Number.MAX_SAFE_INTEGER`. This constrains the router more than it first appears to.

`@tanstack/router-core`'s search-param codec (`qss`) coerces any numeric-looking query
value to a real JS number in its `decode()` step, *independently* of the `parseSearch`
option — passing an identity parser does not intercept this. Concretely, out of the box,
`?code=123456` (no leading zero, within safe-integer range) silently arrives as the
**number** `123456`; an 18-digit SCTID happens to survive by accident (float rounding
breaks `qss`'s own round-trip guard), but that is luck, not a guarantee. `router.tsx`
therefore hand-rolls `parseSearch`/`stringifySearch` directly against `URLSearchParams`,
bypassing `qss` entirely — every search value is a raw string in, and stringified without
JSON-quoting out. `route-tree.test.tsx`'s round-trip assertions (a leading-zero code and
an 18-digit SCTID) guard this file; do not "simplify" it back to
`parseSearchWith`/`stringifySearchWith`.

A second, related trap: TanStack Router calls each route's `validateSearch` more than once
per navigation (once during its lightweight route matching, again while committing the
location), and the *second* call receives the validator's own previously-validated output
— `page` arrives back as the number the validator itself returned, not a string.
`validateSearch` must therefore be idempotent. `search-params.ts`'s `asPage` accepts an
already-valid number as well as a numeric string for exactly this reason;
`search-params.test.ts`'s idempotency test is the regression guard (this failed silently
during development, defaulting a valid `page=3` back to `page=1` on the second pass).

Path params never need this treatment — `params.parse` is opt-in and unused here, so
`/catalogue/code/sct/000123` is a plain string by default.

## Building a URL

Every internal link goes through the route table's types — `<Link to>`, `useNavigate`, or
`router.buildLocation` for a raw href (e.g. a copy-link button) — never a template literal
or string concatenation on a path segment:

```tsx
<Link to="/catalogue/$businessKey" params={{ businessKey: entry.businessKey }}>
  {entry.preferredTerm}
</Link>

<Link to="/catalogue/code/$systemToken/$code" params={{ systemToken: "sct", code }}>
  View in the catalogue
</Link>

const href = router.buildLocation({ to: "/catalogue/$businessKey", params: { businessKey } }).href;
```

The `declare module "@tanstack/react-router" { interface Register }` block in
`router.tsx` is what makes `to`/`params`/`search` a compile error when wrong — a
`pnpm typecheck` failure, not just a review comment. `eslint.config.js`'s
`no-restricted-syntax` rule backstops it by rejecting a template literal or `+`
concatenation that starts with an internal path segment (`route-tree.ts` itself, and test
files that deliberately deep-link a raw URL to exercise the router, are exempt).

Search-param types intentionally use TanStack's `SearchSchemaInput` brand (via a
type-only cast in `route-tree.ts`) so `<Link to="/catalogue">` needs no `search` prop at
all, while the validator functions in `search-params.ts` keep a plain
`Record<string, unknown>` parameter and stay trivially unit-testable.

No schema library (zod/valibot) is used for search validation — see ADR-0020.

## The layout shell

`shell/root-layout.tsx` renders the chrome every route sits inside: `<HeadContent />` (per-
route document title, declared via each route's `head` option), a skip link, `<header>`
with `<nav aria-label="Primary">`, `<main id="main-content" tabIndex={-1}>`, and
`<footer>`. Deliberately no `<h1>` in the shell — each page owns its own, so heading order
stays sane as screens are added.

After a client-side navigation there is no full page load to reset focus, so
`useFocusMainOnNavigation` moves focus to `<main>` on every route change after the first
(NFR-31; PRD §17.2 item 4). The primary navigation is shown unconditionally, including
links into the authenticated and admin sections — see "Authentication is structural"
below for why that's fine.

`shell/shell.css` is a placeholder: just enough for the landmarks and skip link to be
usable. Issue #148 owns the platform's real styling strategy and should replace it, not
build on top of it.

## Not-found and error surfaces

Both are wired once, at the router (`defaultNotFoundComponent`, `defaultErrorComponent` in
`router.tsx`), not per-route — a new route inherits them automatically and cannot forget
to wire one (PRD §17.2 item 5).

- **Not found** (`shell/not-found-page.tsx`): `notFoundMode: "fuzzy"` means the nearest
  matching ancestor renders it, so the shell — header, navigation, footer — stays on
  screen and the user has somewhere to go, rather than a blank screen. A route needing a
  more specific message (e.g. "no entry with that business key") can still throw
  `notFound()` from a loader and set its own `notFoundComponent`.
- **Route error** (`shell/route-error-page.tsx`): catches any render error thrown inside a
  route. Renders a friendly message and a "Try again" action; logs the real error to
  `console.error` for a developer. It must never render `error.message`, `error.stack`, or
  a raw status code — `router.test.tsx`'s test asserts the exception text is *absent* from
  the DOM, not just that the friendly heading is present (a heading-only assertion would
  still pass with a stack trace printed underneath).

## Authentication is structural

`shell/require-auth.tsx` gates the authenticated and admin routes via `auth/auth-status.ts`
(`useAuthStatus()`, currently always `"unavailable"`). This is presentation only: **NFR-20**
requires every request to be authorised server-side against the internal user record, and
no authorisation decision is ever made in the browser — hiding a UI control is not access
control. Not rendering a screen here does not protect the data behind it; the API endpoints
those screens will call are the actual boundary.

Issue #41 (OIDC PKCE login) replaces `useAuthStatus`'s body with the real session and adds
a `beforeLoad` redirect to `/sign-in?redirect=...`. The route table under
`RequireAuth` does not change — `require-auth.test.tsx` asserts today's placeholder
renders at the exact pathname a signed-in user's screen will render at later (no redirect,
`history.length === 1`); that assertion is the contract #41 must preserve.

## Serving requirements

Two things a deployment must get right that this issue cannot enforce itself:

- **SPA fallback.** Every non-asset path is a client-side route. `deploy/` has no
  Caddyfile yet (only `compose.yml`, `.env`, the Keycloak realm), so whichever issue adds
  the Caddy service must include a fallback, e.g.:

  ```caddyfile
  handle /api/* { reverse_proxy api:8000 }
  handle {
    root * /srv/frontend
    try_files {path} /index.html
    file_server
  }
  ```

  Without it, deep-linking to `/catalogue/NPTC-000247` in a fresh session 404s at the
  proxy in production only — `vite dev`/`vite preview`'s default `appType: "spa"` already
  does this locally, so the gap is easy to miss.
- **Base path.** Vite's `base` and `createAppRouter`'s `basepath` (unset today, meaning
  both default to `/`) must move together if the app is ever served from a sub-path.

## Testing

`src/test/render-route.tsx`'s `renderRoute(url)` mounts the **production**
`createAppRouter()` over a fresh `createMemoryHistory` — a cold browser session, exactly
like deep-linking into a new tab, with nothing cached from an earlier route — and awaits
`router.load()` before returning. Tests query by role (`getByRole`), never by test id, per
the repository's existing convention. `route-tree.test.tsx`'s `it.each` sweep renders every
declared route via its typed `to`/`params`/`search`, so the fixture cannot drift from the
route table without failing to compile, and doubles as the coverage driver for the
placeholder factory and the shell.
