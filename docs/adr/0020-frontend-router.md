# ADR-0020: Frontend router — TanStack Router, a hand-written route table, and raw-string search serialisation

**Status:** Accepted
**Date:** 2026-08-19

## Context

ADR-0001 fixes the frontend stack — React 19, TypeScript, Vite, TanStack Query, an
OpenAPI-generated client — but names no router. Issue #146 (routing skeleton and layout
shell) cannot proceed without one: it needs a route table before any screen lands, and
`P1-SEQUENCING.md` makes it a hard gate on #148 (component baseline), #147 (generated API
client), and #41 (OIDC login).

The choice is constrained by more than "pick a router":

- **FR-17 (MUST)** fixes three URL forms as a *public contract* from day one — vendors
  cite these URLs from their own documentation: `/catalogue/{business_key}`,
  `/catalogue/code/{system_token}/{code}`, and `/catalogue/lookup?system={uri}&code={code}`.
- **FR-06** (a hard constraint, CONTRIBUTING.md): a SNOMED CT identifier is a string
  end-to-end, never a number — SCTIDs can exceed `Number.MAX_SAFE_INTEGER` and leading
  zeros are significant.
- **PRD §6.2**: `business_key` (e.g. `NPTC-000247`) is the public identifier; the internal
  UUID must never appear in a route.
- **#140's own acceptance criteria** require the three URL forms to resolve identically,
  and search-result state (query, filters, page) to round-trip through the URL exactly.
- **The issue's own acceptance criterion**: "the route table is the single source of URL
  shapes — no component builds a catalogue URL by string concatenation."
- **NFR-20**: no authorisation decision may be made in the browser; hiding a UI control is
  presentation, not access control. This shapes how the authenticated-route seam is built,
  not the router choice itself, but is a live constraint on the route table's structure.

**Decided with the maintainer:** TanStack Router (over React Router v7); a full P1+ route
skeleton (every URL shape the PRD implies, declared now, with placeholder pages); a
structural `RequireAuth` layout route that #41 fills in without route-table churn.

## Decision

### TanStack Router

Chosen for end-to-end static typing of `to`, `params`, and `search` against the route
tree — the mechanism behind the "no string concatenation" criterion. A route registered
via the `Register` type-map interface makes `<Link to="/catalogue/$businessKey"
params={{businessKey}}>` a compile error if the path or param is wrong, so `pnpm
typecheck` catches what would otherwise only be a review comment.

### A hand-written, code-based route table — not file-based routing

`frontend/src/router/route-tree.ts` is a single file, built with `createRoute`/
`createRootRoute`/`addChildren`. No `@tanstack/router-plugin`, no generated
`routeTree.gen.ts`. Four reasons, in descending weight:

1. **`pnpm typecheck` (`tsc -b --noEmit`) and `pnpm build` (`tsc -b && vite build`) both
   run `tsc` before Vite.** A plugin-generated route tree does not exist on a fresh CI
   clone unless it is committed or a codegen step is added ahead of `tsc`.
