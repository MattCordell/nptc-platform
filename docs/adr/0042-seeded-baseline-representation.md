# ADR-0042: The seeded baseline is an immutable seed record, not a placeholder Release row

**Status:** Accepted
**Date:** 2026-10-03

## Context

FR-76 requires the seeded catalogue to be imported as a synthetic baseline release, so the
first genuinely new release produces a meaningful diff (FR-60). ADR-0010 made the transform
emit that release as the `baseline_release` block of `import-dataset.json`, and kept each
entry's hand-typed `Version` and `History` cells verbatim as `source.legacy_version` and
`source.legacy_history`, so cutover destroys no information.

The `release` table is P4's (FR-57, FR-58, FR-61, FR-60). `nptc.releases` is still a stub,
and the API and history rows already carry always-`null` `release` slots that wait for it
(ADR-0034). The seed loader therefore has to decide how the baseline is recorded now, and
where the legacy cells live, without fixing P4's design early.

## Decision

1. **No `release` table.** The baseline is recorded in `seed_import`, a table that holds at
   most one row (a unique `singleton` column checked `TRUE`). It stores the dataset's
   `baseline_release` name and note, the source filename and SHA-256, the dataset's
   `schema_version` and the entry count. P4 promotes this row into a real `Release`. FR-76
   stays `in-progress` until then, and FR-60's diff behaviour is deferred with it.
2. **Provenance in its own table.** `entry_seed_provenance` holds, per seeded entry, the
   workbook sheet and row and the verbatim `Version` and `History` cells. `catalogue_entry`
   gains no columns, so it carries nothing that is null for every entry created after
   cutover.
3. **Written once, never edited.** `nptc_app` holds `SELECT, INSERT` on both tables and no
   other privilege, so ADR-0010's "never an editable field" holds at the privilege level, as
   it does for `designation_collision_acknowledgement`.
4. **Fail closed on a non-empty catalogue.** The loader refuses if any `catalogue_entry` row
   exists, with its own exit code. `business_key` numbering is positional (ADR-0010), so two
   runs could attach one key to different clinical concepts without any error. A reseed is a
   deliberate database reset.
5. **Refuse what the model cannot store; never repair.** The loader refuses the whole dataset,
   before any write, for a specimen value with no SNOMED CT code, for a named specimen on an
   entry flagged `specimen_unconstrained` (FR-89), and for a code binding with no FSN. Every
   problem is listed at once. This narrows ADR-0010 decision 5: the dataset still carries an
   uncoded specimen verbatim, so the transform's report stays complete, but the loader will
   not seed it. A `property_value` holds `{system, code}`, and the specimen binding requires
   a code in the specimen value set.
6. **Classification resolves through the governed code systems, by one rule.** A discipline or
   subgroup label matches the one active local code whose display or code equals it, ignoring
   case. An unmatched discipline label refuses the run, because the vocabulary is RCPA-QAP's
   to extend (FR-90). An unmatched subgroup label becomes a provisional local code, verbatim
   (FR-92). A label whose only matches are deprecated, or that names more than one active
   code, refuses the run. Every such refusal is listed before any entry is written, and
   provisional codes are created only after none remain.
7. **One transaction, real write paths.** The loader writes through `create_entry`,
   `add_synonyms`, `create_binding` and `save_property_values`, never a Core insert or
   `COPY`, so FR-05, FR-37, NFR-08 and `clean_term` apply to every seeded row. The caller
   owns the commit, and `--dry-run` rolls back. It runs `seed_system_properties` first,
   because nothing else creates the four system property definitions on a new deployment.

## Rejected alternatives

- **A placeholder `Release` row now.** It would force decisions about FR-57's naming, FR-58's
  membership and FR-61's configuration versions before P4 designs them, and the first real
  release could inherit a shape that was only ever a stand-in.
- **Seed with no record of the run until P4.** The baseline's name, note and source digest
  would then exist only in a file that may be deleted, and nothing in the database could
  say which workbook the catalogue came from.
- **Nullable provenance columns on `catalogue_entry`.** Those columns would be null for every
  later entry, and "never editable" would need a trigger or a convention. A trigger is
  excluded (CLAUDE.md, PRD 14.1), and a convention is what ADR-0010 set out to avoid.
- **An idempotent or merging re-run.** Matching seeded entries to the dataset by key is
  unsafe when keys are positional, and matching by term turns a data correction into silent
  re-identification. Failing closed is simpler and cannot corrupt.
- **Seeding an uncoded specimen anyway.** Writing it as a property value with a made-up
  system, or dropping it with a log line, either bypasses the registry's validation or loses
  the workbook's text. Keeping it in a provenance column would store it where RCPA-QAP has no
  screen to code it. Refusing returns the fix to the transform's specimen table, where the
  mapping belongs.
- **A bulk insert for speed.** The catalogue holds about two thousand terms, so the real
  write paths cost seconds, and a bulk insert would skip the checks listed in decision 7.

## Consequences

- FR-76 and FR-60 stay open until P4 turns `seed_import` into a `Release`. P4 must read this
  table and the provenance rows when it does.
- A dataset that the loader refuses is a data task for RCPA-QAP and the transform, not a
  loader defect: map the specimen, resolve the collision or correct the workbook, run the
  transform again, then load.
- A reseed means resetting the database. `audit_event` is append-only (NFR-09, NFR-10), so no
  supported path clears a seeded catalogue in a database that real users have changed. The
  runbook says so.
- The loader depends on the `discipline` and `subgroup` local code systems that migration
  `0011` seeds.
