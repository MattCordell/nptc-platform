# Accessible component baseline

Issue #148. See [ADR-0025](../adr/0025-frontend-styling.md) for the styling choice
(Tailwind v4) and its rejected alternatives; this document is the component baseline
itself.

## Scope

NFR-31 requires WCAG 2.2 Level AA, "verified by automated testing in CI plus a manual
keyboard and screen-reader pass in P5." Issue #148 landed the automated half and five
components later screens are expected to compose: a form field, a button, a modal dialog,
a data table, and a live region for async announcements. Issue #210 completes the set the
entry-edit screens need — a select, the choice controls, a form wrapper and an error
summary. The manual keyboard and screen-reader audit itself is later P5 work, not either
issue.

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

## The baseline components

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
  accidental submit on what was meant to be a plain action button. `aria-disabled`
  gets the same unavailable styling as `disabled` — reach for it whenever a button
  turns unavailable *under* the user, as a submit or a Cancel does mid-save, because
  `disabled` removes the control from the tab order and strands their focus. It styles
  the control only: refusing the action stays the caller's job (`Form` guards re-entry
  in its own submit handler), so a `type="button"` action needs its `onClick` guarded
  too.
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

## The form primitives

Issue #210. Same rules as above: co-located test, `expectNoA11yViolations`, no raw
element where a primitive exists. [ADR-0026](../adr/0026-form-primitives-without-a-form-library.md)
records why there is no form library behind these and what was rejected.

- **`select.tsx` — `Select`.** A native `<select>` composed over `Field`, not a custom
  `role="listbox"`: the browser's own control already carries keyboard operation,
  type-ahead and the platform's mobile picker. Its optional `placeholder` is an empty
  first option that is deliberately *not* disabled — the HTML selectedness algorithm
  skips disabled options when picking a default, so a disabled placeholder is passed
  over and the first real option silently becomes the answer.
- **`checkbox.tsx` — `Checkbox`.** A single labelled box. The one primitive that does not
  compose `Field`, because a checkbox's label belongs after the box and `Field` renders
  the label first by construction; it repeats `Field`'s id scheme and `aria-describedby`
  ordering exactly so the two cannot drift.
- **`checkbox-group.tsx` — `CheckboxGroup`** and **`radio-group.tsx` — `RadioGroup`.**
  Both labelled by a `<fieldset>`/`<legend>` — a screen reader announces a legend on
  entering the group and ignores a nearby paragraph, so adjacent text is not a group
  label. They share `ChoiceOption` (`choice-option.ts`) so the two cannot take subtly
  different option shapes for the same job. `CheckboxGroup` keeps a tab stop per box
  (each is an independent yes/no answer); `RadioGroup` rovers, so a five-option group is
  one `Tab` stop rather than five, with the arrow keys traversing it. A group's hint and
  error are wired to each `<input>`, not to the `<fieldset>` — NVDA and JAWS commonly skip
  `aria-describedby` on a `group`, so a description there is visible but silent. A
  `CheckboxGroup` also carries through a held value that is no longer an offered option
  (a retired code on a stored entry) rather than dropping it when the user ticks something
  unrelated.
- **`error-summary.tsx` — `ErrorSummary`.** One list of everything that failed, at the top
  of the form, each item a link to the control it names. Focusable (`tabIndex={-1}`) but
  not `role="alert"`: a form moves focus here on a failed submit, which announces the
  region once — an alert role on top announces it twice. Its heading level is a prop
  (default 2), because a form inside a `Dialog` or a nested section would otherwise carry
  a hardcoded `<h2>` into a heading-order violation the per-component axe run cannot see.
