# ADR-0040: Property-value audit events record the whole value set, accepted as this write path's own field-level unit

**Status:** Accepted
**Date:** 2026-09-10

## Context

`nptc.catalogue.property_values.save_property_values` audits a property write via
`record_snapshot_change`, with `before`/`after` shaped as `{"values": [...]}` - the
entire value list for that `(entry, property_key)` pair, not a diff of only the value(s)
that changed. PRD NFR-08 (`docs/prd/NPTC-Catalogue-Platform-PRD.md:951`) says an audit
event's `before`/`after` content must be "field-level, not whole-record blobs".

Issue #264 was raised while closing out #61 (entry-edit screens audit-payload
coverage), which found the two tests below asserting this whole-set shape and flagged
it as an open question rather than deciding it unilaterally: is the property's whole
value list itself the field-level unit for this write path, or does NFR-08 require a
per-value diff?

`save_property_values`'s own write semantics are whole-set replace: it deletes every
`property_value` row for `(entry_id, property_key)` and reinserts the submitted values
fresh at `ordinal` 0..n-1 (ADR-0035 applies the same replace semantics to the bulk
seam). `ordinal` is part of `property_value`'s primary key
(`backend/src/nptc/db/models/property_value.py:85`) but is purely positional - it
closes the trivial race of two inserts landing on the same slot, and carries no
identity of its own across writes.

## Decision

**The whole-set snapshot is accepted as this write path's field-level unit.** No code
change. `_PROPERTY_VALUES_AUDIT_POLICY`'s single `auditable={"values"}` field already
matches NFR-08's "field-level, not whole-record blobs" requirement for this entity
type: `property_value_set` is the field, and `values` is its full value.

This follows from `save_property_values`'s write semantics, not from a general rule
about multi-valued fields elsewhere in the audit model. A write that replaces a whole
row set in one transaction has no smaller unit to diff at *that write's own grain*:
`ordinal` is reassigned fresh on every write, so there is no stable per-value identity
to diff a submitted value against a stored one by. `ADR-0018`'s `diff_instance`/
`diff_snapshots` distinction already anticipated this: `diff_snapshots` exists
precisely because #51's `PropertyValue` write has no single ORM instance whose
attribute history `diff_instance` could read.

### Why a per-ordinal diff was rejected

The alternative considered was comparing the old and new value lists by ordinal
position and auditing only the values that moved. Rejected because `ordinal` is
positional, not identity-bearing: inserting or removing one value from a three-value
set shifts every later value's ordinal, so a per-ordinal diff would report "value at
position 1 changed" for every value after an insertion point, even though most of them
did not change in any sense a reader would recognise. That is a misleading diff, not a
more precise one - it would fail NFR-08's field-level intent by a different route,
reporting spurious changes for values the write never touched.

A content-based diff (matching old and new values by equality rather than position, to
report only genuinely added/removed values) was also considered and rejected: it
requires a value-equality notion that holds across every property datatype
`registry/datatypes/` supports (FR-77, ADR-0013), which is exactly the kind of
datatype-specific dispatch ADR-0013 confines to that one package - `nptc.audit` has no
business making a datatype-aware equality judgement to build a diff.

## Consequences

- `_PROPERTY_VALUES_AUDIT_POLICY` in `property_values.py` gains a pointer to this ADR
  alongside its existing inline rationale; no change to `auditable`/`withheld`/
  `ignored`.
- The two tests pinning this shape
  (`test_save_property_values_first_write_audits_a_whole_set_snapshot_with_reason`,
  `test_save_property_values_second_write_audits_before_and_after_the_replacement` in
  `backend/tests/test_api_catalogue_properties.py`) have their docstrings updated to
  state the shape is confirmed by this ADR, not merely flagged as an open question. No
  assertion changes - the shape they pin was already correct.
- Any future write path that replaces a whole `property_value` set in one transaction
  (the bulk seam's per-entry `save_property_values` call included, per ADR-0035)
  inherits this same reasoning by construction - it is a property of what
  `save_property_values` does, not a decision made per caller.
- If cardinality or the value model changes so that individual values gain a stable
  identity across writes (rather than a purely positional `ordinal`), this decision
  should be revisited - it depends on that absence, not on multi-valued fields being
  exempt from field-level diffing in general.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A per-ordinal diff, comparing old and new value lists by position | `ordinal` is reassigned fresh on every write and carries no identity across writes; an insertion or removal shifts every later value's position, so this would report spurious changes for values the write never touched. |
| A content-based diff, matching old and new values by equality to report only genuinely added/removed values | Requires a value-equality notion that holds across every property datatype; that judgement belongs to `registry/datatypes/` alone (ADR-0013), not to `nptc.audit`. |
| Leaving the question open pending a per-value identity mechanism | No requester or design for per-value identity exists today; deferring the decision indefinitely leaves the two tests' docstrings misdescribing settled behaviour as an open question. |
