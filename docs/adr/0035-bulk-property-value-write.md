# ADR-0035: Bulk property-value write — always 200 with per-entry outcomes, whole-set replace, a diff-free batch audit event, and FR-89's specimen check kept whole-request

**Status:** Accepted
**Date:** 2026-09-08
**Amended:** 2026-09-08 (round-2 review: lock ordering, batch header shape, error handling)

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

### A diff-free `property_value.bulk_set` header, carrying its tallies structurally, emitted only when something applied

The batch's own audit event has no diff by construction — it summarises N other events,
it does not itself change one row. `nptc.audit.recording` exists to compute diffs and
refuse an empty one (`AuditNoOpError`, ADR-0018); a batch header is not a diff, so it
does not go through `record_change`/`record_snapshot_change`, but it still needs a place
to carry the outcome tallies (`applied`/`unchanged`/`conflict`/`not-found`) that a
reader — a human auditor, or a future dashboard — needs to make sense of the header on
its own, without cross-referencing the per-entry `property_value.set` events sharing its
`correlation_id`.

**Round-1** (initial accepted decision, since revised): the seam called
`nptc.audit.writer.append_audit_event` directly, with `before`/`after` both omitted and
the tallies appended as a formatted parenthetical onto `reason` — e.g. `"Reclassify
discipline (40 applied, 2 unchanged, ...)"`. Round-2 review flagged two problems with
this: the operator's own changelog note (FR-37's whole point) no longer matched what they
typed, and it now differed from the identical `reason` on the per-entry events in the
same correlation group; and the header was still emitted even when every target
conflicted or was not-found — a batch that changed nothing, recording a permanent,
hash-chained row anyway, which a client retrying a stale selection would repeat once per
attempt.

**Round-2 (current):** `nptc.audit.recording.record_batch_summary` — a new, narrow
wrapper living in `nptc.audit`, not the domain module — passes the tallies as a
structured `after={"applied": n, "unchanged": n, "conflict": n, "not-found": n}` payload,
leaving `reason` as the operator's own note, byte-for-byte identical to the per-entry
events'. It lives in `nptc.audit` (not `nptc.catalogue.property_values`, the caller)
purely so its `after=` keyword satisfies `test_audit_write_path_guard.py`'s rule against
a hand-built `before=`/`after=` payload outside that package — the guard cannot tell "a
diff bypassing `record_change`" from "a structured summary that was never a diff" apart
by keyword name alone, so the distinguishing signal is which file the call lives in, the
same signal the guard already uses everywhere else. This revises the round-1 rejection of
"a dedicated recording helper" below: round-1's version of that helper really would have
been a pure pass-through with nothing to enforce, but round-2's version exists
specifically to relocate the call across the guard's own boundary, which is exactly the
kind of thing worth a one-function file.

The seam now also only calls `record_batch_summary` when `tallies["applied"] > 0` —
`tally_bulk_outcomes` (the same counting function the HTTP response uses, see below)
runs once after the per-entry loop, and the header is skipped entirely for a batch that
changed nothing, matching ADR-0018's "a no-op write emits no audit event" posture at the
batch level, not just the per-entry level `AuditNoOpError` already enforces.

