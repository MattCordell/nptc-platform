# ADR-0008: Specimen inspection strategy — ECL set-membership over the `Has specimen` attribute, a hand-typed + server-augmented specimen table, and a coverage audit for what the table doesn't cover

**Status:** Accepted
**Date:** 2026-08-12

## Context

FR-75 asks the transform to report a semantic mismatch between the RCPA preferred term's
own specimen/timing wording and what the bound SNOMED concept actually models - H-03's own
words, "the preferred term implies a specimen or timing constraint the bound SNOMED concept
does not carry." PRD Annex A.9 supplies four worked examples, verified live against SNOMED
CT-AU during this feature's planning: `47615003` (Acetone urine, models `Has specimen` =
`122575003` Urine specimen - benign), `430551003` (14-3-3 protein CSF, models `Has specimen`
= `258450006` Cerebrospinal fluid specimen - benign, and the term's own FSN already says
"CSF"), `121302000` and `121960004` (both assert a specimen the concept models none of at
all - genuine gaps). Two of four benign, two of four genuine is not a rate a blocking band
survives.

Two ECL forms were verified live against the four rows above: `(codes) MINUS (* : 116686009
= *)` and `(codes) AND (* : 116686009 = <<122575003)`, both returning the expected sets.

Three problems have no single obvious answer:

**What counts as "the concept's specimen" with no per-row `$lookup`.** FR-52's request
budget applies here exactly as it does to FR-97's designation reconciliation (ADR-0006):
20,000 sequential `$lookup` calls is the anti-pattern this platform exists to avoid, and
"does concept X model specimen Y" is a *set-membership* question, well suited to ECL, not a
per-row question that needs the value read back.

**How to recognise "urine"/"CSF"/"24 hour urine" in free text without hand-maintaining a
catalogue-wide dictionary.** RCPA's specimen vocabulary is small (a few dozen distinct
specimen types across the whole catalogue) and stable, unlike FR-79's misspelling problem,
which is genuinely open-ended.

**What a term that mentions a specimen the table doesn't cover looks like, and what to do
about it.** An allowlist that silently never inspects an uncovered specimen is a different,
quieter failure mode than a false positive - it needs its own signal, not a false sense of
completeness.

## Decision

**1. Two new `TerminologySweep` methods, `codes_without_attribute`/
`codes_with_attribute_value`, express "does concept X model attribute A at all" and "is X's
value for A subsumed by root R" as chunked ECL, generalising the same chunking helper FR-84's
hierarchy check already uses.**

