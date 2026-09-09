# ADR-0037: Bulk reclassify UI — read outcomes[] not the status code, two structurally distinct failure presentations, and report conflicts rather than retry them

**Status:** Accepted
**Date:** 2026-09-08

## Context

Issue #63 is the frontend half of FR-39's bulk reclassify: a toolbar and dialog on the
admin catalogue list (issue #267) letting an Administrator set one registry property to
one value across every selected entry in a single audited batch, calling the bulk write
route issue #265/#280 already shipped (ADR-0035, `POST
/catalogue/entries/bulk/properties/{key}`). That route made three decisions the screen
has to be built around rather than discover mid-implementation: it is always `200`, a
stale `expected_row_version` is a per-entry outcome rather than a thrown conflict, and
FR-89's specimen cross-field check aborts the *whole* batch as a `422` rather than
producing a per-entry outcome. Four more decisions were made while building the screen
itself, recorded here so a future change to this area does not relitigate them.

## Decision

### `useBulkSavePropertyValues` never throws a version conflict — there is no `onError` branch

Every other write hook in `frontend/src/api/queries.ts` (`useSavePropertyValues`,
`useAmendDesignation`, `usePatchEntryCore`) narrows a thrown `ApiError` via
`asVersionConflict` and refetches the cached entry on a 409. The bulk route never
produces that shape — a stale `expected_row_version` is a `conflict` outcome inside the
*success* body (ADR-0035) — so `useBulkSavePropertyValues` has nothing to catch. The
dialog and results panel read `result.outcomes[]`/`applied`/`unchanged`/`conflict`/
`not_found` directly from the resolved value, never from a caught error.

**Invalidates by key *prefix*, not the single `adminEntryDetailKey(businessKey)` every
other write hook targets.** A batch can touch up to 100 entries; `queryClient.
invalidateQueries` already matches every cached query whose key starts with the one
given, so three prefix invalidations (the admin list, the admin search, and the admin
entry-detail route) cover all of them without zipping `outcomes` into a hundred
per-entry invalidation calls.

### Two failure presentations, kept structurally apart — never merged into one

A `200` with some entries skipped and FR-89's whole-batch `422` abort are not variants of
the same failure; they are rendered by entirely different components, on purpose:

- **A `200` with skips** closes the dialog and renders `BulkOutcomeSummary` on the list
  page — a durable record, not tied to the dialog's own lifetime (see below).
- **FR-89's abort** is the one validation that is not a per-entry outcome (ADR-0035): it
  reaches the dialog as an ordinary `PropertyValidationResponse` 422, reusing
  `propertyValidationFieldErrors` (extracted from `properties-panel.tsx`'s own
  `PropertyEditDialog` in this same change) for its group-level (`ordinal: null`)
  message. The dialog stays open, the operator's values and note are untouched, and no
  results panel is shown — nothing applied, including any entry earlier in the selection
  that would otherwise have succeeded. Collapsing this into a `BulkOutcomeSummary` "abort"
  row was rejected: the server body does not name which entry tripped the check, so a
  results-panel row claiming to explain it would either lie by omission or require this
  screen to guess.

### `ConflictAttribution` extracted from `VersionConflictNotice`, not simply reused as-is

The results panel needs to render the identical `VersionConflictResponse` body the
single-entry dialog's 409 does, but the single-entry notice's closing line — "The entry
is reloading with their change" — is false in the bulk context: the dialog has already
closed, and nothing on the list screen is refetching a detail view nobody is looking at.
Rather than duplicate the who/when-plus-field-diff rendering a second time,
`collision-notice.tsx` now exports `formatValue` and a `ConflictAttribution` component
parameterised on the one clause that legitimately differs by context (`since`, e.g.
"while you had it open" vs. "since this batch selected it", plus the submitted/current
verb) — both notices compose from it, and neither promises a reload that will not happen.

**The equal-version defensive case gets its own branch, not `ConflictAttribution`.** An
entry deleted and recreated under the same `business_key` mid-batch can return
`current_row_version === expected_row_version`, with no attribution (`changed_by: null`)
and no field diff (`conflicts: []`). Rendering that through `ConflictAttribution` would
read as a version delta with nothing behind it — no who, no when, no field named — which
looks like a bug rather than what actually happened. `BulkOutcomeSummary`'s `SkipReason`
checks for this shape explicitly and renders "This entry was replaced while the change
was running, so it was skipped." instead.

### A conflicted entry is reported, never offered a one-click retry

The results panel names every skipped entry and why, but does not offer to resubmit a
conflicted one in place against the server's own `current_row_version`. This was
considered and rejected: it is last-write-wins dressed up as a convenience, and the
conflict body carries no field diff whenever the concurrent edit touched a different
field (`conflicts: []`) — an operator clicking "retry" in that case would overwrite a
change they were never shown any detail of, exactly what FR-38's optimistic lock exists
to prevent. An operator who wants to proceed re-opens that one entry through its own
single-entry edit screen (#61), which does show the full reconciliation flow.

### The captured selection is cleared after any submit, not refreshed from the outcome

Every `expected_row_version` the dialog sent becomes stale the instant anything in the
batch applies. Refreshing the selection's captured versions from `outcome.row_version`
so a second bulk submit could reuse them was considered and rejected: it would let a
second reclassify blind-overwrite whatever a concurrent editor did to one of those
entries in the interim, silently, since the operator never re-selected them with the new
version in view. The list clears `selected` unconditionally once `onComplete` fires;
`BulkOutcomeSummary` is the durable record of what to revisit, and re-selecting is an
explicit act.

### Bulk clearing is deliberately not offered — only bulk setting

The server places no floor on `values.length` on the bulk write route: an empty set is a
legitimate whole-set *clear*, the same as the singular `PUT
/catalogue/entries/{business_key}/properties/{key}` route already allows for one entry.
The dialog refuses this rather than passing it through: `noValuesEntered` blocks submit
with "Add at least one value before reclassifying." whenever a property is chosen but
every auto-rendered value slot is left blank, composed into the same `Form.
submitBlocked` gate chain as the 100-entry cap and the changelog note.

This is a genuine capability restriction, not input validation for its own sake — FR-39's
own framing is "set one property to one value" across a selection, and a bulk write with
a much larger blast radius than a single-entry edit is exactly the wrong place to also
offer "clear this across everyone selected" for free. The singular route keeps that
capability per entry; an operator who wants to clear a property across a selection does
so one entry at a time through its own editing screen (#61), where the smaller blast
radius matches the smaller, more deliberate action.

### The 100-entry cap is enforced inside the dialog, not the toolbar

`BulkReclassifyToolbar`'s launch button is never disabled by the cap. `_MAX_BULK_ENTRIES`
(ADR-0035) is enforced by `BulkReclassifyDialog`'s own `Form.submitBlocked`, composed
ahead of the changelog-note gate and left unlinked (no field to send focus to) — the
operator can see exactly how many are selected and by how much they are over, from
inside the dialog they already opened, rather than being blocked at the toolbar with no
way to see why short of backing out to the list and counting rows by hand.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Treating a per-entry `conflict` outcome as a thrown error, matching every other write hook | The route never throws one (ADR-0035); forcing it through the same `onError` shape as `useSavePropertyValues` would require re-wrapping a success response as a synthetic failure for no benefit. |
| One component rendering both a partial-200 and the FR-89 abort | The two need different affordances (a persistent results panel vs. an in-dialog blocking error) and the server names no offending entry for the abort case - conflating them risks the abort looking like a per-entry outcome it is not. |
| Reusing `VersionConflictNotice` unchanged for the results panel | Its closing "the entry is reloading" line is false once the dialog has closed - the reason `ConflictAttribution` was extracted instead of exported as-is. |
| A one-click "retry" on a conflicted outcome row | Last-write-wins with no field diff to justify it in the common case (`conflicts: []`) - exactly what FR-38 exists to prevent. An operator who wants to proceed uses the single-entry edit screen's own reconciliation flow. |
| Refreshing the selection's captured `expected_row_version`s from the outcome list for a follow-up submit | Lets a second bulk write blind-overwrite a concurrent edit to one of those entries with no re-selection in between. |
| Disabling the toolbar's launch button past the 100-entry cap | Tells the operator nothing about *why*, and they may want to deselect down to the cap from inside the dialog rather than back out to the list first. |
| Passing an empty `values[]` through to the bulk route, matching what the singular per-entry route already allows | A much larger blast radius than a single-entry edit is the wrong place to also offer bulk clearing for free - #295 recorded this decision here after PR #290 review added the gate without documenting it. |
