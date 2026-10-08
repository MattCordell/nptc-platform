# Frontend routing and layout shell

Issue #146. See [ADR-0020](../adr/0020-frontend-router.md) for the router choice and
rejected alternatives; this document is the implementation reference.

## Scope

Lands with #146: the route table, the layout shell (header, primary navigation, `<main>`
landmark, footer, skip link), router-level not-found and error surfaces, and a structural
`RequireAuth` seam for authenticated and admin routes. Deliberately absent, and left to
their own issues: a page that actually loads catalogue data through the generated API
client (#147's infrastructure - `QueryClientProvider`, the typed client, TanStack Query
hooks - is wired into `main.tsx`, but no route consumes it yet, see
[public-api.md](public-api.md#generated-typescript-client-issue-147)), the accessible
component/styling baseline (landed with #148 — see [components.md](components.md)), and
real sign-in (#41 — see "Authentication is structural" below). No route is code-split yet
(see ADR-0020's consequences).

## The route table is the single source of URL shapes

`frontend/src/router/route-tree.ts` declares every route the platform serves, hand-written
and code-based (no file-based routing, no generated `routeTree.gen.ts` — see ADR-0020). A
new screen adds a route here; it does not invent a path anywhere else. Full inventory:

| URL | Driving FRs |
|---|---|
| `/` | landing (its search box navigates to `/catalogue` with the trimmed `q`) |
| `/catalogue` (+ `q`, `after`, `filter.<key>` search params, issue #439) | FR-14, FR-15, FR-16, FR-18 |
| `/catalogue/$businessKey` (issue #440) | FR-17, FR-18, FR-19 |
| `/catalogue/$businessKey/history` (+ `before` search param, issue #441, a real screen: `pages/entry-history.tsx`) | FR-19, FR-35 |
| `/catalogue/code/$systemToken/$code` (issue #442, a real screen: `pages/code-by-token.tsx`) | FR-17 |
| `/catalogue/lookup?system=&code=` (issue #442, a real screen: `pages/code-lookup.tsx`) | FR-17 |
| `/releases`, `/releases/compare?from=&to=`, `/releases/$releaseId` | FR-56–FR-61 |
| `/exports`, `/about` | FR-62–FR-69, FR-78 |
| `/terms` (issue #435, a real screen: `pages/terms.tsx`) | NFR-45, NFR-47 |
| `/sign-in?redirect=`, `/sign-out`, `/register`, `/auth/callback` | issue #41 |
| `/submissions`, `/submissions/new`, `/submissions/$submissionId` | FR-23–FR-31 |
| `/interest`, `/account` | FR-32–FR-34 |
| `/admin`, `/admin/catalogue` (+ `q`, `after`, `filter.<key>` search params, issue #267), `/admin/catalogue{/new,/$businessKey/edit}` | FR-36–FR-39 |
| `/admin/properties` (+ `deprecated=show` search param), `/admin/properties/$propertyKey`, `/admin/properties/$propertyKey/edit`, `/admin/properties/new` | FR-08–FR-13 |
| `/admin/users{,/$userId}` | FR-40–FR-43 |
| `/admin/validation{,/$findingId}` | FR-45–FR-55 |
| `/admin/releases{,/new}`, `/admin/exports/config` | FR-56–FR-61, FR-78 |
| `/admin/audit` (+ `actor`, `entity_type`, `entity_id`, `action`, `from`, `to`, `before` search params, issue #447) | NFR-09, NFR-12, NFR-31 |

Every route not yet implemented mounts `pages/placeholder.tsx`'s `createPlaceholderPage`
factory rather than one bespoke file per stub.

The signed-in routes sit behind two shell steps, both presentation only (NFR-20). `RequireAuth`
sends a signed-out visitor to sign in. Inside it, `TermsGate` shows the full-page **Accept the
terms of use** screen in place of the routed page, which stays mounted but hidden, until the
user accepts the current terms (NFR-45, issue #435). `/terms`, `/privacy` and the other public
routes are outside both, so they stay readable to a user who has not accepted. See
[permissions.md](permissions.md#what-the-spa-does-issue-435).

### How a stub becomes a real screen

A stub is one `createPlaceholderPage({ title, issue?, nearest? })` call in
`router/route-tree.ts`. The factory renders a `NoticePage` with the title as the one `h1`,
a text "Planned" status, the issue it lands with (when `issue` is set), a link back to the
landing page and, when `nearest` is set, a link to each built screen it names. `nearest`
takes one link or a list. Only the admin stubs set it, and each points to
`/admin/catalogue`. The public stubs leave it unset: the landing page link already leads to
`/catalogue`.

The admin home at `/admin` is a real page (`pages/admin-home.tsx`), not a stub. It holds one
typed list of every `/admin/*` screen and renders a card for each. A planned screen's card
links to its stub and carries a text "Planned" status. When a stub becomes a real screen,
set that entry's `planned` to `false`. The page also shows a notice to a signed-in user whose
`GET /auth/me` roles lack `administrator`. The notice is presentation only (NFR-20): no card
is hidden, and the server refuses what the caller may not do.

To replace a stub with the real screen:

1. Add the page file under `frontend/src/pages/`, with its own `h1` (use `PageHeader`) and
   a co-located test that calls `expectNoA11yViolations`.
2. In `route-tree.ts`, change that route's `component` from the `createPlaceholderPage(...)`
   call to your page component. Leave `path` and `head: titled(...)` as they are.
3. Delete the stub's `issue` and `nearest` options with the call. If it was the last stub
   using a given `nearest` constant, delete the constant too.

No route path changes, so links, `aria-current` and the title mechanism keep working.

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

Search result state (`q`, `after`, `filter.<key>`) is encoded entirely in `/catalogue`'s
URL, so a pasted search link reproduces the identical result set and filter state. The
public endpoints offer no sort and no page number (ADR-0024), so the route has neither.

The API half of this contract - `GET /catalogue/entries/{business_key}`,
`GET /catalogue/code/{system_token}/{code}` and `GET /catalogue/lookup`, all serving the
identical `EntryDetail` body - is documented in
[public-api.md](public-api.md#exact-code-lookup-fr-17). `/catalogue/{business_key}` is a
real screen (`pages/catalogue-entry.tsx`, issue #440).

The two code lookup routes are real screens too (issue #442). Neither redirects to the
entry. Each shows a result with a link to it, because the API resolves a retired code and a
redirect would hide that the code is retired on the entry.

- `/catalogue/code/$systemToken/$code` (`pages/code-by-token.tsx`) calls `useEntryByCode`.
- `/catalogue/lookup` (`pages/code-lookup.tsx`) shows a form. Submitting it navigates to
  the route above with the code system's token and the trimmed code. When the address
  already holds both `system` and `code`, the page calls `useEntryBySystemCode` and shows
  the result under the form.
- Both render `CodeLookupResult` (`catalogue/code-lookup-result.tsx`): the matching entry, a
  404 as "No matching entry" using the server's sentence, a 422 as "Code not accepted", and
  any other failure with **Try again**. The server answers an unregistered system and an
  unknown code with the same 404, so the page never says which was wrong.
- The API has no endpoint that lists code systems. `catalogue/code-systems.ts` holds the
  list the form's select reads, and it must change in the same change that registers a
  system server-side.

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
— a `filter.<key>` arrives back as the array the validator itself returned, not a string.
`validateSearch` must therefore be idempotent. `search-params.test.ts`'s idempotency tests
are the regression guard (an earlier `page` parameter failed this silently during
development, defaulting a valid `page=3` back to `page=1` on the second pass).

Path params never need this treatment — `params.parse` is opt-in and unused here, so
`/catalogue/code/sct/000123` is a plain string by default.

`stringifySearch` supports scalars and arrays of scalars only, and throws for anything
else. This isn't an oversight: `parseSearch` never JSON-parses a value back out (never
coercing is the whole point), so a plain object would `JSON.stringify` cleanly going out
but return as an unparsed JSON string, not the original object, on the next parse —
`stringifySearch` and `parseSearch` would silently stop being inverses of each other.
Throwing surfaces that the day a structured filter param is first added, rather than
leaving it to be discovered as a quietly wrong round trip.

`asPage` additionally requires the entire search value to be digits (`Number.parseInt`
alone accepts trailing garbage, e.g. `"3drop"` → `3`) and caps it at a generous but finite
upper bound (`Number.parseInt` has no ceiling, so an absurd value like
`"99999999999999999999"` would otherwise pass through as a real, if meaningless, page
number). `validateSignInSearch`'s `redirect` is restricted to an internal, same-origin
path (must start with a single `/`, not `//`, and reject a backslash) — accepting an
arbitrary value would make it an open redirect, since issue #41 uses it to send a
just-signed-in user back where they were.

## `from` means two different things depending on the hook

`useSearch({ from })`/`useParams({ from })` key off a route's **internal id**, which
includes a pathless layout segment such as `authenticated` (`shell/require-auth.tsx`'s own
route, contributing no URL segment of its own) - matching `admin-catalogue-edit.tsx`'s own
`useParams({ from: "/authenticated/admin/catalogue/$businessKey/edit" })`.
`useNavigate({ from })`/`<Link from>` key off the **resolved URL path** instead, which never
includes a pathless segment - `/admin/catalogue/$businessKey/edit`, with no
`/authenticated` prefix (issue #267 review; confirmed against the router's own
`routesById`, not assumed from the URL, after `tsc` rejected the id-shaped string with an
opaque "not assignable to `FromPathOption`" error that named no missing route). A route
nested under `admin-catalogue-list.tsx`'s two constants (`ROUTE_ID` for `useSearch`,
`ROUTE_PATH` for `useNavigate`) is the pattern to copy for a new screen under `/admin`
needing both.

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
`no-restricted-syntax` rule backstops it — rejecting a template literal or `+`
concatenation that builds an internal path, a raw string literal in an `href` attribute,
and a direct `location.assign`/`.replace`/`.href` — but it is a syntax-shape backstop, not
an exhaustive guarantee: it cannot see through an indirection (a path built in a variable
or helper elsewhere). The type system and code review are what a determined bypass still
has to get past. (`route-tree.ts` itself, and test files that deliberately deep-link a raw
URL to exercise the router, are exempt from the rule.)

Search-param types intentionally use TanStack's `SearchSchemaInput` brand (via a
type-only cast in `route-tree.ts`) so `<Link to="/catalogue">` needs no `search` prop at
all, while the validator functions in `search-params.ts` keep a plain
`Record<string, unknown>` parameter and stay trivially unit-testable.

`/catalogue`'s and `/admin/catalogue`'s routes also attach a `stripSearchParams` search
middleware for `q: ""`. Each validator always returns `q`, so consuming code never has to
fall back on an absent one, but without stripping every link into `/catalogue` would commit
as `/catalogue?q=`. The middleware only affects what gets written to the URL bar. A
`filter.<key>` has no default to strip: its presence is the selection.

No schema library (zod/valibot) is used for search validation — see ADR-0020.

`/catalogue`'s and `/admin/catalogue`'s search state (ADR-0032) share one shape,
`FilteredListSearch`, with a dynamic set of keys: each selected facet is its own top-level `filter.<key>`
key (`filter.status=draft`, `filter.discipline=chemistry`), never nested under a `filters`
object — `stringifySearch` throws on exactly that shape, since a plain object is not "a
scalar or an array of scalars". Both validators normalise a facet's value to an array
regardless of whether it appeared once or several times in the URL (`parseSearch` gives a
bare string for the former). The admin validator adds an optional `sort`.
`/admin/properties`'s only search param is `deprecated=show`, which reveals deprecated
properties. It is a string flag, not a boolean, so it survives the `stringifySearch` and
`parseSearch` round trip, and any other value reads as absent. The list always fetches the
whole registry, deprecated included, and hides rows in the browser, so one cache entry
serves every screen that reads the registry.

`filterSelections`, `toggleFilterValue` and `clearAllFilters` (`search-params.ts`) serve both
screens; the first two convert the flat validated
search to and from a keyed `Record<string, string[]>` for the filter panel and the API
client's own query params (`api/filter-params.ts` - the one place a `filter.<key>`
parameter name is built by hand for the generated client, since the OpenAPI document can
only type it as a single literal templated field).

## The layout shell

`shell/root-layout.tsx` renders the chrome every route sits inside: `<HeadContent />` (per-
route document title, declared via each route's `head` option), a skip link, `<header>`
with a wordmark, `<nav aria-label="Primary">` and the user menu, `<main id="main-content"
tabIndex={-1}>`, and `<footer>` with `<nav aria-label="Footer">`. Deliberately no `<h1>`
in the shell — each page owns its own, so heading order stays sane as screens are added.

After a client-side navigation there is no full page load to reset focus, so
`useFocusMainOnNavigation` moves focus to `<main>` on every route change after the first
(NFR-31; PRD §17.2 item 4). The primary navigation is shown unconditionally, including
links into the authenticated and admin sections — see "Authentication is structural"
below for why that's fine. The router marks the current page's link with
`aria-current="page"`, and the header underlines it so the cue is not colour alone.

`shell/user-menu.tsx` is the only part of the header that varies with the auth status. It
takes the same footprint in every state so the bar does not shift when the status settles:

| Status | What the header shows |
|---|---|
| `restoring` | An inert, hidden-from-assistive-technology placeholder |
| `signed-out` | Sign in and Register links (the `/sign-in` and `/register` routes start the OIDC redirect) |
| `signed-in` | A button labelled with the user's display name that opens Account and Sign out |
| `unavailable` | Muted text, "Sign-in unavailable", with no `role="status"` |

The signed-in button calls `GET /auth/me` through `useSession`, and only in that state,
so a signed-out or unavailable visitor triggers no extra request. The label falls back
from display name to username to "Your account". The menu is a disclosure (a button with
`aria-expanded` and `aria-controls`), not an ARIA `menu`: it closes on Escape (returning
focus to the button), on a click outside, when focus leaves it, and on any change of
location (page, search or hash). It offers links and never shows roles or permissions;
the server still authorises every request (NFR-20).

The shell's landmarks and skip link are now styled from `src/styles/app.css` (issue #148's
Tailwind adoption, [ADR-0025](../adr/0025-frontend-styling.md)) — see
[components.md](components.md) for the styling strategy and the component baseline built
on it.

## Not-found and error surfaces

Both are wired once, at the router (`defaultNotFoundComponent`, `defaultErrorComponent` in
`router.tsx`), not per-route — a new route inherits them automatically and cannot forget
to wire one (PRD §17.2 item 5).

- **Not found** (`shell/not-found-page.tsx`): `notFoundMode: "fuzzy"` means the nearest
  matching ancestor renders it, so the shell — header, navigation, footer — stays on
  screen and the user has somewhere to go, rather than a blank screen. A route needing a
  more specific message (e.g. "no entry with that business key") can still throw
  `notFound()` from a loader and set its own `notFoundComponent`.
  The entry page (`pages/catalogue-entry.tsx`) has no loader. It renders `NotFoundPage`
  itself when `GET /catalogue/entries/{business_key}` answers 404 or 422, so an API
  refusal and an unmatched URL look the same. The API gives an unknown key and a
  non-public key the identical 404, and a malformed key a 422 whose body has no declared
  shape, so the page cannot tell the cases apart and never reads the 422 body. The same
  mapping applies when a refresh of an entry already on screen answers 404.
- **Route error** (`shell/route-error-page.tsx`): catches any render error thrown inside a
  route. Renders a friendly message and a "Try again" action; logs the real error to
  `console.error` for a developer. It must never render `error.message`, `error.stack`, or
  a raw status code — `router.test.tsx`'s test asserts the exception text is *absent* from
  the DOM, not just that the friendly heading is present (a heading-only assertion would
  still pass with a stack trace printed underneath).

Both are built on `NoticePage`, the same component the stub and sign-in screens use. Each
moves focus to its `h1` on mount (`focusHeading`), because it replaces the page the user
asked for and a keyboard or screen-reader user would otherwise hear nothing change. Each
links back to the landing page. `router.test.tsx` asserts focus on the heading both on a
cold load and after a client-side navigation.

`root-layout.tsx` also moves focus to `<main>` after every navigation
(`shell/use-focus-main-on-navigation.ts`). That hook leaves focus alone when it is
already inside `<main>`, so the heading keeps focus whichever order the two moves
commit in. The heading takes focus once, on mount: going from one unknown URL straight
to another reuses the same screen, so focus stays where it already is, inside `<main>`.

Both set `document.title` themselves via `shell/use-document-title.ts`, rather than relying
on a route's `head` option: they render in place of whatever route was requested, not as a
route of their own, so without this a deep link straight to an unknown or erroring URL
would leave the title at whatever the previous navigation set, or blank on a cold load.

## Authentication is structural

`shell/require-auth.tsx` gates the authenticated and admin routes via `auth/auth-status.ts`
(`useAuthStatus()`). This is presentation only: **NFR-20** requires every request to be
authorised server-side against the internal user record, and no authorisation decision is
ever made in the browser — hiding a UI control is not access control. Not rendering a
screen here does not protect the data behind it; the API endpoints those screens call are
the actual boundary.

**Since issue #41 (OIDC PKCE login)** `useAuthStatus()` reads the real session — see
[authentication.md](authentication.md) and [ADR-0021](../adr/0021-browser-side-pkce-login.md).
The seam held: `auth-status.ts` kept its name and return type, and the route table under
`RequireAuth` did not change. What each status now produces:

| `AuthStatus` | `RequireAuth` renders |
|---|---|
| `restoring` | a "checking your session" notice at the requested URL — the cold-load probe has not answered yet |
| `signed-in` | the route's own screen, at the requested URL |
| `signed-out` | a redirect to `/sign-in?redirect=…`, replacing rather than pushing |
| `unavailable` | a notice at the requested URL — deliberately *not* a redirect, which would loop against a sign-in page that also cannot work |

`restoring` is what makes a cold deep-link work. Tokens live in memory only, so a fresh
page has none even when the Keycloak SSO session is perfectly good; without a status
distinct from `signed-out`, opening `/submissions` in a new tab would redirect to
`/sign-in` and start a full interactive login for a session the user already had.

The redirect fires once per mount, from an effect rather than a route `beforeLoad`. Note
that the effect alone would not have fixed the cold-load bounce — it fires immediately
too; it is the `restoring` status that does, by making "not signed in *yet*" distinct from
"not signed in". The effect is still the right place because the status can change after
the route has matched, which a `beforeLoad` guard would not see. Firing it once also
matters — re-running it as the navigation
lands would read the new `/sign-in?redirect=…` as the place to return to and nest one
encoded copy of the URL inside the next.

`/sign-in`, `/sign-out`, `/register` and `/auth/callback` — reserved as placeholders
by issue #146 — now resolve to real screens at the same paths.

## Serving requirements

Two things a deployment must get right that this issue cannot enforce itself:

- **SPA fallback.** Every non-asset path is a client-side route.
  [`deploy/caddy/Caddyfile`](../../deploy/caddy/Caddyfile) provides the fallback
  (`try_files {path} /index.html`) for everything except `/api/*` and `/assets/*`. A missing
  file under `/assets/` returns 404 on purpose, so a stale hashed URL never receives
  `index.html`. Any other reverse proxy in front of the SPA must do the same.

  Without it, deep-linking to `/catalogue/NPTC-000247` in a fresh session 404s at the
  proxy in production only — `vite dev`/`vite preview`'s default `appType: "spa"` already
  does this locally, so the gap is easy to miss.
- **Base path.** Vite's `base` and `createAppRouter`'s `basepath` (unset today, meaning
  both default to `/`) must move together if the app is ever served from a sub-path.

## Testing

`src/test/render-route.tsx`'s `renderRoute(url)` mounts the **production**
`createAppRouter()` over a fresh `createMemoryHistory` — a cold browser session, exactly
like deep-linking into a new tab, with nothing cached from an earlier route — under
`<StrictMode>`, matching `main.tsx` exactly, and awaits `router.load()` before returning.
Rendering under `StrictMode` is not incidental: it is what caught `root-layout.tsx`'s
original focus-on-navigation bug (a "have we run yet" boolean flipped permanently on
React's double-invoked initial effect, so it stole focus on the very first render); a
helper that skipped it would have let that regress silently again. Tests query by role
(`getByRole`), never by test id, per the repository's existing convention.
`route-tree.test.tsx`'s `it.each` sweep renders every declared route via its typed
`to`/`params`/`search`, so the fixture cannot drift from the route table without failing to
compile, and doubles as the coverage driver for the placeholder factory and the shell.
