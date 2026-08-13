# ADR-0010: Import dataset format and the transform's role in minting `business_key`

**Status:** Accepted
**Date:** 2026-08-13

## Context

Issue #31 (P0-9) closes the transform's second terminal output: FR-70's "validated import
dataset" half (the defect report is the other, delivered with P0-1/#23 through P0-8/#30), and
FR-76's synthetic baseline release. `--emit-dataset` has existed as a CLI flag since P0-1 that
refused to run at all (`cli.py`'s `emit_dataset` branch printed "not implemented yet" and exited
2); `Mode.EMIT_DATASET` has existed on `RunResult` since the same PR as an unused enum member.
Landing this means: applying FR-71's auto-correctable band's repairs for real, writing a new
file, and deciding that file's shape.

FR-76 requires "the seeded data MUST be imported as a synthetic baseline release representing
the state at seeding, so that the first genuinely new release produces a meaningful diff
(FR-60)" - a machine-generated `Release` standing in for a curation cycle that never happened,
because it is the left-hand side FR-60 diffs the first real release against. This is a claim
about the *release*, not the data: the seeded content itself should be a genuine excerpt of the
published workbook wherever possible (the PRD's own planning scale is written against a real
50-row sample, PRD:227), which is why a real, committed excerpt fixture accompanies this issue
rather than synthetic fixture data standing in for it.

## Decision

1. **One new file, `import-dataset.json`, written into the existing `--report-dir`** alongside
   `report.json`/`report.md` - never a second output directory. This keeps the CLI's "no file
   outside `--report-dir` is ever touched" invariant true without qualification, and keeps one
   envelope discipline (own `schema_version`, no clock value, basename-only source, every
   collection explicitly sorted or built in a deterministic order, `encoding="utf-8"`,
   `newline="\n"`, overwrite in place) rather than inventing a second one.
2. **JSON, not CSV or NDJSON.** CSV was already rejected for the defect report (ADR-0009) for
   exactly the reason it is rejected here too: relational fan-out in CSV (one row per
   designation, one per code binding, one per property value, each needing its own foreign key
   back to the entry) re-introduces the delimiter and typing hazards FR-04/FR-06 exist to
   eliminate from the source data in the first place - the dataset format cannot itself become
   a new instance of the defect it is correcting. NDJSON (one JSON object per line, for
   streaming) is rejected because there is nothing to stream: the PRD's own planning scale tops
   out at roughly 5,000 entries (PRD:231 warns explicitly against complexity that scale does not
   need), and a single JSON document with an `entries` array is simpler to validate, diff, and
   read in full than a line-oriented format bought for a streaming property this dataset will
   never need.
