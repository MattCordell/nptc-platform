# ADR-0031: Coded-property values addressed by property key, one route, offset/count paging

**Status:** Accepted
**Date:** 2026-09-03

## Context

Three of the four seeded registry properties (`specimen`, `discipline`, `subgroup`) are
`datatype = "code"`, and `CodeHandler.form_control` asks the frontend to render a
`ControlKind.CONCEPT_PICKER` for each. Nothing served that picker any values:
`TerminologyClient.expand` (SNOMED value-set binding) and the `LocalCode` table (governed
local code system binding) both existed, but neither was reachable over HTTP, so #151's
acceptance criterion ("coded properties present values from their bound value set,
FR-10") had no data source. `specimen` binds to a SNOMED implicit value set; `discipline`
and `subgroup` bind to governed local code systems Ontoserver does not hold.

Two design questions had to be settled before any route could be written: how a client
asks for a coded property's values, and how the two binding targets are served behind
one shape.

## Decision

### Addressed by property key, never by binding target

`GET /api/v1/registry/properties/{key}/values` takes only the property `key` and paging/
filter parameters. It never takes a `binding_target`, a value set URI, or an ECL - the
server resolves `key`'s own `PropertyDefinitionSpec.binding` and answers from Ontoserver
or the `LocalCode` table itself. The response shape (`{items: [{code, display}], total}`)
is identical either way; a client cannot tell which value source answered, and nothing in
`frontend/src` (or the backend router/response model) branches on `binding_target` at all.

`nptc.catalogue.property_value_sources.list_property_values` is the one function in the
whole backend that reads `binding_target` - a router or frontend branch on it would be
`datatype == "code"` in disguise, the exact proxy-switch [ADR-0013](0013-datatype-handler-registry.md)
SS5 names as its hardest-to-catch violation class, since the syntactic `datatype`-compare
AST guard (`test_datatype_dispatch.py`) cannot see a `binding_target` string comparison as
the same defect.

### One service function owns the branch, living in `nptc.catalogue`, not `nptc.registry`

`nptc.registry` is a leaf package (ADR-0013 SS2): its own imports prove it never reaches
`nptc.db` or a `Session`. The new service needs both - a `Session` to load the
`PropertyDefinition` and query `LocalCode`, and a live `TerminologyClient` call - so it
lives in `nptc.catalogue`, alongside `local_codes.py`/`property_values.py`, which already
combine `nptc.db` access with `nptc.registry.handlers` types for the identical leaf-rule
reason.

### Paging is offset/count, not the catalogue's own opaque keyset cursor

Every other paginated route in this API (`nptc.catalogue.entries`/`search`,
[ADR-0024](0024-catalogue-search-and-pagination.md)) uses a keyset cursor - an offset
re-reads and re-skips every earlier row on every page, and drops or repeats rows outright
when a concurrent insert shifts the window mid-scan. This route uses `offset`/`count`
instead, deliberately: `TerminologyClient.expand`'s FHIR `$expand` only speaks
offset/count - there is no keyset primitive on the SNOMED side at all. Since a single
route must answer both binding targets with one shape, and the SNOMED side has no keyset
option, offset/count is the only shape both can share. `list_local_codes` could support
keyset paging in isolation (`LocalCode` has a real, stable `(display_order, code)` key),
but forcing that shape onto this route while the SNOMED side stays offset-based would
recreate exactly the client-can-tell-them-apart problem this ADR's first decision exists
to avoid.

This is a narrower hazard than the one ADR-0024 guards against: a value-set expansion or a
governed local code system is comparatively static and read-mostly, not the actively-edited
catalogue ADR-0024 was written against, so an occasional skipped/repeated row under a rare
concurrent local-code write is an acceptable trade against a uniform two-source shape.

### `ecl_from_implicit_value_set_url`, the inverse of the existing builder

A property's `value_set_uri` is stored as a full implicit value set URI
(`nptc_shared.terminology.snomed.implicit_value_set_url`'s own output shape), not as bare
ECL. Resolving it through `expand` needs the ECL back out, so
`ecl_from_implicit_value_set_url` (`shared/src/nptc_shared/terminology/snomed.py`) parses
the `?fhir_vs=ecl/<percent-encoded ECL>` form back to a plain ECL string, round-tripped
against the builder in tests. Any other shape (a non-implicit URI, `?fhir_vs=isa/...`,
`?fhir_vs=refset/...`) raises `ValueError` - the route wraps that into a typed 500
(`PropertyValueSourceMisconfiguredError`), since every real `value_set_uri` in this
database is written by the one builder this parses the inverse of, making the failure a
data-integrity fault in the stored definition, never a caller mistake.

### Server-side `filter` on `expand`

FHIR `$expand` already defines a `filter` parameter - a case-insensitive substring match
against a candidate's display, evaluated server-side. Added as a fifth parameter to
`TerminologyClient.expand` (both `OntoserverClient` and `StubTerminologyClient`,
contract-tested against both) so a picker's search-as-you-type narrows results without
pulling the whole expansion client-side first. `list_local_codes`'s own `filter` is a
plain `ILIKE`, proportionate to a governed vocabulary's handful of codes - not
`nptc.catalogue.search`'s trigram/full-text ranking machinery, built for ranked search
across the whole catalogue.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A `binding_target` (or value-set-URI/ECL) query parameter, client-supplied | Lets the client learn and depend on which value source answers - the exact coupling this ADR exists to prevent, and a second, client-visible encoding of `datatype == "code"`. |
| Two routes, one per binding target | Same coupling problem one level up: a frontend picker would have to know which route to call for which property, rather than the property registry alone deciding. |
| Keyset paging on both sides, forcing an ECL-range cursor onto `expand` | `$expand` has no native keyset primitive - forcing one would mean either building a second, synthetic offset-tracking cursor on top of it (no real benefit over passing the offset directly) or dropping the SNOMED side down to one-page-at-a-time with no continuation at all. |
| A hand-rolled ECL parser, independent of `implicit_value_set_url`'s own construction | Two independent readings of the same URI shape that could silently drift apart - `ecl_from_implicit_value_set_url` is deliberately the exact inverse of the one builder, tested by round-trip. |

## Consequences

- Any future coded property (a new value-set or local-code-system binding) is served by
  this same route with no new endpoint and no frontend branch - the picker only ever
  needs a property `key`.
- `nptc.catalogue.property_value_sources` is the one place to extend if a third binding
  target is ever added; the router, response models, and frontend stay untouched.
- `TerminologyClient.expand`'s `filter` parameter is now part of the FR-53 contract
  surface for both implementations, not just this route - a future caller can rely on it
  too.
- This route's paging shape (`offset`/`count`, `total`) is a deliberate, documented
  exception to ADR-0024's keyset convention for the rest of the catalogue API - a reviewer
  should not read it as an inconsistency to fix, and a future *catalogue-editing* route
  should still default to ADR-0024's keyset shape unless it has the same both-sources
  constraint this route does.
