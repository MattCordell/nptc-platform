# ADR-0034: Finding indicator and history — a minimal read-only `validation_finding` table now, and an anonymous redacted history projection

**Status:** Accepted
**Date:** 2026-09-07

## Context

Issue #141 (FR-18, FR-19) is two read-side surfaces the P1 milestone still owed: a public
signal that an entry's terminology binding has an open validation finding, and the entry's
change history - the old spreadsheet's free-text `History` column, made structured.

Two facts shaped the whole plan before any code was written:

- **`ValidationFinding` did not exist.** PRD §6.1 draws `CatalogueEntry --< ValidationFinding
  (open / acknowledged / resolved / superseded)`, specified further by FR-45 (finding types)
  and FR-55 (the lifecycle) - but that entity, and the sweep and acknowledge/resolve
  endpoints that populate and transition it, are P3 (`nptc.validation` was still a
  placeholder module). FR-18's indicator has nothing to read without it.
- **No history endpoint existed.** The only application read of `audit_event` was
  `nptc.catalogue.entries._latest_change_attribution`, used for FR-38's 409 conflict body.
  Field-level diffs (issue #37) are fully implemented, so the raw material for FR-19 - each
  write's changed fields and its FR-37 changelog note - was already persisted; nothing read
  it back.

## Decision

### `validation_finding` lands now, minimal and read-only, ahead of P3

Rather than stub FR-18's indicator against a resolver that always returns `false` until P3
lands, the table itself lands in P1: `entry_id` (`NOT NULL`), a nullable `binding_id`,
`finding_type` and `severity` (both CHECK-constrained to FR-45's own taxonomy), and `status`
(CHECK-constrained to FR-55's four states, defaulting to `open`). No `acknowledged_by_user_id`,
no `resolved_at`, no columns for a lifecycle nothing can drive yet - P3 widens this table
when its sweep and acknowledge/resolve endpoints exist, the same "new column, new migration,
never a widened old grant" discipline every other table in this schema already follows (see
`nptc.db.roles`'s own comments throughout).

**`nptc_app` is granted `SELECT` only** - the first table in this codebase where the
interactive app role has no write path at all. Every other table's grant is at minimum
`SELECT, INSERT` because the app role genuinely inserts those rows in production
(`catalogue_entry`, `designation`, even the narrow `designation_collision_acknowledgement`).
Nothing in P1 ever inserts a `validation_finding` row through `nptc_app`: FR-45's sweep and
FR-55's transitions are both P3, and which process ends up running the sweep - `nptc_app`
itself, or a separate service role - is P3's decision to make, not this issue's to
anticipate. Test fixtures needing a real `open` row seed it via the owner connection,
committed, matching `test_audit_tamper_detection.py`'s own "two connections in their own
uncommitted transactions cannot see each other's writes" precedent for the identical reason.

`has_open_finding: bool` sits on `EntrySummary` (`nptc.api.routers.catalogue_shared`), so
`SearchHit` and `EntryDetail` inherit it - one place a type, a severity or a count could ever
leak through, and there is none. Resolved by a single batch query per response
(`nptc.catalogue.queries.open_finding_business_keys`), keyed on `business_key` rather than
`entry_id`: `nptc.catalogue.search.SearchHit` deliberately carries no internal id at all (its
own module docstring), so an id-keyed lookup could not serve search results, while
`list_entries`/`get_entry` callers already have `business_key` sitting on the same
`CatalogueEntry` row they would otherwise read `id` from. One function, not two, covers every
caller.

### History is public, and redacted by construction rather than by filtering

Entry detail is already served to an anonymous caller under `Permission.CATALOGUE_BROWSE`
(FR-20); the alternative - gating `GET /catalogue/entries/{business_key}/history` behind
`Permission.REGISTRY_READ` ([ADR-0028](0028-registry-read-permission.md)) so only a signed-in
member and above could see it - was considered and rejected. `REGISTRY_READ` exists for a
specific, narrower reason: the property registry is submission-form plumbing, internal
maintenance shape that happens to describe *properties*, not published catalogue *content*.
An entry's change history is a fact about the entry itself, in the same category as its
designations, bindings and property values, all of which are already public sub-resources on
this same detail page - singling out history for a login requirement would be an arbitrary
line within one page's own content, not a defensible new permission boundary the way
`REGISTRY_READ` was.

Making history public means it must never carry what it is not safe to publish: the
`before`/`after` diff `audit_event` stores internally, an actor's internal id, or a system
identifier for the row that changed. Rather than build the full internal shape and filter it
at the API boundary (the pattern every other risk of this kind in this codebase deliberately
avoids - see `catalogue.py`'s own "no internal identifier" rule and the whole-body hygiene
scan that backs it), `nptc.catalogue.history.load_history`'s projection is redacted **by
construction**: `_changed_field_names` reads only the *keys* of `before`/`after` (and
`REDACTED_KEY`'s own listed names), never a value, so there is no code path anywhere in this
module that could serialise a withheld or sensitive value even by accident. A field's name
appearing is not the secret FR-18/FR-19 protect; a field's *value* is.

`changed_by` resolves to `app_user.display_name`, never the internal UUID (NFR-04/NFR-26,
matching `_latest_change_attribution`'s own precedent), and is `null` for a system-initiated
event or an account since closed and pseudonymised - the join always succeeds, only the name
is ever missing.

### History spans an entry's children, keyed by their own audit `entity_id`

A `designation`/`code_binding` audit event's `entity_id` is that child row's *own* primary
key (`nptc.audit.recording._default_entity_id`'s default), never the parent entry's - so
`load_history` first resolves every designation and binding id attached to the entry
(`nptc.catalogue.queries.load_designations_for_write`/`load_bindings`, both already
unfiltered by status: a retired child's history belongs in the entry's history too), then
queries `audit_event` for the union of `catalogue_entry`, `designation`, `code_binding` and
`property_value_set` rows naming one of those ids. `property_value_set`'s own composite key
(`f"{entry_id}:{property_key}"`, `nptc.catalogue.property_values`'s own precedent) is rebuilt
from every property key the entry currently carries a value for - a property once set and
later cleared to no values at all would not surface, a known limitation of this minimal read
rather than something this issue closes.

### `release` is a defined, always-`null` slot

FR-19's own wording is "every published release in which it appeared, what changed at each,
and the changelog note" - releases do not exist until P4. Building `HistoryEvent.release` as
a real field that is simply always `null` today, rather than omitting it and adding it later,
means a client written against this shape now needs no change once P4 populates it - only a
value to actually render where it previously rendered `null`. `docs/requirements/
requirements.yaml` records FR-19 as `in-progress`, not `implemented`, until that slot is
filled.

## Consequences

- `validation_finding`'s `SELECT`-only grant is a new precedent in this codebase - the next
  contributor adding a column expecting `nptc_app` to already have some write privilege on
  this table should check `nptc.db.roles.GRANT_VALIDATION_FINDING_SQL`'s own comment first.
- P3's sweep and acknowledge/resolve lifecycle inherits a table shape it did not design:
  `binding_id` nullable, no acknowledgement columns yet. Widening it is a new migration and a
  new, separate grant statement, never an edit to migration 0018's own.
- `nptc.catalogue.history` has no write path of its own and never will; every event it reads
  was written elsewhere. A future contributor adding a new write path to `nptc.catalogue.*`
  gets history coverage for free the moment that path calls `record_change` with the
  conventional default `entity_type`/`entity_id` - no change to this module is needed unless
  the new entity type needs its own composite key, matching `property_value_set`'s case.
- The property-value-set gap above (a property cleared to zero values disappearing from
  history) is accepted as a known limitation, not filed as a follow-up issue: no write path
  in this codebase currently clears a property to nothing at all without leaving at least one
  other trace, and inventing a fix for a case nothing yet produces would be speculative.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| A stubbed `has_open_finding` resolver, always `false`, until P3 lands `ValidationFinding` | Leaves FR-18's own acceptance criteria untestable as written - "an entry with an open finding shows the indicator" has no real row to prove it against. |
| `nptc_app` granted `SELECT, INSERT` on `validation_finding`, matching every other table | Nothing in P1 has a write path that would ever use the INSERT grant; granting it now is privilege the interactive role does not need and P3 may want to route through a different role entirely. |
| Gate `GET .../history` on `Permission.REGISTRY_READ` (ADR-0028) | `REGISTRY_READ` is a considered, narrow line around submission-form plumbing; history is a fact about published catalogue content, the same category as the entry's already-public designations/bindings/properties. |
| Build the full internal history shape (raw `before`/`after`) and filter sensitive fields at the API layer | Exactly the "build internal, filter at the boundary" pattern this codebase's own hygiene tests exist to catch failing at, once, for a field nobody thought to filter. Redaction-by-construction (project field names only) has no such value to ever leak. |
| Key the batch lookup on `entry_id` (an `open_finding_entry_ids` shape), matching every other batch loader in `queries.py` | `nptc.catalogue.search.SearchHit` deliberately carries no entry id at all, so an id-keyed lookup could not serve search results without adding an id to a type whose own docstring says it never will. |
| Invent `finding_type` slugs for FR-47's dual-edition diff findings (AU-only, forecast-inactive, absent-from-both) now | The PRD names these findings in prose, not with a type code the way FR-45's table does. Guessing a name risks P3's actual sweep engine needing a different one, forcing a second migration to fix it. |

## Amendments

- 2026-09-07: The "Alternatives rejected" row about keying the batch lookup on `entry_id`
  named the hypothetical function `open_finding_entry_ids`, worded ambiguously enough to
  read as though that had ever been the shipped name (PR #278 review). Reworded to make
  clear it names the rejected shape, not a renamed function - the shipped name has always
  been `open_finding_business_keys` (`nptc.catalogue.queries`).