`(chunk) MINUS (* : <attr> = *)` and `(chunk) AND (* : <attr> = <<root>)`, chunked the same
way ADR-0005 chunks FR-84's own `MINUS <<71388002` idiom (that ADR's own ~340KB measurement
applies to any disjunction of catalogue-scale codes, not only the procedure-hierarchy one).
The `<<` on the *value* side of the second form - not merely wrapping the whole refinement -
is deliberate: it is what catches a descendant specimen value (e.g. "Urine specimen from
catheter" under "Urine specimen") as agreeing, rather than only an exact-match root.

**2. A third method, `describe`, resolves a set of codes' own designations directly - never
through a hierarchy expression, and deliberately not through `run()`.** `run()`'s FR-84
hierarchy check and FR-99 semantic-tag check would both misfire on every specimen concept
(never a procedure, never tagged `(procedure)`), so this is a separate, minimal path reusing
the same paging/dedup-tolerant `_expand_chunk` primitive and the same `_project_designations`
projection FR-97 already relies on.

**3. A hand-typed specimen table (`nptc_transform.specimen_table.SPECIMEN_TABLE`), 16 groups,
each pairing a verified SCTID with a short list of plausible RCPA-style surface forms -
augmented at run time by each group's own `describe()`-fetched designations for the
*visibility filter* specifically, never for what counts as an assertion in the first
place.** A term's own wording is matched against the *hand-typed* terms only, to decide
which specimen it asserts (deterministic, reviewable, stable). Whether that assertion
needs a server round-trip at all is then decided against hand-typed *and* server-served
terms together: a term whose own designations already carry the specimen concept's own
served synonym (CSF's "CSF specimen"/"CSF - Cerebrospinal fluid sample", say) is visibly
consistent without a classification request, even if the hand-typed table happens not to
list that exact synonym.

**4. The workbook's own `Specimen` column is read only as a coverage audit, counting
distinct values that map to no group in the table - never as an assertion source.** Free
text a curator typed is not controlled vocabulary, and trusting it as ground truth would
let one curator's shorthand silently redefine what "asserts a specimen" means. Excluding
`Any`/`Fluids` (which the wider plan gives their own, separate findings) keeps this audit
from double-counting a gap another check already owns.

**5. Exactly one finding per row, precedence `TERM_SPECIMEN_NOT_MODELLED` >
`TERM_SPECIMEN_DIFFERS` > `TERM_TIMING_NOT_MODELLED`, all `Band.INFORMATIONAL`.** Annex A.9's
own 50% false-positive rate on the four worked rows makes a blocking band indefensible (see
`bands.py`'s own reasoning for `INFORMATIONAL`, ADR-0004). A row asserting both an unmodelled
specimen and an unmodelled timing gets one finding, at the stronger code, whose message
names both - never two findings describing the same row's content twice.

**6. The total request cost is `2 + G`, not literally the plan's own working title of
`1 + G`.** Two pieces are structurally fixed, regardless of how many groups are asserted:
one `describe()` call (the specimen table's own vocabulary, for the visibility filter and
for messages) and one `codes_without_attribute` call (restricted to whatever codes the
visibility filter didn't already clear - this is what distinguishes
`TERM_SPECIMEN_NOT_MODELLED` from `TERM_SPECIMEN_DIFFERS`; without it the two could not be
told apart at all). Only the third piece - "does code X's value agree with group G
specifically" - scales with `G`, the number of distinct groups still asserted after the
visibility filter. Neither fixed piece can be merged into the other without losing a
capability the requirement itself needs, so this ADR records `2 + G` as the actual,
tested invariant rather than forcing an artificial single-call merge to match an earlier,
looser framing.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Per-row `CodeSystem/$lookup` with `properties=Has specimen` | ADR-0006's own request-budget argument, restated: one call per asserting row does not survive catalogue scale. It would also let the check keep the actual specimen *value* for a richer message, which this ECL approach deliberately trades away - noted here as a real cost, and one small private function to add later if a richer message is ever worth a second request shape, not a redesign. |
| Equality instead of `<<` closure on the value side | A descendant specimen value (e.g. a catheter-collected urine specimen under plain "Urine specimen") would read as `TERM_SPECIMEN_DIFFERS` - a false positive on exactly the kind of more-specific-but-correct modelling a terminologist would expect to be unremarkable. |
| Per-`(group, value)` `CodeSystem/$subsumes` in a loop | `client.py`'s own "single-code by nature, explicitly not the way to validate the catalogue" discipline for `validate_code` applies identically to `subsumes` - a loop over every asserting code times every candidate group is the same forbidden shape FR-52 exists to prevent. |
| A YAML/JSON specimen table instead of a Python module | No data-file precedent under any `src/` in this repo; a hand-maintained data file drifts from its own tests with nothing to catch it, unlike a Python module `test_specimen_table.py` can assert directly against (`has_valid_check_digit`, unique keys). |
| A blocking band (`data-defect`/`requires-human-decision`) | Annex A.9's own worked examples: two benign rows out of four confirmed live. A false-positive rate that high aborting an otherwise-clean seeding run would be a worse failure mode than a small number of informational findings nobody is forced to act on. |
| Using the workbook's own `Specimen` column as an assertion source, not just a coverage audit | Free text a curator typed over more than a decade, not controlled vocabulary - trusting it as ground truth risks manufacturing findings from a curator's own shorthand rather than from the concept model FR-75 is actually about. |

## Consequences

- `nptc_shared.terminology.models.HAS_SPECIMEN_ATTRIBUTE` (`116686009`) is a new constant,
  alongside `PROCEDURE_ROOT_CODE`/`FSN_USE_CODE`.
- `TerminologySweep._check_hierarchy`'s own chunking loop is now expressed through a shared,
  parameterised `_expand_combined` helper (byte-identical ECL, only the plumbing moved) that
  `codes_without_attribute`/`codes_with_attribute_value` reuse for the identical chunking
  discipline.
- `StubTerminologyClient`'s ECL subset grows two refinement forms
  (`* : <attr> = *`, `* : <attr> = <<root>`) and a top-level `AND`, on top of the existing
  literal-disjunction/`<<`/`<`/`MINUS` subset - still not an ECL engine, an unsupported
  refinement still raises `StubEclNotSupportedError`.
- `transform/src/nptc_transform/specimen_table.py` and `semantic_drift.py` are new modules;
  three new finding codes (`TERM_SPECIMEN_NOT_MODELLED`, `TERM_SPECIMEN_DIFFERS`,
  `TERM_TIMING_NOT_MODELLED`), all `Band.INFORMATIONAL`.
- `RunResult.drift` is `None` under the same condition as `terminology`/`designations` (no
  live sweep, no drift pass) - `report_writer.SCHEMA_VERSION` moves 5 → 6.
- FR-75 moves to `implemented` (this PR delivers the full requirement, unlike FR-79/FR-36 in
  the preceding PR); H-03 in `docs/governance/hazard-log.md` moves to "mitigation implemented."
- A future reader measuring this pass's request cost against the plan's own working title
  ("1 + G") will find `2 + G` instead, and this ADR's Decision 6 is the record of why that is
  a deliberate correction, not a miscount left unexamined.
