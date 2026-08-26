# Accessible component baseline

Issue #148. See [ADR-0025](../adr/0025-frontend-styling.md) for the styling choice
(Tailwind v4) and its rejected alternatives; this document is the component baseline
itself.

## Scope

NFR-31 requires WCAG 2.2 Level AA, "verified by automated testing in CI plus a manual
keyboard and screen-reader pass in P5." This issue lands the automated half and the five
components later screens are expected to compose: a form field, a button, a modal dialog,
a data table, and a live region for async announcements. The manual keyboard and
screen-reader audit itself is [P1-SEQUENCING.md](../../P1-SEQUENCING.md)'s later P5 work,
not this issue.

(The issue body cites NFR-19, the data-breach-response procedure — unrelated. The
requirement this baseline implements is NFR-31.)

## The rule: screens compose these, they do not reach for raw elements

A screen builds its markup from `frontend/src/components/`, not from a bare `<button>`,
`<input>`, `<dialog>`, `<table>`, or a live region assembled by hand. The reasoning is the
same one behind the route table (ADR-0020) and the permission framework
(`docs/architecture/permissions.md`): centralise a correctness property in one place so it
is enforced once, rather than re-derived — and potentially gotten slightly wrong — on
every screen that needs it.

This is a documented convention plus a lint backstop, not a hard ban:
`eslint-plugin-jsx-a11y`'s recommended rule set (`frontend/eslint.config.js`) catches many
raw-element mistakes (a missing label association, a non-interactive element with a click
handler, a positive `tabindex`) even where a screen does reach for a raw element for a
genuine one-off. Review is still what catches "this should have been `<Button>`."

## The five components

All in `frontend/src/components/`, each with a co-located `*.test.tsx` that asserts both
its behavioural contract and `expectNoA11yViolations` (`frontend/src/test/a11y.ts`, a thin
wrapper over `axe-core` — chosen over the `vitest-axe` matcher package for direct control
over rule configuration).

- **`field.tsx` — `Field`.** Generates the label/control association via `useId` rather
  than leaving it for a caller to wire by hand, and threads an optional hint and error
  message through `aria-describedby`/`aria-invalid`. Takes the control itself as a
  render-prop so it composes with any input type (`<input>`, `<select>`, `<textarea>`)
  without `Field` needing to special-case each one.
- **`button.tsx` — `Button`.** `type` is a required prop, not defaulted: an untyped
  `<button>` inside a `<form>` defaults to `type="submit"`, a frequent source of an
  accidental submit on what was meant to be a plain action button.
- **`dialog.tsx` — `Dialog`.** A modal that prefers the native `<dialog>` element's
  `showModal()` where the runtime supports it, but does not depend on it for the focus
  contract — `showModal()`/the browser's own `Tab` trap are not implemented in jsdom, so
  focus-into-dialog, the `Tab` trap, `Escape`-to-close, and focus-restore-to-trigger are
  all handled explicitly. That keeps the contract testable in CI regardless of what a given
  browser does natively on top of it.
- **`data-table.tsx` — `DataTable`.** A required `caption`, `scope="col"` on every column
  header, `scope="row"` on the column designated as the row header, and an explicit
  empty-state row rather than a headers-only table when there are no results.
- **`live-region.tsx` — `LiveRegion`, paired with `use-announce.ts` — `useAnnounce`.** The
  region is always mounted, present and empty, rather than inserted into the DOM only at
  announce time — a screen reader reliably picks up a text change inside a region that was
  already present when the page loaded, and is not guaranteed to for one created in the
  same tick it is populated. `useAnnounce` owns the message/politeness state; a screen
  calls `announce()` when an async result (a save, a search) arrives.

## Known limits of the automated check

- **`axe-core` cannot evaluate `color-contrast` under jsdom** — the rule needs real layout
  and computed rendering, which jsdom does not provide. It is disabled explicitly in
  `frontend/src/test/a11y.ts`, with a comment there rather than silently skipped. Contrast
  is instead carried by the `--color-*` tokens declared in `frontend/src/styles/app.css`'s
  `@theme` block, and is confirmed in the P5 manual pass — CI passing does not mean
  contrast has been verified, and this document is where that limit is written down rather
  than implied by a green check.
- The automated check runs component-by-component, in isolation. It catches what is wrong
  with a component's own markup; it cannot catch a whole-screen defect (heading order
  across several components, a focus order that only breaks once components are combined).
  That is exactly the kind of defect the P5 manual pass exists to find.
