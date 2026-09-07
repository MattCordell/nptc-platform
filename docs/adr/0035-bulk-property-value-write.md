# ADR-0035: Bulk property-value write — always 200 with per-entry outcomes, whole-set replace, a diff-free batch audit event, and FR-89's specimen check kept whole-request

**Status:** Accepted
**Date:** 2026-09-08

## Context

Issue #265 (FR-39) is the server half of #63's bulk reclassify: one route setting a
single registry property to a single value set across many entries, with per-entry
optimistic locking, so one stale entry never blocks a 99-entry reclassify. `nptc.
catalogue.property_values.save_property_values` (issue #52) takes one `CatalogueEntry`;
`nptc.catalogue.entries.save_entries` batches `EntryChanges`' own core columns, not
`property_value` rows, so it cannot reach discipline (a coded registry property,
`origin=system`, cardinality `0..*` — PRD §6.5, FR-90) at all. Both an incorrect
docstring on `save_entries` and a matching note on FR-38 in `docs/requirements/
requirements.yaml` claimed it was the seam; both are corrected as part of this issue.

Four shapes needed deciding before the seam or the route could be written: what a
partial-success batch returns as its HTTP status, what "replace" means for an entry
that already holds a compound value, how the batch's own audit event is built without a
diff to show, and how FR-89's specimen cross-field check — the one validation that
depends on an individual entry's state — fits a rule designed to split checks into
"whole-request" and "per-entry".

## Decision

### Always 200, with a per-entry outcome list — never a whole-request 409

`save_property_values_for_entries` returns one `BulkPropertyOutcome` per target, in
request order: `applied`, `unchanged`, `conflict` (carrying the domain `ConflictReport`),
or `not-found`. The route wraps this as `BulkSavePropertyValuesResult` and always
answers `200`, even when every entry conflicted.

A whole-request `409` was considered and rejected: the request was authorised,
well-formed, and fully processed, and a `409` would have to either omit the entries that
*did* apply, or attach their new `row_version`s to a status code whose entire meaning is
refusal — the one thing a retrying client needs, thrown away by the response shape. `207
Multi-Status` (WebDAV) was also considered; there is no other multi-status precedent
anywhere in this API, and it buys nothing over a `200` whose body already carries
per-entry status strings and counts. The route declares no `409` in `responses=` at all:
a documented body a route can never actually emit is a branch no generated client can
exercise.

### Whole-set replace across every targeted entry — not remove-one/add-one

Each entry ends up holding exactly the `values` sent, identical to the singular route's
own semantics (`save_property_values` deletes and re-inserts the whole set for
`(entry, property_key)`). A compound value an entry already holds (`"Chemical pathology
or Haematology"`) is therefore *replaced*, not amended.

Remove-one/add-one (diffing against each entry's own existing set and applying only the
delta) was considered and rejected: it would need a second code path alongside the
existing DELETE-then-INSERT `save_property_values` already uses, doubling the ways
cardinality's upper bound (ADR-0012) could be reached, and it has no requester today —
FR-39 names "reclassifying discipline across a filtered set" as the motivating case,
which is a replace, not a merge. If the editorial workflow later proves whole-set
replace insufficient for some other property, that is a new issue with its own
requester and its own test, not a variant folded into this one.

### A diff-free `property_value.bulk_set` header, appended directly

The batch's own audit event has no diff by construction — it summarises N other events,
it does not itself change one row. `nptc.audit.recording` exists to compute diffs and
refuse an empty one (`AuditNoOpError`, ADR-0018); wrapping a header with no diff through
that module would mean either faking a payload to satisfy it or adding a bypass
parameter to a module whose entire purpose is refusing exactly that. The seam instead
calls `nptc.audit.writer.append_audit_event` directly, with `before`/`after` both
omitted (not passed as literal `None`) — satisfied by, not exempted from, `test_audit_
write_path_guard.py`'s rule against a `before=`/`after=` keyword outside `nptc/audit/`.
This is the first call to `append_audit_event` from outside that package; a repo-wide
grep before this change found none.

`entity_type="property_value_bulk"`, distinct from `property_value_set` (the per-entry
event's own type): a history query scoped to `property_value_set` must not pick up a
diff-free header it cannot render as a value diff. `entity_id=property_key` — the batch
names the property it touched, not any one entry; per-entry attribution is what the
per-entry `property_value.set` events already carry, linked by sharing one
`correlation_id` (minted once per request, NFR-08) for free.

A dedicated recording helper in `nptc.audit.recording` for this one call site was
considered and rejected: its whole body would be a formatted `reason` string and a
straight pass-through to `append_audit_event`, a public entry point with nothing of its
own to enforce.

### FR-89's specimen cross-field check stays whole-request, by exception

Every other check in `save_property_values_for_entries` follows one rule: anything
derivable from `property_key`/`values`/`reason` alone is whole-request, checked once
before the loop; anything depending on one entry's own state at write time is a
per-entry outcome. FR-89's specimen cross-field check does not fit either side cleanly —
it depends on one entry's `specimen_unconstrained` flag, so it cannot be evaluated
before the loop reaches that entry, but it is deliberately *not* turned into a per-entry
outcome either.

**Decision: a specimen conflict aborts the whole batch** (a `422`, no partial write —
`PropertyValidationError` propagates uncaught out of the per-entry loop, past the
savepoint's own `except (StaleDataError, ObjectDeletedError)`, so `session_scope` rolls
back everything including any entry already applied earlier in the same request). The
seam does not catch it and does not replicate `_validate_specimen_cross_field`'s logic —
it runs naturally as part of the ordinary `save_property_values` call each target
already goes through, inside the same module that owns FR-89's knowledge.

The alternative — a fifth outcome status, `specimen-conflict`, alongside `applied`/
`unchanged`/`conflict`/`not-found` — was considered and rejected: the operator
explicitly selected that entry for this reclassify. Reporting the batch as a qualified
success while quietly skipping the one entry that would have violated FR-89 is a worse
failure mode than refusing the whole batch outright: a caller who does not carefully
audit every outcome string would believe the reclassify was complete when it was not.
This is also why the specimen check is the one place order-independence is *not* fully
achievable — a batch could apply several entries' writes (within their own savepoints,
inside the still-open outer transaction) before reaching the entry that triggers the
abort — but that is exactly the "genuine failure discards the whole batch" case
`session_scope` already exists to handle, not a new mechanism.

## Consequences

- A generated client (or a future frontend) must read `outcomes[]`/`applied`/
  `unchanged`/`conflict`/`not_found` to know what happened — a bare status-code check
  is not enough, unlike every other write route in this API. This is the direct
  consequence of "always 200": documented in `docs/architecture/catalogue-write-api.md`.
- The next bulk write route this API adds (if any) inherits this ADR's shape by
  precedent: always 200, per-entry outcomes, no whole-request 409 for a per-entry
  concern. A route that genuinely cannot tolerate partial success should say so
  explicitly rather than silently follow this one.
- `save_property_values_for_entries` re-derives the property definition and re-runs
  schema/cardinality/binding-strength validation once per entry (inside each call to
  `save_property_values`), on top of the identical whole-request preflight run once
  before the loop. This is deliberate redundancy, not a bug: `save_property_values`
  remains a complete, independently correct function callable on its own (the singular
  route still calls it directly), and the seam does not special-case it into two
  different code paths depending on caller. The cost is one extra `property_definition`
  SELECT per entry, bounded by the same 100-entry cap FR-39's lock-contention rationale
  already accepts.
- FR-89's specimen check remains the one path where a bulk write's failure mode differs
  from FR-38's row-version conflict: a stale entry never blocks its peers, but a
  specimen conflict blocks everyone after it in request order (and, if entries earlier
  in the list already applied within the same request, only `session_scope`'s rollback
  — not the seam itself — undoes them). A future issue wanting partial-success
  semantics for FR-89 specifically would need to revisit this decision, not just add a
  new outcome status.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| A whole-request `409` when any entry conflicts | Forces a choice between omitting the entries that did apply or attaching their new `row_version`s to a refusal status — the client needs exactly that information to retry, and a `409`'s whole meaning is "nothing happened". |
| `207 Multi-Status` | No other precedent in this API; the `200` body's own outcome list and counts already carry everything a `207` would, with no new client-side handling to learn. |
| Remove-one/add-one merge semantics for a bulk write | A second write code path alongside the existing DELETE-then-INSERT, doubling how cardinality's bound could be reached; no requester today — FR-39 names a replace, not a merge. |
| A dedicated `nptc.audit.recording` helper for the batch header | Its whole body would be a formatted string and a pass-through to `append_audit_event`, a public entry point with nothing of its own to enforce. |
| A `specimen-conflict` per-entry outcome status | Reports partial success for a batch that silently skipped an entry the operator explicitly selected — a worse failure mode than refusing the batch outright. |
| Pre-loading every targeted entry up front to check FR-89 before any write starts | Would still need a per-entry `not-found` outcome for a missing `business_key`, so `load_entry_for_update`'s `EntryNotFoundError` has to be caught inside the loop regardless; pre-loading buys no order-independence FR-89's own abort-on-conflict semantics need, since the check still cannot run before its own entry is reached. |
