# ADR-0025: Frontend styling — Tailwind CSS v4

**Status:** Accepted
**Date:** 2026-08-26

## Context

ADR-0001 fixes the frontend stack — React 19, TypeScript, Vite, TanStack Router/Query, an
OpenAPI-generated client — but names no styling approach. The frontend's only stylesheet
to date, `frontend/src/shell/shell.css` (issue #146), is deliberately minimal and says so
in its own header comment: it supplies just enough for the layout shell's landmarks and
skip link to be usable, and issue #148 (this one) "owns the platform's real styling
strategy and should replace this file rather than build on top of it."

Issue #148 lands the accessible component baseline — field, button, dialog, data table,
live region — that every later screen (P1's catalogue browse/search/detail, P2+'s editing
and review workflows) is expected to compose rather than reinvent. Whatever styling
approach these five components use, every screen built after them inherits it; changing
approach later means restyling every component and every screen built against them, not
just adding an ADR.

## Decision

**Tailwind CSS v4**, adopted via `@tailwindcss/vite` (no separate PostCSS config needed
in v4) with CSS-first `@theme` configuration (there is no `tailwind.config.js` in v4 — see
`frontend/src/styles/app.css`).

The trade-off was put to the maintainer directly, since the repository's established
pattern elsewhere (the hand-rolled `search-params.ts`, ADR-0020's rejection of a schema
library) is to prefer no new dependency when the problem is small:

- **The case for plain CSS with custom-property tokens** (the smaller-footprint choice,
  matching the rest of the stack): no new dependency; five small, accessibility-focused
  components don't yet generate a class-naming problem large enough for utilities to pay
  for themselves; the contrast/focus-ring rules that matter most for NFR-31 get hand-written
  either way.
- **The case for Tailwind**: no class-naming problem to invent naming conventions for;
  automatic dead-style elimination as components are deleted; the spacing/colour scale is
  enforced by the utility set itself rather than left to convention; responsive and
  `focus-visible` variants are a prefix rather than a hand-written media query or
  pseudo-class block; it is the ecosystem default a future contributor is most likely to
  already know.

**Decided with the maintainer:** Tailwind now, accepting the new dependency and this ADR,
specifically so no screen from #149 onward is ever written in a different styling
convention than the baseline components it composes.

### What stays hand-written regardless

Adopting Tailwind does not remove the need to hand-write the handful of rules that are
accessibility-critical and easy to get wrong with a generic utility:

- The global `:focus-visible` ring (`src/styles/app.css`) is declared once, at the root,
  specifically so no component's own styling can suppress or weaken it — the "visible
  focus indicator that survives the component's own styling" acceptance criterion.
- The `visually-hidden` and `skip-link` patterns carried over from `shell.css`, expressed
  as Tailwind v4 `@utility` blocks so they stay single-sourced rather than duplicated per
  component.
- Colour contrast is carried by the `--color-*` token choices in the `@theme` block, not
  verified by tooling — see `docs/architecture/components.md` and the note in
  `frontend/src/test/a11y.ts` on why `axe-core` cannot check contrast under jsdom.

## Rejected alternatives

- **Plain CSS with custom-property tokens** — the case for it is real (see above) and would
  have been the better choice if the component baseline were the platform's styling
  surface for good. It was rejected because it is not: #149 onward is the catalogue
  browse/search/detail screens the PRD describes, and by the time that surface is large
  enough to make Tailwind's naming-elimination benefit obvious, migrating away from plain
  CSS costs more (every component and every screen already built against it) than adopting
  Tailwind now costs (one dependency, one ADR, five components).
- **CSS Modules** — no new dependency (Vite supports `*.module.css` natively) and scoped
  class names avoid the naming-collision problem, but the generated class-name identifiers
  diverge from the plain-CSS-like ergonomics of the rest of the shell, and it does not
  address the underlying "invent a name for every visual variant" cost the utility
  approach removes outright.

## Consequences

- `frontend/src/shell/shell.css` is deleted; its three rules (skip-link, visually-hidden,
  focus ring) move into `frontend/src/styles/app.css`'s `@theme`/`@utility` blocks.
- `pnpm-lock.yaml` changes (new `tailwindcss`, `@tailwindcss/vite`, `prettier-plugin-tailwindcss`
  entries), which re-triggers `.github/workflows/openapi.yml`'s schema-staleness check and
  `.github/workflows/security.yml`'s `pnpm audit --prod` — both expected to pass, since
  `tailwindcss`/`@tailwindcss/vite` ship no runtime code into the built bundle (Tailwind is
  a build-time CSS generator) and `prettier-plugin-tailwindcss` is dev-only.
- `prettier-plugin-tailwindcss` is added so class order inside `className` is deterministic
  and cannot make `pnpm format:check` (a CI gate) flap on ordering alone.
- Every component and screen written after this lands is expected to use Tailwind
  utilities, not a new hand-written stylesheet — see
  `docs/architecture/components.md`.