`entity_type="property_value_bulk"`, distinct from `property_value_set` (the per-entry
event's own type): a history query scoped to `property_value_set` must not pick up a
diff-free header it cannot render as a value diff. `entity_id=property_key` — the batch
names the property it touched, not any one entry; per-entry attribution is what the
per-entry `property_value.set` events already carry, linked by sharing one
`correlation_id` (minted once per request, NFR-08) for free.

`nptc.catalogue.property_values.tally_bulk_outcomes` counts `outcomes` by `status` once;
both the audit header's `after` payload and the HTTP response's `applied`/`unchanged`/
`conflict`/`not_found` counts call it against the same `outcomes` tuple, rather than each
independently summing the list — round-2 review's finding that two independent counts of
the same batch could in principle disagree.

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

### Lock ordering: a deterministic early acquisition, not a complete fix (round-2 addendum)

This route is the first HTTP surface that can hold a row-exclusive lock on more than one
`catalogue_entry` row inside a single transaction. `nptc.audit.writer.
append_audit_event` already serialises every audit append across the whole application
via `pg_advisory_xact_lock` (issue #36) — a lock every write path takes *after* its own
row lock, which was safe as long as no transaction ever held more than one row lock at a
time. A bulk write breaks that assumption: round-2 review identified a genuine deadlock
cycle — a bulk request already holding the advisory lock (acquired at an earlier entry's
own audit append) can block waiting for a row a concurrent single-entry writer holds,
while that writer blocks waiting for the same advisory lock the bulk request will not
release until it commits.

**Decision: acquire the advisory lock once, deterministically, before the per-entry loop
starts** (`nptc.audit.writer.acquire_append_lock`, called from `save_property_values_
for_entries` directly), rather than relying on whichever entry's own audit append happens
to acquire it first. This makes the bulk transaction's lock-holding window well-defined
(the whole transaction, not "from wherever the first non-`unchanged` entry lands
onward") and closes the **bulk-vs-bulk** case completely: two concurrent bulk requests,
even targeting overlapping entries in different orders, now both queue on the same
advisory lock before either takes a row lock, so neither can be holding it while wanting
a row the other holds.

**This does not close the bulk-vs-a-concurrent-singular-write case.** A complete fix
requires every writer that can take both a `catalogue_entry` row lock and the audit
append lock — which, today, is every mutating catalogue write, not just this route — to
acquire them in the same order. Reordering only the bulk seam's own acquisition point
cannot enforce a *global* invariant across writers that do not coordinate with each
other; the singular write paths (`nptc.catalogue.entries.save_entry`, the singular
`PUT .../properties/{key}` route) still take their one row lock before the advisory lock,
and a sufficiently unlucky interleaving between one of them and a bulk request can still
deadlock. **Accepted, not fixed, here**: Postgres's own deadlock detector aborts one of
the two transactions when this happens (a `500`, full rollback of whichever transaction
loses, no corruption or partial write survives) — a real availability cost under
contention, but not a correctness one, and the 100-entry cap already bounds how much work
a lost transaction discards. A full fix — the same "advisory lock before any row lock"
ordering applied to every catalogue-entry writer — is a larger, higher-blast-radius
change than this issue's own scope and is filed as a follow-up (issue #281).

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
- A bulk write racing a concurrent single-entry write to an overlapping set of entries
  can deadlock (see the "Lock ordering" decision above); Postgres aborts one side rather
  than corrupting data, but that side's caller sees a `500` and must retry. Issue #281
  tracks the complete fix (a single lock-ordering rule applied to every catalogue-entry
  writer).
- **Round-3 review addition:** acquiring the audit append lock before the loop widens,
  not narrows, how long it is held. The lock is application-global (every audited write
  anywhere in the platform serialises on it), and a bulk request now holds it from before
  its first entry load until commit — every entry's load, validation, and flush for the
  whole batch, not just the moment of an audit append. Round-1's lazy, per-entry
  acquisition held it only from the first applied entry onward; round-2's fix trades a
  deadlock risk for a longer global-serialisation window, bounded by the same 100-entry
  cap. This is the right trade (availability cost, not a correctness one), but issue
  #281's "same ordering for every catalogue-entry writer" inherits the identical widened
  window for every write path it touches, not just this one, and should be sized with
  that in mind.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| A whole-request `409` when any entry conflicts | Forces a choice between omitting the entries that did apply or attaching their new `row_version`s to a refusal status — the client needs exactly that information to retry, and a `409`'s whole meaning is "nothing happened". |
| `207 Multi-Status` | No other precedent in this API; the `200` body's own outcome list and counts already carry everything a `207` would, with no new client-side handling to learn. |
| Remove-one/add-one merge semantics for a bulk write | A second write code path alongside the existing DELETE-then-INSERT, doubling how cardinality's bound could be reached; no requester today — FR-39 names a replace, not a merge. |
| Appending the outcome tallies to `reason` as a formatted parenthetical (round-1) | The operator's own changelog note stopped matching what they typed, and diverged from the identical `reason` on the per-entry events in the same correlation group — superseded by `record_batch_summary`'s structured `after=` (round-2). |
| Always emitting the batch header, even when nothing applied (round-1) | A client retrying a stale selection would append one permanent, hash-chained audit row per attempt for a batch that changed nothing — superseded by the `tallies["applied"] > 0` gate (round-2). |
| A `specimen-conflict` per-entry outcome status | Reports partial success for a batch that silently skipped an entry the operator explicitly selected — a worse failure mode than refusing the batch outright. |
| Pre-loading every targeted entry up front to check FR-89 before any write starts | Would still need a per-entry `not-found` outcome for a missing `business_key`, so `load_entry_for_update`'s `EntryNotFoundError` has to be caught inside the loop regardless; pre-loading buys no order-independence FR-89's own abort-on-conflict semantics need, since the check still cannot run before its own entry is reached. |
| Sorting `targets` into a fixed order (e.g. by `business_key`) before the per-entry loop | Would only close the bulk-vs-bulk lock-ordering cycle (two concurrent batches locking rows in different orders) — and `acquire_append_lock` before the loop already closes that case, without needing to reorder processing at all (a sort could still preserve request-order reporting by sorting a working copy and emitting outcomes in the caller's original order, so that was never the blocking cost). Rejected because it buys nothing beyond what the lock-ordering fix above already provides, not because it conflicts with the outcome-ordering contract. |