2. **Committing a generated file fights this repo's own gates**: `.pre-commit-config.yaml`
   (`mixed-line-ending --fix=lf`, `end-of-file-fixer`), `frontend/eslint.config.js`,
   `.prettierignore` (default `quoteStyle: single` fights this repo's `"singleQuote":
   false`), and the coverage `exclude` list would each need a carve-out for a file with no
   behavioural benefit.
3. **The acceptance criterion asks for it literally.** With a hand-written table, one file
   is the artefact "the route table is the single source of URL shapes" describes — one
   diff, `git blame` on a URL change. File-based routing spreads the same information
   across ~35 filenames plus a large generated file.
4. **35 placeholder routes as 35 files is disproportionate** for scaffolding that ships no
   real screens.

### Raw-string search serialisation

`router.tsx` overrides `parseSearch`/`stringifySearch` with hand-rolled functions built
directly on `URLSearchParams`, bypassing `@tanstack/router-core`'s built-in `qss`-based
codec entirely. This was **not** an upfront design choice — it was forced by a defect found
while writing `route-tree.test.tsx`'s round-trip tests:

`qss.decode()`'s `toValue()` step coerces any numeric-looking query value to a real JS
number, *before* a custom `parseSearch` parser argument ever runs — passing an identity
function as that parser does not intercept it. Concretely: `?page=3` arrives as the number
`3` regardless of the `parseSearch` option, and `?code=123456` (no leading zero, within
safe-integer range) would too — silently, the exact defect class FR-06 exists to
eliminate. An 18-digit SCTID happens to survive by accident (float rounding breaks `qss`'s
own encode/decode round-trip check, so it falls back to a string) — that is luck, not a
guarantee, and a leading-zero code is saved by a different `qss` special case, not by the
`parseSearch` override at all. The fix bypasses `qss` completely: every search value is a
raw string coming in, and is stringified via plain `String()`/`URLSearchParams` without
JSON-quoting going out.

A second, related defect surfaced by the same tests: TanStack Router invokes a route's
`validateSearch` more than once per navigation (once during lightweight route matching,
again while committing the location), and the second call passes the validator's own
previously-validated output back in — `page` arrives as the *number* the validator itself
returned, not the original string. The initial `asPage` implementation rejected any
non-string input outright (a defensive measure against exactly the `qss` coercion above),
which made it non-idempotent: a valid `page=3` silently defaulted back to `page=1` on the
second call. `validateSearch` must be idempotent; `asPage` now accepts an already-valid
number as well as a numeric string. Both defects are guarded by tests
(`route-tree.test.tsx`'s round-trip assertions; `search-params.test.ts`'s explicit
idempotency test) precisely because both are silent failure modes with no visible symptom
until a released export or a shared link is subtly wrong.

### Hand-rolled `validateSearch`, no schema library

`search-params.ts` validates `/catalogue` and `/catalogue/lookup`'s search params with
plain functions, not zod/valibot. The rule being enforced — never coerce a code — is a
schema library's least ergonomic mode: the convenient path in most of them
(`z.coerce.number()`) is the exact hazard this file exists to avoid. `@tanstack/react-router`
is this repository's first frontend runtime dependency beyond React itself; a second
dependency for two routes' worth of scalar parsing is disproportionate. This is reversible:
if a schema library earns its place later (e.g. once request/response validation is needed
for the generated API client), adopting one is a `validateSearch` change with no
route-table churn.

### Router-level, not per-route, not-found and error defaults

`defaultNotFoundComponent`/`defaultErrorComponent` are set once on the router
(`createAppRouter` in `router.tsx`), not repeated per route, so a new screen inherits both
automatically and cannot forget to wire one (PRD §17.2 item 5). `notFoundMode: "fuzzy"` (the
library default, stated explicitly) means the nearest matching ancestor renders the
not-found page, keeping the shell — header, navigation, footer — on screen rather than a
blank page.

## Rejected alternatives

- **React Router v7** — the maintainer's explicit choice against it. Fair case for it: the
  largest ecosystem, the most familiar to most React developers, and "framework mode"
  bundles file-based routing with data loading in one package. It lost here because `to`,
  `params`, and search carry no end-to-end static type against the route tree — the "no
  string concatenation" criterion would be a review convention, not a `pnpm typecheck`
  failure — and it has no first-class, typed search-param validation, so FR-06's
  never-coerce rule would need re-asserting by convention at every call site rather than
  being centralised in one file.
- **File-based routing via `@tanstack/router-plugin`** — see "A hand-written, code-based
  route table" above.
- **zod / valibot for `validateSearch`** — see "Hand-rolled `validateSearch`" above.
- **`wouter` / a hand-rolled `history`-based router** — no typed params, no search
  validation, no route-level error boundaries out of the box; #140's search-state-in-URL
  criterion and FR-17's three URL forms would have to be hand-built regardless, without
  gaining type safety in return.
- **TanStack Router devtools now** — genuinely useful, but adds a dependency and its
  styling stack for a tree of placeholder screens. Deferred to the issue that lands the
  first real data-loading screen.

## Consequences

- **Every URL shape lives in one file** (`route-tree.ts`); a URL change is one diff, and
  `noUnusedLocals` makes a route declared but forgotten from `addChildren` a `tsc` error —
  a compiler-enforced correctness check, not just a convention.
- **No automatic code splitting** (`autoCodeSplitting`, which the file-based plugin would
  provide) — irrelevant while every route is a placeholder; reversible per-route later via
  `route.lazy()`, or by migrating to file-based routing once screens are real.
- **The custom search serialisation must survive refactors.** It looks like boilerplate
  and is easy to "simplify" back to `parseSearchWith`/`stringifySearchWith` — doing so
  silently reintroduces SCTID precision loss with no visible symptom until a released
  export or shared link is wrong. `route-tree.test.tsx`'s round-trip tests are the guard.
- **Every future `validateSearch` must be written to be idempotent** — accepting both a
  raw string and its own previously-validated output for the same field. This is not
  obvious from TanStack's documentation and cost real debugging time here; it should not
  cost it again.
- **Adopting a schema library later is a `search-params.ts` change only.**
- **Deploying behind Caddy needs an SPA fallback** (`try_files {path} /index.html`) before
  this ships to production — `deploy/` has no Caddyfile yet. Documented as an explicit
  requirement in `docs/architecture/frontend-routing.md`'s "Serving requirements" section,
  and left as a checklist item for whichever issue introduces the Caddy service.
