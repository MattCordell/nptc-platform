# Accessible component baseline

Issue #148. See [ADR-0025](../adr/0025-frontend-styling.md) for the styling choice
(Tailwind v4) and its rejected alternatives; this document is the component baseline
itself.

## Scope

NFR-31 (WCAG 2.2 Level AA) is SHOULD and in progress at option B scope. It was deferred
on 2026-09-09 while the user base was small and known; the anonymous catalogue browser is
a public-facing view, which is the re-entry condition recorded then. The definition of
option B lives once, in the NFR-31 entry of `docs/requirements/requirements.yaml`; PRD
§13.6 summarises it and CONTRIBUTING.md holds the per-change checklist. In short, this
baseline and its axe-core assertions are step one (build it in). A real-browser axe run
with colour contrast on is step two. One manual keyboard and screen-reader pass of the
public and login screens, before public launch, is step three. A formal audit and a
conformance statement stay deferred.

Issue #148 landed the automated half and the first components later screens are expected
to compose: a form field, a button, a modal dialog, a data table, and a live region for
async announcements. Issue #210 completes the set the entry-edit screens need — a select,
the choice controls, a form wrapper and an error summary. The layout primitives (a page
container, a page header and a card) round out the set; see "Layout primitives" below.
A search input, a filter bar and keyset pagination follow; see "Search and paging
primitives" below.

Three properties below are not yet confirmed in a real browser or with a screen reader:
colour contrast (the token pairs are checked arithmetically, but not as rendered), a
repeated-hint pattern for option groups, and whole-screen defects (heading order, focus
order across combined components) that no per-component check can see. Each is
**unverified** today. The real-browser run covers contrast and whole-screen structure
once it exists, and the manual pass covers the screen-reader properties. Each note below
names the check that will confirm it. Do not read the absence of a documented problem as
evidence one of these is fine.

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

Alongside "compose from `components/`", a screen also has to match the design system: see
[design-system.md](design-system.md) for the palette, type and layout vocabulary these
components draw their tokens from.

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
  too. `button-class-name.ts` exports the base + variant classes `Button` renders
  (`buttonClassName`) for a call site that needs the same look on an element `Button`
  can't be — e.g. a router `Link` styled as an action (`pages/home.tsx`'s register/
  sign-in/sign-out actions) — so nothing hand-copies the variant classes into a second,
  divergent copy. Kept out of `button.tsx` itself: mixing a component export with a
  plain function export in one file defeats Fast Refresh.
- **`dialog.tsx` — `Dialog`.** A modal that prefers the native `<dialog>` element's
  `showModal()` where the runtime supports it, but does not depend on it for the focus
  contract — `showModal()`/the browser's own `Tab` trap are not implemented in jsdom, so
  focus-into-dialog, the `Tab` trap, `Escape`-to-close, and focus-restore-to-trigger are
  all handled explicitly. That keeps the contract testable in CI regardless of what a given
  browser does natively on top of it.
