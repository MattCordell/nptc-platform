"""Catalogue entries, designations and code bindings. Lands with P1-5 (FR-03 to FR-08).

`nptc.catalogue.entries` (issue #46) delivers the entity itself:
`business_key` identity and `row_version` optimistic locking (FR-03,
FR-38).

`nptc.catalogue.designations` (issue #47) delivers designation storage -
synonyms and non-en-AU preferred-term variants as individual rows, never a
delimited string (FR-04) - plus FR-85/FR-24's computed (never stored)
preferred-term length. `nptc.catalogue.changelog` delivers FR-37's
changelog-note validation, shared by every write path in this package.

FR-05's collision detection (an error-severity duplicate against another
entry's preferred term, a warning-severity duplicate synonym) and code
bindings (FR-06 to FR-08) are separate issues (#49, #48) and are not
implemented here.
"""
