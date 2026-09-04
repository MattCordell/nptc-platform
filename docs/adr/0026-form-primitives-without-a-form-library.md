# ADR-0026: Form primitives without a form library

**Status:** Accepted
**Date:** 2026-08-27

## Context

ADR-0025 settled how the frontend is styled; issue #148 landed the accessible component
baseline — `Field`, `Button`, `Dialog`, `DataTable`, `LiveRegion` — that every screen is
expected to compose. It deliberately left out the choice controls and the form-level
machinery, which issue #210 now adds: `Select`, `Checkbox`, `CheckboxGroup`,
`RadioGroup`, `ErrorSummary` and `Form`.

Those last two are where a decision has to be made. Every edit screen queued behind this
(#149 designations, #150 code bindings, #151 the registry property form, #183 the
acceptance controls) needs the same four things: field state, validation, one submit
path, and a way to tell a screen-reader user what failed and where. The React ecosystem's
default answer is a form library — `react-hook-form`, usually paired with a schema
library such as `zod` — and adopting one shapes every form screen written afterwards.
Changing approach later means rewriting each of those screens, not adding an ADR.

The requirement in play is NFR-31 (WCAG 2.2 Level AA), whose form-specific obligations —
a programmatically associated label on every control, a group label that is a real
`<legend>`, focus moved to a summary on a failed submit and onward to the named field —
are what these components exist to make structural rather than remembered.

## Decision

**No form library.** The form primitives are props-in/props-out React components: the
caller owns field state and validation, and passes results down.

- `Form` takes `onSubmit`, an optional `errors: FormError[]`, an optional `formError`,
  and `pending`. It owns the `<form>`, renders its own submit button, guards against a
  double submit, and moves focus to the summary when a submit is answered with errors.
- `ErrorSummary` takes the same `errors` and links each to the `fieldId` it names.
- `Field`, `Select`, `Checkbox`, `CheckboxGroup` and `RadioGroup` take an optional `id`
  so a caller can address a control by an id it chose — which is what makes a summary
  link able to move focus to the field it names.

Two supporting conventions fall out of that and are recorded here because they are not
obvious from the code:

- **A group's caller-supplied `id` lands on its first option's `<input>`, not on the
  `<fieldset>`.** A summary link has to land somewhere focusable that announces something
  useful; focusing a fieldset announces nothing, whereas focusing the first radio or
  checkbox announces the legend as the group it belongs to, then the option itself.
- **`RadioGroup` implements its own roving tabindex and arrow-key traversal** rather than
  relying on the browser's native radio behaviour, calling `preventDefault()` so the two
  cannot both fire. This mirrors what `Dialog` already does for `showModal()` and the
  `Tab` trap, and for the same reason: jsdom implements neither, so "one `Tab` stop,
  arrows traverse" would be an untestable claim in CI otherwise.

## Rejected alternatives

**`react-hook-form` (+ `zod`).** Genuinely good at the problem it solves — uncontrolled
field registration, re-render minimisation, resolver-driven validation — but that problem
is not the one these screens have. The forms in question are small (a designation, a code
binding, a property definition), and the expensive part of each is the accessibility
wiring, which a form library does not supply: label association, `aria-describedby`
ordering, group labelling, and summary focus management would still have to be written
here, on top of a dependency. It is also the repository's established instinct to decline
a dependency for a small problem — ADR-0020 rejected a schema library for search-param
parsing and `frontend/src/router/search-params.ts` is hand-rolled for exactly this
reason. Revisit if a screen appears with genuinely dynamic field arrays or cross-field
validation, where the hand-rolled state starts to cost more than the dependency would.

