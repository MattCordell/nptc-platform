"""Catalogue entries, designations and code bindings. Lands with P1-5 (FR-03 to FR-08).

- `nptc.catalogue.entries`: the entity itself, with `business_key` identity
  and `row_version` optimistic locking (FR-03, FR-38).
- `nptc.catalogue.designations`: designation storage - synonyms as
  individual rows, never a delimited string (FR-04) - and FR-85/FR-24's computed, never
  stored, preferred-term length.
  `nptc.catalogue.changelog` holds FR-37's changelog-note validation, shared
  by every write path here.
- `nptc.catalogue.bindings`: code binding storage - the SNOMED CT code, `fsn`
  and `au_preferred_term` stored exactly as served, never cleaned or
  re-derived (FR-06, FR-08, FR-82).
- `nptc.catalogue.collisions`: FR-05's collision detection (an
  error-severity rejection, a warning-severity query and its acknowledgement)
  and FR-08's blocking severity, which `bindings.create_binding` applies.
  FR-84's subsumption check is not here: it is the FR-45 validation sweep's
  concern, layered on the rows `bindings` creates.
"""
