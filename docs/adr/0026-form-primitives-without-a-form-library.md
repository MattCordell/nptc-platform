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
  control inside it by another route. That is what makes "one submit path" and "disabled
  while pending" guarantees rather than conventions, at the cost of an opinionated
  actions row; `secondaryActions` covers Cancel and friends.
- `RadioGroup`'s hand-written key handling is a divergence risk if the ARIA authoring
  practices for radios change. It is covered by tests that state the expected behaviour
  in full, so a future change is a visible diff rather than a silent drift.
