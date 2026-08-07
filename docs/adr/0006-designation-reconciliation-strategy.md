# ADR-0006: Designation reconciliation strategy - local-first classification, a monotone server probe, and a workbook-scoped index for the "wrong concept" outcome

**Status:** Accepted
**Date:** 2026-08-07

## Context

FR-97 requires the seeding transform to classify every workbook row's `SNOMED CT Fully
Specified Name` column value against the designation set of the concept its code is bound
to, into four outcomes: matches the tag-stripped FSN (seed silently); matches another
active designation on the same concept (informational, "drift"); matches a designation of
a *different* concept, or of none at all (both blocking data defects - FR-71's own words,
"the most dangerous outcome... because both halves look individually plausible"). FR-97
also requires a separate, always-informational list of rows where the current AU
preferred term differs from the column value.

Three problems have no single obvious answer:

**Request shape.** FR-52 forbids one `$validate-code`/`$lookup` per catalogue code - at
the PRD's 20,000-code planning ceiling that is 40,000 sequential requests, and PRD
Appendix A.10's own method note ("Operations used: ... `$validate-code`...") describes
exactly that anti-pattern at the 50-row sample scale where it happens not to matter yet.

**FR-83's exclusivity.** FR-83 states semantic-tag removal "happens only in the export
renderer, never in storage or in validation" - but FR-97's first outcome is defined in
terms of "the tag-stripped FSN," which requires stripping a tag somewhere to compare
against.

**No server primitive for "matches a different concept."** The FR-53 client contract has
no reverse designation search - nothing that goes from a display string to the concept(s)
that carry it - and `$expand` with a `filter=` parameter is server-defined substring
matching, not equality, and not deterministic across server versions (FR-73).

## Decision

**1. Classify locally against `SweepResult.designations` first; escalate only the local
miss to one `CodeSystem/$validate-code` per unique `(code, label)` pair
(`TerminologySweep.confirm_labels`), and make the probe strictly monotone.**

`TerminologySweep._resolve_status`'s bulk `$expand` already fetches every active
concept's designations (`includeDesignations=true`), for FR-99's tag check. Retaining a
deduplicated, sorted projection (`ConceptDesignations`) on `SweepResult` gives FR-97's
classification against the concept's *own* designation set at zero extra requests.
Only a label matching nothing there is escalated - the delta, matching `client.py`'s own
`validate_code` docstring ("one call per row is legitimate only in the seeding
transform's designation-reconciliation pass, where the delta is the workload").

The probe can only make an outcome **more** benign, never less: no local match issues a
probe; a server confirmation downgrades to informational; a server rejection leaves the
local verdict - and the workbook-scoped outcome-3/4 discrimination below - exactly as it
would have run without a probe at all. This is what keeps a server whose `$validate-code`
display matching is imperfect (language-scoped, say) from turning a benign label into a
false abort: the probe is a rescue, never an additional way to fail.

**2. `strip_semantic_tag` lives in `nptc_shared.terminology.snomed`, beside
`semantic_tag`, as a second, narrowly scoped call site on FR-83's rule, not a
violation of it.**

FR-83's argument for "only in the export renderer" is: exactly one call site, whose input
is always a served FSN read fresh, so a double strip is structurally impossible. That
invariant - never storage, never a round-tripped value, input always freshly served -
holds here too: `strip_semantic_tag`'s input in this pass is
`ConceptDesignations.fully_specified_name`, read off the wire in the same sweep run,
compared once with `==`, and discarded. What FR-83 actually forbids is a second,
independently-derived stripping rule that could disagree with the export renderer's; using
the *same* function from a second caller cannot disagree with itself.

The residual honesty: FR-83's literal words say "never ... in validation," and this is
validation. This ADR records that as a deliberate, scoped amendment rather than eliding
the tension - the mechanism FR-83 protects is intact, but its stated scope is narrower
than FR-97 turned out to need.

**3. The "matches a different concept" outcome is workbook-scoped: an index of every
designation value (including each concept's tag-stripped FSN) resolved anywhere in the
current run, built once from the sweep results already in hand.**

A label failing the local check and the server probe is looked up in this index. If it
names a *different* code this workbook binds, that is
`LABEL_BOUND_TO_OTHER_CONCEPT`; otherwise `LABEL_MATCHES_NO_DESIGNATION`. Both block, so
a miss between the two is a message-quality question, not a safety one - the import
aborts either way.

**4. Benign outcomes are a union across every edition the code resolved in; AU is
authoritative for the FSN quoted in a message, for the probe, and for the
preferred-term-differs check specifically.**

Mirrors FR-71's own "not resolving in *either* edition" construction for code status: a
label valid against one edition's designation set is not a defect merely because a
different edition's set doesn't happen to carry it too (an AU-only extension code is the
expected shape of the catalogue, FR-47). The preferred-term check reads only the AU
edition's `display` and is skipped for a code that did not resolve in AU at all, because
FR-82's preferred-term comparison is specifically the AU language reference set - a
non-AU `display` value is not the preferred term FR-82 means.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| One `$validate-code` per row, as PRD Appendix A.10's method note describes | Exactly FR-52's forbidden shape at scale: 20,000 sequential requests at the PRD's planning ceiling, discourteous to a shared server, and indistinguishable from a design that "quietly works at 50 codes." |
| Trust `$expand`'s designation list alone, with no server probe at all | Cheapest, but a server that returns an incomplete designation set in one expansion (a paging quirk, a non-conformant `includeDesignations`) would abort a seeding run over data the server itself would confirm as valid on direct question - inverting FR-54's hazard from "hide an outage" to "manufacture a defect." |
| A private regex-based tag-stripper local to `designation_check.py`, instead of a second call to `nptc_shared.terminology.snomed.strip_semantic_tag` | Recreates the exact defect class FR-83 exists to prevent - two independently maintained ideas of "the tag" that can silently drift apart, this time between the export renderer and the seeding transform instead of between two seeding-transform call sites. |
| `$expand` with `filter=<label>` per unmatched label, to find the concept(s) actually carrying it | One request per delta row (no better than per-row `$validate-code`), and `filter` is server-defined substring matching, not equality - a confidently *wrong* answer is worse than the honest workbook-scoped narrowing this ADR chose, which degrades to "matches nothing" rather than naming the wrong concept. |
| Intersection across editions for the benign outcomes (a label must match in every edition that resolved the code) | Any AU/International designation difference - which is FR-45's `fsn_drift` at steady state, not a P0 seeding concern - would abort a seeding run over a discrepancy the platform is explicitly designed to tolerate until first sync. |
| AU-only, with no edition fallback for the FSN/probe/message | An International-only-resolved code (rare for this catalogue, but not excluded by the schema) would have nothing to quote or probe against, rather than gracefully falling back to whatever edition did resolve it. |

## Consequences

- `SweepResult` gains a `designations: tuple[ConceptDesignations, ...]` field;
  `_unexpected_tags` (FR-99) now derives from the same projection instead of its own
  independent dedup of the raw `ExpandedConcept` list, so the two checks cannot disagree
  about which of two duplicated pages won.
- `Edition` gains `display_language`, set only on `SNOMED_CT_AU`; `Edition.pinned_to` was
  updated to carry it through, or FR-49's reproduce-a-historical-run path would silently
  lose it.
- `TerminologySweep` gains `confirm_labels`, the one caller of `validate_code` this
  codebase has - request-count discipline enforced by test, the same way `run` already is.
- Three new report provenance counters (`labels_reconciled`, `labels_not_reconciled`,
  `label_confirmations`), and `report_writer.SCHEMA_VERSION` moves from 3 to 4.
- A label belonging to a concept this workbook does not bind anywhere reads as
  `LABEL_MATCHES_NO_DESIGNATION`, not `LABEL_BOUND_TO_OTHER_CONCEPT` - both block, so this
  is a documented limitation of outcome 3's diagnosis, not a correctness gap (see the
  [transform runbook](../operations/runbooks/transform.md#interpreting-a-designation-finding-fr-97)).
- A future FR-83 reviewer will find a second call site for tag-stripping and needs this
  ADR to know it was a deliberate, scoped exception rather than drift.
