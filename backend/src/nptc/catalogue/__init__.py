"""Catalogue entries, designations and code bindings. Lands with P1-5 (FR-03 to FR-08).

`nptc.catalogue.entries` (issue #46) delivers the entity itself:
`business_key` identity and `row_version` optimistic locking (FR-03,
FR-38).

`nptc.catalogue.designations` (issue #47) delivers designation storage -
synonyms and non-en-AU preferred-term variants as individual rows, never a
delimited string (FR-04) - plus FR-85/FR-24's computed (never stored)
preferred-term length. `nptc.catalogue.changelog` delivers FR-37's
changelog-note validation, shared by every write path in this package.

`nptc.catalogue.bindings` (issue #48) delivers code binding storage - the
SNOMED CT code, `fsn` and `au_preferred_term` stored exactly as served
(FR-06, FR-08, FR-82), never cleaned or re-derived.

`nptc.catalogue.collisions` (issue #49) delivers FR-05's collision
detection - an error-severity rejection when a synonym exactly matches
another live entry's preferred term (or the symmetric case), and a
warning-severity query for the same synonym on multiple live entries,
resolvable to an acknowledged state - plus FR-08's blocking severity (one
active SNOMED code cannot be bound to two entries), added to
`nptc.catalogue.bindings.create_binding`. FR-84's subsumption check is
still not here - that is the FR-45 validation sweep's own concern, layered
on top of the rows `nptc.catalogue.bindings` creates.
"""