**Addendum (issue #151):** the registry properties panel is exactly the "genuinely dynamic
field arrays" case this paragraph names as the revisit trigger - a `0..*`/`1..*` property
renders a caller-controlled number of value slots, added and removed at runtime. The
decision, on arrival, was to stay with plain `useState` (`property-controls/
repeatable-values.tsx`'s `RepeatableValues`): the array is small (the PRD's own worst case
is seven specimen values), holds no cross-field validation of its own - ADR-0030 keeps that
server-side - and the component that owns it is a single, narrow wrapper rather than
something repeated per screen. The trade-off this ADR anticipated (hand-rolled state costing
more than a dependency) has not materialised for this shape; it may still for a case with
real cross-field client validation, which this is not.

**A form context that wires errors to fields automatically.** `Form` would provide a
context, each field would register its generated id under a `name`, and a summary link
could never point at a dead id. Rejected in favour of explicit props after putting the
trade-off to the maintainer: the implicit version is harder to follow at the call site,
couples `Field` to `Form`, and buys safety against a class of mistake (a mistyped
`fieldId`) that a screen's own test catches immediately.

**A custom `role="listbox"` for `Select`.** Rejected outright — issue #210 rules one out
unless a native `<select>` genuinely cannot express the need, and it can. The native
control carries keyboard operation, type-ahead and the platform's mobile picker, none of
which a hand-built listbox gets for free.

**Per-field server-side errors.** Out of reach rather than rejected: the API contract
exposes only `ErrorResponse { detail: string }` (`docs/api/openapi.json`) with no
per-field `loc`, deliberately, because a refusal must not name a role, a permission or an
internal identifier (FR-44, NFR-04). A server refusal therefore has no field to attach
to, and `Form`'s `formError` slot is where it lands. Attributing a 422 to a control needs
an OpenAPI change first.

## Consequences

- Each edit screen writes its own validation. That is more code per screen than a
  resolver would be, and the mitigation is that the accessibility-critical half — which
  is the half that is easy to get subtly wrong — is not repeated.
- A caller must supply matching `id`s to a field and to the summary entry that names it.
  A mismatch means a summary link that focuses nothing; every screen's test should
  exercise the failed-submit path, which surfaces it immediately.
- `Form` renders its own submit button, so a screen cannot place a `type="submit"`
  control inside it by another route. That is what makes "one submit path" and "refused
  while pending" guarantees rather than conventions, at the cost of an opinionated
  actions row; `secondaryActions` covers Cancel and friends.
- `Form` keeps listening for a submit's answer until an error arrives, rather than
  treating the first render where `pending` is false as the answer. A caller that never
  sets `pending`, or sets it a tick later, therefore still gets its refusal announced —
  the majority case. An error that follows no submit at all still never moves focus.

  The cost runs the other way, and is sharper than "a stale flag": after a *successful*
  submit the form stays armed indefinitely. For a validate-on-submit screen that is
  benign. For a screen that also validates on change it is not — the next keystroke that
  produces an error would pull focus out of the input the user is typing in, once per
  keystroke. **These primitives therefore assume validate-on-submit.** Issue #214 tracks
  the clean fix: widen `onSubmit` to `() => void | Promise<void>` and disarm when the
  returned promise settles, so arming stays unconditional for the sync case and the bug
  that motivated the always-armed flag does not come back.
- `RadioGroup`'s hand-written key handling is a divergence risk if the ARIA authoring
  practices for radios change. It is covered by tests that state the expected behaviour
  in full, so a future change is a visible diff rather than a silent drift.

## Amendment (2026-09-04): `submitBlocked` for a caller-side client gate

Issue #62 needed a second thing from `Form`, beyond validate-on-submit: a changelog note
that fails FR-37 (mirrored client-side per ADR-0030) must refuse submission *before* a
request goes out, not only report a failure after one comes back. The nine edit forms this
issue touches (`designations-panel.tsx`, `bindings-panel.tsx`, `properties-panel.tsx`) all
need it, which is exactly this ADR's "the caller owns validation, `Form` owns the submit
path" split, extended to a validity a caller can compute *before* any submit is attempted.

**Decision: `Form` gained `submitBlocked?: boolean`, `blockedReason?: string` and
`blockedFieldId?: string`, alongside `pending`.** `submitBlocked` joins `pending` on the
submit button's `aria-disabled` and in the re-entry guard, so a caller cannot forget to
wire the refusal itself — the same argument that made `Form` render its own submit button
in the first place. Critically, the guard refuses the submit *before* calling `onSubmit`,
so a tenth form composing `ChangelogNoteField` gets the gate for free rather than having to
remember to check `blocked` itself.

`blockedReason` is announced through the same summary-focus path a validation failure
already uses, but only once an attempted submit is actually refused — never merely because
a required field starts empty, which would accuse the user of an error before they had done
anything. That gating is state private to `Form` (`blockedAttempted`), not something a
caller can observe or needs to.

`blockedFieldId` was added after the first attempt shipped without it: `blockedReason` alone
rendered as a plain, unlinked sentence (the same slot `formError` uses), which meant the
gate's refusal did not get the "click the summary entry, land on the named field" affordance
every other field-level error gets. Passing the id the caller already gave `ChangelogNoteField`
turns it into a real summary link, consistent with the rest of `ErrorSummary`'s contract.

**Why this belongs on `Form` and not as a fourth thing each caller re-implements:** the
alternative was each of the nine forms computing its own "is the note valid" check and
manually short-circuiting `onSubmit`, which is exactly the kind of easy-to-get-subtly-wrong
accessibility wiring this ADR already declined to leave to convention. `useChangelogNote`
(`frontend/src/catalogue/changelog-note-field.tsx`) computes the validity; `Form` is the one
place that enforces it cannot be bypassed by a click on an `aria-disabled` button.

### Addendum (2026-09-04): `onSubmitBlocked`, after review found the gate hid other fields

The first cut of the gate had a real gap, found in review before this PR left draft: four
of the nine forms (`AddSynonymsForm`, `AmendDialog`, `BindCodeForm`, `ReplaceBindingDialog`)
compute their *own* extra field validation - "enter a term", "this code must resolve" -
inside `onSubmit`, and `onSubmit` never runs while blocked. So a first click on a form with
both an empty other field and an empty note reported only the note's failure; the other
field's error surfaced only on a second click, after the note was fixed. The same gap meant
`useChangelogNote`'s own `guidance` (gated on the field having been blurred) never showed
on a submit attempt against a note field the user had not yet touched, even though the
summary linked to it - unlike every other field-level error in this codebase, whose inline
message and summary link appear together.

**Decision: `Form` gained `onSubmitBlocked?: () => void`, called in place of `onSubmit`
when a submit is attempted while blocked.** A caller with its own extra field validation
moves that computation out of `onSubmit` into a plain function the component body can call
from *both* `onSubmit` and `onSubmitBlocked`, so a blocked click recomputes and displays it
exactly like a non-blocked one does. Every one of the nine forms passes
`onSubmitBlocked={changelogNote.markSubmitAttempted}` (the four with extra fields also
recompute and `setErrors` their own), which is what makes the note's own `guidance` visible
on a blocked attempt regardless of blur.

This keeps `onSubmit` never running while blocked - the property the first cut promised and
this ADR's existing tests assert - rather than the alternative the review offered (always
call `onSubmit` and let `Form` merge the blocked reason into the announced errors), which
would have required every caller to add its own "don't actually mutate while blocked" guard
in exchange for removing one already-tested guarantee.
