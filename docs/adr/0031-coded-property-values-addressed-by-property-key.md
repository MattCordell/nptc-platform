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

### `ecl_from_implicit_value_set_url` and `edition_from_implicit_value_set_url`, the inverse of the existing builder

A property's `value_set_uri` is stored as an implicit value set URI, not as bare ECL, so
resolving it through `expand` needs the ECL parsed back out -
`ecl_from_implicit_value_set_url` parses the `?fhir_vs=ecl/<percent-encoded ECL>` query
half back to a plain ECL string.

The *edition* `expand` needs is a separate, harder problem, because this codebase's real
`value_set_uri` values are not all one shape. `implicit_value_set_url`'s own output is
module-qualified (`<system>/<module>[/version/<v>]?fhir_vs=ecl/...`) and self-describing -
the module id, and any pinned version, is read straight back out of it. But PRD S6.6's own
worked example (line 417), and `nptc.db.bootstrap`'s real seeded `specimen` binding,
instead store the *bare*-system form `<system>?fhir_vs=ecl/...`, which carries no module
at all - there is nothing in that URI to recover an edition from. `edition_from_implicit_
value_set_url` (`shared/src/nptc_shared/terminology/snomed.py`) handles both: a
module-qualified URI resolves from itself alone; a bare-system URI falls back to matching
the stored `binding.edition` label against the two well-known editions
(`SNOMED_CT_AU`/`SNOMED_CT_INTERNATIONAL`), raising rather than fabricating an `Edition`
for a label that names neither (issue #247 review, see Amendments below - the original
`_edition_for` docstring's claim that "expand is driven entirely by the ECL" was wrong: the
bare-system shape means the edition genuinely cannot come from the URI alone, so the label
*is* load-bearing for that shape, just not for the module-qualified one). Any URI shape
neither function recognises (a non-implicit URI, `?fhir_vs=isa/...`/`?fhir_vs=refset/...`,
a module id or bare-system label that matches no known edition) raises `ValueError` - the
route wraps that into a typed 500 (`PropertyValueSourceMisconfiguredError`), a
data-integrity fault in the stored definition, never a caller mistake.

The resolved edition's `display_language` is passed to `expand` too (FR-82) - a picker
needs the edition's own preferred term, not whatever the server defaults to.

### Server-side `filter` on `expand`

FHIR `$expand` already defines a `filter` parameter, evaluated server-side - Ontoserver's
own implementation is a word-prefix match against a candidate's display, not a general
substring match. Added as a fifth parameter to `TerminologyClient.expand` (both
`OntoserverClient` and `StubTerminologyClient`, contract-tested against both) so a
picker's search-as-you-type narrows results without pulling the whole expansion
client-side first. `list_local_codes`'s own `filter` is a plain, case-insensitive `ILIKE`
substring match against `display` only (never `code`) - proportionate to a governed
vocabulary's handful of codes, not `nptc.catalogue.search`'s trigram/full-text ranking
machinery built for ranked search across the whole catalogue.

**The two branches' `filter` semantics are not identical**, even though the response
shape is (issue #247 review): a mid-word needle matches on the local-code side (`ILIKE
'%needle%'`) but may not on the SNOMED side (word-prefix), and typing a code matches
neither side's `filter` at all (SNOMED's is display-only by FHIR definition;
`list_local_codes` deliberately doesn't extend its `ILIKE` to `code`, see that function's
own docstring). This is a real, user-visible asymmetry a picker's search box can surface,
accepted here because both are still "server-side text narrowing over `display`", and
because reconciling the two down to one exact matching algorithm would mean either giving
up Ontoserver's own search relevance or reimplementing it client-side against the local
codes, neither of which this route needs.

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

## Amendments

**2026-09-03:** The first draft's "`ecl_from_implicit_value_set_url`" decision described
the edition as recovered *entirely* from the stored `binding.edition` label, with the
parsed ECL as the only thing actually taken from `value_set_uri`, treating a label match
against exactly `"au"`/`"int"` as sufficient and anything else as falling back to
`Edition(module_id=label, label=label)`. Automated review on PR #250 found this was wrong
in a way that mattered: that fallback fabricates a nonsense `Edition` for any label that
isn't one of the two known ones (rather than raising the typed 500 this module already
has for exactly this class of fault) and, for a module-qualified `value_set_uri` (not in
use today, but a real future shape once a pinned binding is created), would silently
discard the URI's own pinned `/version/<v>` segment in favour of an unpinned edition
derived from the label alone. The recovered `display_language` was also never actually
passed to `expand`, so FR-82's stated benefit wasn't delivered. Corrected by adding
`edition_from_implicit_value_set_url` (resolves a module-qualified URI from itself alone,
preserving any pinned version; falls back to a label match, validated against the known
editions rather than fabricated, only for the bare-system shape that carries no module -
see the revised Decision section above) and by passing its `display_language` through to
`expand`. Also corrected: `filter`'s FHIR `$expand` semantics were described as "case-
insensitive substring"; Ontoserver implements word-prefix matching, not substring, and
`list_local_codes`'s own `ILIKE` was not stated to be `display`-only (never `code`) - see
the revised "Server-side `filter` on `expand`" section.