3. **The transform mints `business_key`.** FR-03 requires every catalogue entry to carry an
   immutable `business_key` assigned at creation and never reused. For a seeded entry, the
   transform *is* the point of creation - there is no earlier system of record that could have
   assigned one - so it is generated here, deterministically: `NPTC-` plus a zero-padded
   six-digit sequence, numbered over entries in `(sheet name, row)` order. That ordering, not
   the workbook's own sheet order or any hash-derived order, is what keeps FR-73's
   byte-identical-output guarantee meaningful now that the pipeline actually transforms content
   rather than only reporting on it. **The numbering is positional, not content-derived**, and is
   therefore stable only across re-runs of a byte-identical workbook: inserting or deleting a row
   between runs - including the expected "fix at source and re-run" remedy for a blocking finding
   - shifts every subsequent entry onto a different `business_key`. Only the run that actually
   becomes the seeded baseline has authoritative keys; an earlier run's `import-dataset.json`,
   produced before a source correction, must never be persisted or diffed against (see the
   runbook's "The import dataset" section).
4. **`Length`, `Version` and `History` are not carried as entry fields.** `Length` MUST NOT be
   storable (FR-85) - it is computed in the export layer, and carrying it here would let a
   seeded value silently disagree with what the export computes later. `Version`/`History` MUST
   be generated from release membership (FR-59), which is precisely what `baseline_release`
   replaces going forward. The *existing* hand-typed `Version`/`History` cell values carry real
   provenance from the decade the workbook was hand-maintained, so they are preserved verbatim
   under `source.legacy_version`/`source.legacy_history` - immutable seeding provenance, never
   an editable field, so FR-24 is not violated and no information is destroyed at cutover.
5. **Specimen: verbatim always, code only where certain.** A specimen value's `code` is
   populated only on an *exact*, casefolded match against
   `specimen_table.SPECIMEN_TABLE`'s own surface forms (`cell_defects.resolve_specimen_term`) -
   deliberately not the word-boundary substring heuristic `semantic_drift.py` uses for its own
   free-text review, which is calibrated for a lower-stakes, informational purpose and would
   over-match here. An unmapped value is still seeded, verbatim, with `code: null`
   (`SPECIMEN_VALUE_UNMAPPED`, informational, never blocking) - the same "migrate the existing
   strings verbatim as provisional codes rather than guessing at a structure" precedent FR-88/
   FR-92 set. `'Any'` sets `specimen_unconstrained: true` and yields no specimen value for
   itself (FR-89), but never discards another value the same cell asserts: the published data is
   not guaranteed to keep `'Any'` from co-occurring with a named specimen on one row, and
   `build_dataset` must seed exactly what the report already describes for that cell, never less.
6. **Five new `FindingCode` members, emitted in both modes.** `EMPTY_SYNONYM_REMOVED`,
   `SPECIMEN_UNCONSTRAINED_RESOLVED` and `COMPOUND_VALUE_SPLIT` are auto-correctable (FR-71's own
   examples plus three narrowly-scoped structural repairs this issue adds); `SPECIMEN_VALUE_UNMAPPED`
   is informational; `MISSING_PREFERRED_TERM` (a row that resolves a code binding but carries no
   `RCPA Preferred term` value - there is nothing to seed a designation from) is data-defect,
   blocking, and row-level rather than cell-level, since the defect is the absence of a cell.
   All five are detected during the existing cell/row scan (`cell_defects.scan_workbook`), not
   only when `--emit-dataset` runs - so `--report-only` becomes a truthful preview of exactly
   what `--emit-dataset` will change, and a run without `--emit-dataset` still surfaces them for
   review. `report.json`'s `schema_version` moves 7 -> 8 to mark the widened vocabulary, even
   though the report's own shape is unchanged.
7. **Terminology-served enrichment is deferred, not attempted partially.** FR-82 requires stored
   `fsn`/`au_preferred_term` to come from the server exactly as served once `--check-terminology`
   ran. Doing that here would need the per-code `SweepResult` threaded through further than
   `RunResult` carries it today (`pipeline.RunResult` records only aggregate counts, e.g.
   `TerminologyRun.codes_checked`, never the resolved designations themselves). Rather than widen
   `RunResult`'s contract for this one issue, `edition_hint` stays `"unknown"` and `fsn`/
   `au_preferred_term` come from the published cell text/`null` unconditionally; a follow-up
   issue threads the sweep's results through once the backend's initial data load is designed
   enough to specify what it actually needs.
8. **A blocking finding aborts emission, never the report.** Exit 1, `report.json`/`report.md`
   are written as usual, and `import-dataset.json` is not written at all - PRD:310's "the seeded
   baseline cannot be created until RCPA-QAP resolves those collisions editorially". A partial
   dataset is worse than none: nothing downstream can distinguish "every row is clean" from
   "some rows were silently dropped" without re-reading the report.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A CSV-set (one file per entity: entries, designations, code_bindings, properties) | Relational fan-out in CSV needs its own foreign-key and delimiter discipline per file - exactly the hazard class (FR-04's delimited-string synonyms, FR-06's numeric code coercion) this issue exists to eliminate from the source data. `report.json`'s own precedent (ADR-0009) already rejected CSV for the same reason. |
| NDJSON, one entry per line | Bought for streaming a dataset size (≤ roughly 5,000 entries, PRD:231) this tool will never need to stream. A single JSON document is simpler to load, diff and validate in full, and needs no line-splitting discipline of its own. |
| Defer `business_key` minting to the backend's initial data load | FR-03 requires the key to be assigned *at creation*, and for a seeded entry the transform is the point of creation - there is no earlier system of record to defer to. Minting it later would also mean the transform's own output could not be diffed run-to-run by identity, only by content, which is a strictly weaker form of FR-73's determinism guarantee. |
| Populate `edition_hint`/`fsn`/`au_preferred_term` from the sweep now, by widening `RunResult` | Doable, but widens this issue's scope into a `pipeline.py`/`terminology_check.py` change with no consumer yet (the backend's initial data load, the only planned consumer, does not exist until P1-1) - deferred to a follow-up issue instead of speculatively building an interface nothing calls. |
| A second output directory for the dataset, separate from `--report-dir` | Breaks the CLI's own "no file outside `--report-dir` is ever touched" invariant for no benefit; the report and the dataset describe the same run and belong together. |

## Consequences

- **`report.json`'s `schema_version` moves from 7 to 8.** The four new `FindingCode` values can
  now appear in `defect_classes`; the report's own shape is otherwise unchanged. Any external
  tooling asserting on the closed set of codes (there is none known today) would need updating.
- **`import-dataset.json` is a new file with its own `schema_version` (1), starting fresh** -
  not extending `report.json`'s schema numbering, since it is a structurally different artefact
  (a dataset, not a report) with its own reasons to change version independently.
- **The backend's initial data load has a concrete file to design against**, once
  `backend/src/nptc/{catalogue,releases,db}` move off scaffolding (P1-1) - this issue does not
  build that loader, only the file it will read.
- **FR-88/FR-89/FR-90/FR-92 stay `planned`, not `implemented`**, in `requirements.yaml`: their
  home is the backend's property registry (P1), and the transform's seeding-time treatment here
  is a provisional migration step, not the governed value set/code system those requirements
  actually ask for. Each requirement's `notes` records what this issue's seeding-time treatment
  does and does not cover.