- **`form.tsx` — `Form`.** Owns the `<form>`, renders its own submit button, guards a
  double submit, and moves focus to the summary when a submit is answered with errors.
  It renders the submit button rather than accepting one as a child so that "one submit
  path" and "submitting is refused while a save is in flight" are structural rather than
  conventions a screen has to remember; `secondaryActions` is the escape hatch for Cancel
  and friends. While pending, that button is `aria-disabled`, not `disabled` — a
  `disabled` control leaves the tab order mid-save and drops the keyboard user's focus to
  `<body>` with nothing announced; the submit handler's own guard is what refuses the
  second submit. `submitBlocked` (issue #62) is the same idea for a caller-side gate
  computed before any submit is attempted — a missing or invalid changelog note, today —
  joining `pending` on `aria-disabled` and in the re-entry guard; `blockedReason` (with
  `blockedFieldId`, to make it a real summary link rather than a plain sentence) is
  announced only once an attempted submit is actually refused, per ADR-0026's amendment.
  `onSubmitBlocked` is called in place of `onSubmit` on a blocked attempt, so a caller with
  its own extra field validation can recompute and display it the same way a non-blocked
  submit does, per ADR-0026's addendum.

### Composing a form

Two conventions a screen author needs, neither obvious from the call site:

- **Give every control an `id` you chose.** `Field`, `Select`, `Checkbox`,
  `CheckboxGroup` and `RadioGroup` all generate one with `useId` when you do not — but an
  `ErrorSummary` item links to `#id`, and it cannot link to an id only the component
  knows. The same id goes in the `FormError.fieldId` the summary is given.
- **A group's `id` lands on its first option's `<input>`, not on the `<fieldset>`.** A
  summary link has to send focus somewhere that announces something useful: focusing a
  fieldset announces nothing, whereas focusing the first radio announces the legend as
  the group it belongs to, then the option. Remaining options derive `${id}-1`,
  `${id}-2`, and so on.

A rejected save goes in `Form`'s `formError`, not on a control: the API's error shape is
`ErrorResponse { detail: string }` with no per-field `loc` (deliberately — FR-44,
NFR-04), so a server refusal has no field to attach to. `Form` keeps listening for a
submit's answer until an error actually arrives, however many renders later, and does not
require the caller to set `pending` for that to work — `pending` drives the button and
`aria-busy` only.

**`Form` assumes validate-on-submit.** Because it stays armed until an error arrives, a
screen that also validated on *change* would have the next keystroke that produces an
error pull focus out of the input being typed in — a recurring surprise, not a one-off.
Validate on submit; issue #214 tracks removing the restriction.

## Known limits of the automated check

- **`axe-core` cannot evaluate `color-contrast` under jsdom** — the rule needs real layout
  and computed rendering, which jsdom does not provide. It is disabled explicitly in
  `frontend/src/test/a11y.ts`, with a comment there rather than silently skipped. Contrast
  is instead carried by the `--color-*` tokens declared in `frontend/src/styles/app.css`'s
  `@theme` block, and is confirmed in the P5 manual pass — CI passing does not mean
  contrast has been verified, and this document is where that limit is written down rather
  than implied by a green check.
- **A group's hint and error are announced once per option.** Wiring them to each
  `<input>` is what makes them announced at all — a `group` role's description is
  inconsistently supported — but the consequence is that a user tabbing through a
  six-option group hears the full hint and error six times. If that grates in the P5
  manual pass, the usual refinement is to describe only the *first* option by the hint
  while keeping the error on all of them. That is a change to make with a real screen
  reader in front of you, not on this reasoning alone.
- **jsdom does not implement native radio behaviour** — neither the roving tabindex nor
  arrow-key traversal. `RadioGroup` therefore implements both itself and calls
  `preventDefault()` on the keys it handles, so a real browser's identical native
  behaviour cannot fire alongside it. This is the same call `dialog.tsx` makes about
  `showModal()` and the `Tab` trap: a contract the runtime supplies invisibly is a
  contract CI cannot assert, so the component owns it explicitly instead.
- The automated check runs component-by-component, in isolation. It catches what is wrong
  with a component's own markup; it cannot catch a whole-screen defect (heading order
  across several components, a focus order that only breaks once components are combined).
  That is exactly the kind of defect the P5 manual pass exists to find.