- **`data-table.tsx` — `DataTable`.** A required `caption`, `scope="col"` on every column
  header, `scope="row"` on the column designated as the row header, and an explicit
  empty-state row rather than a headers-only table when there are no results. A column's
  optional `align` sets both the header and data cell's text alignment, for the mono
  left-aligned / numeric right-aligned convention in
  [design-system.md](design-system.md#layout-patterns).
- **`status-badge.tsx` — `StatusBadge`.** A lifecycle-status pill, taking a `tone` rather
  than a catalogue status value so it stays a generic primitive — `statusToneFor` in
  `frontend/src/catalogue/status-options.ts` maps the real `CatalogueEntryStatus` values
  onto its four tones. See [design-system.md](design-system.md) for the tone colours.
- **`live-region.tsx` — `LiveRegion`, paired with `use-announce.ts` — `useAnnounce`.** The
  region is always mounted, present and empty, rather than inserted into the DOM only at
  announce time — a screen reader reliably picks up a text change inside a region that was
  already present when the page loaded, and is not guaranteed to for one created in the
  same tick it is populated. `useAnnounce` owns the message/politeness state; a screen
  calls `announce()` when an async result (a save, a search) arrives.
- **`markdown.tsx` — `Markdown`.** Renders the small subset the terms of use are written
  in (NFR-47): headings, paragraphs, bulleted and numbered lists, `**bold**`, `*italic*`,
  `` `code` `` and `http:`, `https:` and `mailto:` links. A list may have blank lines
  between its items, and a numbered list starts at the number its first item carries. It
  builds elements and never sets inner HTML, so raw HTML in the text shows as text, and any
  other link target shows as plain text. `headingOffset` (default 1) pushes headings below the page's one `h1`. It is not a
  general Markdown engine: tables, images, nested lists and reference links are not
  supported, so a new terms version must stay within the subset.

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
- **`input-classes.ts` — `INPUT_CLASSES`.** The text-input look as one class string, not a
  component. Put it on every text-like `<input>` (`text`, `number`, `url`, `search`) you
  render inside `Field` or beside a label of your own, so the screen needs no style of its
  own and a bare input never falls back to browser defaults. It sets the 4px control
  radius (`--radius-control`), the 1px border, the surface fill, 16px text (`text-base`)
  and a 40px minimum height. `Select` shares the same radius and text size, but not the
  rest: it has no minimum height (it renders about 40px, an input about 42px) and it shows
  a danger border on error, which `INPUT_CLASSES` does not. Checkboxes and radios are not
  text-like: use `Checkbox` and `RadioGroup`.
  `frontend/tests/catalogue-inputs-use-shared-style.test.ts` fails if a text-like
  `<input>` under `src/catalogue` omits the class. The string includes `w-full`, so to narrow an input,
  constrain the element that wraps it instead of adding a competing width class (the
  order of classes in a string does not decide which wins). The values behind it are in
  [design-system.md](design-system.md#layout-patterns).
- **`checkbox.tsx` — `Checkbox`.** A single labelled box. The one primitive that does not
  compose `Field`, because a checkbox's label belongs after the box and `Field` renders
  the label first by construction; it repeats `Field`'s id scheme and `aria-describedby`
  ordering exactly so the two cannot drift. A visible label has a 24px minimum height,
  because the 16px box alone is below the WCAG 2.5.8 target size and the label toggles it
  too. A visually-hidden label is left alone.
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

**`Form` supports validate-on-change through `SubmitOutcome`.** `onSubmit` returns either
nothing or a promise that resolves a `SubmitOutcome`, `{ ok: boolean }`:

| `onSubmit` returns | Focus flag | Use it when |
|---|---|---|
| nothing | stays armed until an error arrives, however many renders later | `formError` comes from a mutation hook (every panel today) |
| a promise resolving `{ ok: true }` | disarms, focus does not move | the screen validates on *change*, so a later keystroke error must not steal focus |
| a promise resolving `{ ok: false }`, or rejecting | stays armed | the save failed and the error may commit on a later render |

Anything else a promise resolves is read as `{ ok: false }`: an unannounced error is the
defect, a stale armed flag is not. A rejection is handled inside `Form`, so it never
surfaces as an unhandled rejection.

The type is what protects the hook-driven panels. A bare `mutateAsync()` promise resolves
the saved record, not an outcome, so `return add.mutateAsync(...)` fails to compile rather
than silently losing the announcement. Map it instead:
`.then(() => ({ ok: true }), () => ({ ok: false }))`. An `async` `onSubmit` that returns
nothing is also a type error. The type is what protects a panel; no test can observe it.
`SlowRefusingPromiseForm` in `form.test.tsx` pins each outcome, and
`admin-catalogue-edit.test.tsx` ("moves focus to the summary when the server refuses the
save") pins the void path's late-refusal announcement for a hook-driven panel. See
ADR-0026's 2026-10-02 addendum for the alternatives rejected.

The registry screens (`admin-property-create.tsx`, `admin-property-edit.tsx` and
`registry/deprecate-property-dialog.tsx`) are the first callers of the promise path. Each
validates on submit, returns nothing when its own checks fail, and otherwise returns the
`mutateAsync` promise mapped to an outcome. A save that navigates away calls
`registry/release-focus.ts`'s `releaseFocus()` first: the submit button still holds focus
when the route changes, so `useFocusMainOnNavigation` leaves focus alone and it then falls
to `<body>` as the button unmounts. Blurring first lets that hook move focus to `<main>`.

## Layout primitives

Issue #427. Presentational components that carry the page structure from
[design-system.md](design-system.md#page-layout), so a screen composes them instead of
hand-rolling Tailwind layout. Same rules as above: a co-located test, an
`expectNoA11yViolations` call, and design tokens only. Each test also asserts through the
class string that no hex value, Tailwind palette class or `shadow-*` class is used
(`expectTokenClassesOnly` in `frontend/src/test/token-classes.ts`).

- **`page-container.tsx` — `PageContainer`.** The page-width wrapper: centred, capped at
  the `--container-page` token, with a 24px gutter and 24px between its children. A `div`,
  not `main` — `root-layout.tsx` already provides the one `<main id="main-content">`
  landmark. Its gutter stacks on the `main { padding: 1rem }` base rule in `app.css`.
- **`page-header.tsx` — `PageHeader`.** The page's single `h1`, an optional muted meta
  line and optional actions on the right. The title and meta come before the actions in
  DOM order, so keyboard order matches reading order. An optional `id` lands on the `h1`
  so a screen keeps its `aria-labelledby` wiring; the component generates none. It renders
  no empty meta or actions element when those props are absent, and it is deliberately not
  a `<header>` element, because `app.css` pads every `header` globally. `focusable`
  renders `tabindex="-1"` on the `h1`, so a screen can move focus to it without adding
  a tab stop.
- **`card.tsx` — `Card`.** A white surface with a 1px border and the 6px card radius, one
  24px padding and no shadow. It spreads native `div` props, so a caller adds `role` or
  `aria-labelledby` when a card is a labelled region.
- **`notice-page.tsx` — `NoticePage`.** A whole screen that says one thing and offers a way
  on: a `PageContainer`, a `PageHeader` (the one `h1`) and a `Card` holding the message,
  then the actions, in that DOM order. The stub placeholder, not-found, route-error and
  all the sign-in, registration, sign-out and auth-callback states use it, so they look
  alike. `focusHeading` makes the `h1` focusable (`PageHeader`'s `focusable`, which
  renders `tabindex="-1"`) and moves focus to it once on mount
  (`use-focus-heading-on-mount.ts`); set it only on a screen that replaces the page the
  user asked for.
- **`back-to-landing-link.tsx` — `BackToLandingLink`.** The "Back to the landing page" link,
  styled as a button, so the wording is the same on every such screen.

## Entry detail primitives

Issue #440. The pieces of the public entry page that another screen can reuse. Same rules
as above: a co-located test, an `expectNoA11yViolations` call and design tokens only.

- **`breadcrumb.tsx` — `Breadcrumb`.** A `nav` labelled "Breadcrumb" holding an ordered
  list. It takes `ancestors`, each already a link, and the `current` page as plain text
  marked `aria-current="page"`. The caller builds the links with the router's typed
  `<Link>`, so the component holds no URL. The separators are drawn by the component and
  hidden from assistive technology. Each link is at least 24px tall.
- **`code-chip.tsx` — `CodeChip`.** A code in the `--color-code-text` and `--color-code-bg`
  tokens, monospaced, left-aligned and never truncated. Its `code` prop is a `string`,
  never a number, and it renders the text as given (FR-06). Use it for any SNOMED CT code
  shown on its own.
- **`detail-layout.tsx` — `DetailLayout`.** The two-column body. It takes the main column
  as `children`, a `sidebar` and a `sidebarLabel` that names the sidebar's `aside`
  landmark. Below the large breakpoint the sidebar stacks under the main column, so the
  reading order never changes. `minmax(0, 1fr)` lets the main column shrink, so a wide
  table scrolls inside its own `overflow-x-auto` wrapper instead of widening the page.
- **`finding-indicator.tsx` — `FindingIndicator`.** "Open finding" in the danger tokens with
  a mark, or "None" when `open` is false (FR-18). It carries no detail about a finding. The
  search results table and the entry page both use it.

The sections of the entry page itself live in `frontend/src/catalogue/entry-*.tsx`. They
render what the API sends and nothing else: a property value goes through
`format-property-value.ts` whatever its type, because a branch on `datatype` is what
ADR-0013 forbids, and a fully specified name is shown exactly as served (FR-83).

`entry-audit-trail.tsx` (`EntryAuditTrail`, issue #441) is the audit trail pattern from the
design system: an ordered list of an entry's changes with a rule down the left, each with a
monospace time in UTC+10, the author when the API sends one, what happened, the fields and the
reason. It only renders events. `pages/entry-history.tsx` owns the query, the `before` cursor in
the URL and the Previous stack, and keeps the page on screen while the next one loads
(`useEntryHistory`'s `keepPreviousPage`), so a focused paging button is not unmounted.

## Search and paging primitives

Issue #438. The search box, filter controls and keyset paging that listings share, so the
public catalogue browser, the audit log and the admin list compose one version of each.
Same rules as above: a co-located test with an `expectNoA11yViolations` call and a keyboard
test, design tokens only (`expectTokenClassesOnly`), and every target at least 24px
(`min-h-10` on inputs and buttons, `min-h-8` on pills). None holds state; the caller owns
the value, the selection and the cursor, usually in the URL.

- **`search-input.tsx` — `SearchInput`.** A labelled search field and a submit button in
  a `role="search"` form, so it is a landmark. Controlled: it takes `value` and
  `onValueChange`, and calls `onSubmit` with the trimmed value from the button or Enter.
  The input is `type="search"`, so its role is `searchbox`.
- **`filter-bar.tsx` — `FilterBar`.** One removable chip per active filter, in a group named
  "Active filters", with a "Clear all filters" button (FR-16). It draws no controls: the
  caller picks values with its own, `MultiSelectCombobox` on the public catalogue page and a
  facet panel on the admin list, and passes the selection here. Chips are pills (20px radius,
  at least 32px high).
- **`multi-select-combobox.tsx` — `MultiSelectCombobox`.** A labelled, type-to-narrow
  combobox that picks several values, built on Base UI's `Combobox` (the trial passed under
  jsdom and axe; Headless UI and React Aria Components were not tried). It lists every option
  as "Label (n)", narrows the list in the browser as the user types, and stays open after a
  pick so several values come from one query. A selected option carries a check mark as well
  as its fill (NFR-31). It holds no selection and draws no chips: the caller owns `selected`
  and is told each change through `onToggle`, and shows the selection with `FilterBar`'s chip
  row, so a selection is removable from both. A selected value missing from `options` stays selected, but the popup cannot list it, so the chip is then the only way to remove it. With no `options` at all the popup says "No other values match this search." instead of blaming the typed text. At `maxSelected` it refuses a
  further pick with a message inside the popup, because Base UI marks everything outside the
  popup inert while it is open, so a live region outside would not be read. For the same
  reason it names the input with `aria-label`, since the visible label is hidden from
  assistive technology while the popup is open.
- **`pagination.tsx` — `Pagination`.** Previous and Next for keyset paging: no page
  numbers and no total, because the API returns neither (ADR-0024). It takes `hasNext`,
  `onNext` and an optional `onPrevious`, and names no URL parameter, so a screen paging by
  `after` and one paging by `before` both use it. When `hasNext` is false it says "No more
  results" in text and the Next button points to that text with `aria-describedby`. An
  unavailable button uses `aria-disabled`, so it stays focusable, and the component
  ignores its clicks. The server returns no previous cursor, so the caller decides how to
  go back; the admin list keeps a stack of the cursors it has visited. Do not render
  `Pagination` for an empty result set.

## Known limits of the automated check

- **`axe-core` cannot evaluate `color-contrast` under jsdom** — the rule needs real layout
  and computed rendering, which jsdom does not provide. It is disabled explicitly in
  `frontend/src/test/a11y.ts`, with a comment there rather than silently skipped. Contrast
  is instead carried by the `--color-*` tokens declared in `frontend/src/styles/app.css`'s
  `@theme` block, and `frontend/tests/design-tokens-contrast.test.ts` checks the token
  pairs arithmetically. **Unverified against real rendering** until the real-browser axe
  run with colour contrast enabled exists (option B step two, see "Scope" above) — CI
  passing has never meant contrast was verified in a browser.
- **A group's hint and error are announced once per option.** Wiring them to each
  `<input>` is what makes them announced at all — a `group` role's description is
  inconsistently supported — but the consequence is that a user tabbing through a
  six-option group hears the full hint and error six times. Whether that grates in
  practice is **unverified**: the refinement this would call for (describe only the
  *first* option by the hint while keeping the error on all of them) needs a real screen
  reader in front of you to judge, not this reasoning alone. The manual pre-launch pass
  (option B step three) is where that judging happens, for the public and login screens.
- **jsdom does not implement native radio behaviour** — neither the roving tabindex nor
  arrow-key traversal. `RadioGroup` therefore implements both itself and calls
  `preventDefault()` on the keys it handles, so a real browser's identical native
  behaviour cannot fire alongside it. This is the same call `dialog.tsx` makes about
  `showModal()` and the `Tab` trap: a contract the runtime supplies invisibly is a
  contract CI cannot assert, so the component owns it explicitly instead.
- The automated check runs component-by-component, in isolation. It catches what is wrong
  with a component's own markup; it cannot catch a whole-screen defect (heading order
  across several components, a focus order that only breaks once components are combined).
  That class of defect is **unverified** until the real-browser run walks whole screens
  and the manual pre-launch pass covers the public and login screens.
