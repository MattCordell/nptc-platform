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

FR-05's collision detection (an error-severity duplicate against another
entry's preferred term, a warning-severity duplicate synonym) is a separate
issue (#49) and is not implemented here. Nor is FR-84's subsumption check -
that is the FR-45 validation sweep's concern, layered on top of the rows
`nptc.catalogue.bindings` creates.
"""
