# ADR-0038: Batch code resolution on the existing property-values route

**Status:** Accepted
**Date:** 2026-09-09

## Context

[ADR-0031](0031-coded-property-values-addressed-by-property-key.md) gave
`GET /api/v1/registry/properties/{key}/values` one shape for both binding
targets, paged `offset`/`count`, capped at `DEFAULT_PAGE_SIZE` (50) per call.
PR #305 (issue #289) used that same route to resolve an active-filter chip's
label: fetch the unfiltered first page and look the selected code up in it
(`resolveValueLabel`, `frontend/src/pages/admin-catalogue-list.tsx`).

That works only while the selected code sits in the first page. Issue #306
is the case it does not cover: a `snomed_value_set`-bound property with a
large expansion, where a selected code is often not among the first 50
results, and the chip silently falls back to the raw code - the exact defect
issue #289 fixed, for a narrower but realistic case. `PropertyFacetGroup`'s
`carriedOptions` (`admin-catalogue-filter-panel.tsx`) has the identical
ceiling, so both surfaces need the same fix to stay consistent with each
other.

`filter` cannot serve as the repair. It is display-text narrowing - word-
prefix on the SNOMED side, `ILIKE` on the local side, `display` only on both
(ADR-0031's own "two branches' `filter` semantics are not identical" note) -
so passing a selected code as `filter` matches nothing on either side.
Resolving a set of already-selected codes is a different operation from
paging a picker's offerable values, and needed its own decision.

## Decision

### Extend the existing route with a batch `code` query parameter, not a new endpoint

`GET .../values?code=X&code=Y` resolves exactly those codes and returns the
unchanged `PropertyValuePage` shape (`{items: [{code, display}], total}`),
rather than adding a `values/{code}` sub-resource or a second route. One
request per property resolves any number of selected values, matching the
frontend's existing per-property `useQueries` batching, and the caller
(`resolveValueLabel`, `PropertyFacetGroup`) already depends on this route's
`PropertyValuePage` shape today - reusing it means no second response model,
and no second place for a future third binding target to be wired in.

`code` and the picker's own `filter`/`offset`/`count` are refused together
(422, `PropertyValueSelectionConflictError`): the two are different selection
modes on the same route, and mixing them has no coherent meaning (does
`filter` narrow before or after `code` resolves?). Legibility, not
performance, is the reason - one route serving two never-combined modes
stays easier to reason about than one route silently reinterpreting `code`
as a further, ambiguous narrowing of the picker page.

`code` accepts at most 200 values (`count`'s own existing ceiling), enforced
declaratively (`Query(max_length=200)`), not a bespoke error path.

### One `expand` call per batch, via `ecl_set_of`, not `$lookup` per code

The SNOMED side resolves through `client.expand(ecl_set_of(codes), ...)` -
one request for the whole batch, matching FR-52's "one call, not N" for
every other terminology-server code path in this codebase. A `$lookup`-per-
code design was considered while drafting this ADR; batching made the
ECL-set expansion (already used by FR-84's hierarchy check) the natural
choice instead, since `ecl_set_of` already exists, already validates each
code's format before concatenating it into the query (the injection guard
this path needs), and turns N codes into one request the same way `expand`
already does for the picker page.

The local-code-system side resolves through one `SELECT ... code IN (...)`
against `local_code`/`local_code_system` - a batched query, not
`find_local_code_with_system_status`'s own per-code shape
(`DatabaseLocalCodeLookup.resolve`'s read path, right for a single-code
lookup but not for up to 200 of them in one request). FR-52's "one call,
not N" is about the terminology server, not an in-process Postgres read,
but N round trips against Postgres for one HTTP request was still worth
collapsing to one (review round 1, PR #307).

### Not intersected with the property's own bound value set, and not active-only

The expansion sent to `expand` is `ecl_set_of(codes)` alone - never `ecl_set_of(codes)
AND (the property's own bound ECL)` - and `active_only=False`, unlike the
picker page's own `_value_set_page` (`active_only=True`). The local side
resolves through the same status-blind read `DatabaseLocalCodeLookup.resolve`
already uses, so a deprecated code (or one in a deprecated system) still
resolves here exactly as it already does through that lookup.

This is deliberate, not an oversight: a chip or carried filter value renders
a code an entry (or a saved filter) already recorded, at some point in the
past. Whether that code is still active, or still inside the RCPA's current
value set, is a fact about today's editorial state, not about whether a
value the catalogue already committed to still has a name. Intersecting with
the bound ECL, or filtering to active-only, would make a chip's label
depend on the code's current set membership - exactly the "since-removed
code loses its label" defect this issue exists to fix, reintroduced one
layer deeper.

A code neither side can resolve at all (never existed, or the terminology
server has genuinely never heard of it) is omitted from `items`, never
invented; the existing caller-side fallback to the raw code (issue #289's
own resolution path) covers that case unchanged. `total` is always the
number resolved (`len(items)`), never the number asked for, so a caller can
never see `items` and `total` disagree.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A `values/{code}` sub-resource, one code per request | Reintroduces the one-request-per-code shape FR-52 exists to forbid, for a caller (a chip row, a carried filter checkbox) that routinely needs several codes resolved at once. |
| `$lookup` per code on the SNOMED side | Exactly the N-calls-for-N-codes shape FR-52 forbids; `ecl_set_of` already gives one call for the whole batch. |
| Retry `filter=<code>` on a picker-page miss | `filter` is a display-text narrowing operator on both binding targets (ADR-0031); a raw code is not display text, so this does not reliably return the single matching item, or anything at all. |
| Intersect the resolve query with the property's own bound ECL | Reintroduces this issue's own defect one layer deeper: a code the RCPA has since narrowed out of the value set would stop resolving again, even though the catalogue already recorded it. |
| `active_only=True`, matching the picker page | A retired SNOMED concept, or a deprecated local code, must still render its label for a value already on record - the same trade `DatabaseLocalCodeLookup.resolve` already makes for the local side. |

## Consequences

- A chip or carried filter value resolves to its display label regardless of
  its position in (or absence from) the picker page's own `DEFAULT_PAGE_SIZE`
  window, closing the gap #289 left open.
- The picker page (`list_property_values`) and the resolve-by-code path
  (`resolve_property_values`) now coexist in `nptc.catalogue.property_value_sources`,
  both reading `binding_target` - ADR-0013 SS5's guard is satisfied by
  confining both to this one module, not by there being exactly one function
  that reads it.
- A future caller that wants "the offerable values, narrowed to what the
  user typed" still uses `filter`; a caller that wants "the display value of
  codes I already have" uses `code`. Combining the two remains refused by
  design, not a gap to close later.
- `concept-picker.tsx`'s own carried "currently recorded" option (shown
  inside an edit dialog that already fetches with a `filter`) is out of
  scope here; if it wants the same resolution, that is a follow-up, not an
  extension of this decision.
