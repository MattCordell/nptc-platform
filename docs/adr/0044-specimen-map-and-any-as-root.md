# ADR-0044: Specimen strings map to SNOMED CT codes through a reviewed data file, and `Any` is the specimen root

**Status:** Accepted
**Date:** 2026-10-07

Supersedes decision 3 of [ADR-0008](0008-specimen-inspection-strategy.md) (a hand-typed Python
table) and its "YAML/JSON specimen table" rejected alternative. It replaces the audit count in
ADR-0008's decision 4, because a string the map does not cover now blocks the run. It also
supersedes the FR-89 model that closed OI-2 (zero specimen values plus a
`specimen_unconstrained` flag). The rest of ADR-0008 stands: the ECL checks and the informational
drift bands.

## Context

The transform and the seed loader disagreed about what can be seeded. The transform matched
a workbook specimen string against surface forms in `specimen_table.py` and reported a miss as
`SPECIMEN_VALUE_UNMAPPED`, an informational finding, so it exited `0`. The loader refuses any
specimen without a SNOMED CT code (ADR-0042) and exited `3`. On the sample workbook, ten
values were unmapped, among them `Amniotic fluid`, `Platelet poor plasma`, `Blood`, `Skin`,
`Swabs`, `Fluids` and `Red cells`.

A terminologist-reviewed map now covers every string in the workbook: 87 rows, prepared by the
maintainer against the September 2026 SNOMED CT-AU release. The relationship column reads 70
equivalent, 12 inexact, 3 broader and 2 no map (`N/A` and `Culture`).

The map conflicts with the model in PRD 6.2 and 6.6 in two places:

- `Any` maps to `123038009 |Specimen|`. FR-89 forbade a code for `Any`.
- `Breath` maps to the same root, as a broader concept, until SCTAU adds a Breath concept.
  The binding `<123038009` selects descendants only, so it refuses the root.

`Body` maps to `371784004`, which the maintainer confirmed is a subtype of `123038009`. A
more precise concept is due in the October release.

## Decision

**1. The reviewed map is a data file, loaded and validated by the transform.** It is the one
source of specimen vocabulary. Provenance (who prepared it, against which release, which file
version) is recorded beside it. Loading refuses a malformed row, a code that is not a valid
SCTID, and two rows that differ only by case or whitespace but disagree on the code.

**2. Every workbook specimen string must be in the map, and a string that is not is a blocking
data defect.** Matching trims whitespace and ignores case. Nothing is matched by prefix, by
substring or by similarity. The transform now fails where the loader would, so the exit code of
the transform predicts the loader.

**3. `Any` is coded as `123038009`, and the specimen binding widens to `<<123038009`.** The
`forbidden_codes` constraint on `specimen` is dropped. An entry that accepts any specimen
holds `123038009` alone. The transform, the seed loader and the write API each refuse the root
beside another specimen, so `Any; Serum` is a defect to fix at source.

**4. `specimen_unconstrained` is retired.** The column, its field in list, search, detail and
write responses, its write route and its frontend toggle go. A migration first converts each `true` row into a `123038009`
specimen value, with audit events (NFR-08), then drops the column. An entry with no specimen
value now means only "not yet filled in". That is the ambiguity the flag existed to remove, and
the root value removes it.

**5. A no-map row (`N/A`, `Culture`) yields zero specimen values and an informational
finding.** It never means "accepts any specimen".

**6. Each FR-75 drift group takes its code and display from the map.** `SPECIMEN_TABLE`
keeps its preferred-term wording in Python, because the map holds workbook cell strings and not
the phrases a curator writes inside a preferred term, such as "stool" or "cerebrospinal fluid".
A corrected code in the map reaches the drift check without a second edit. Group keys and the
timing assertion for the timed urine groups stay in Python, because the map has neither. Two
groups, whole blood and breast milk, have no row in the map and keep their own verified codes. A
test fails when the map gains a row for either, so the group moves over.

**7. `--check-terminology` verifies every map code is under `<<123038009`.** A code that fails
blocks the run. This reuses the existing sweep primitives, with no request per row.

**8. The dataset `schema_version` moves from 1 to 2, and `report.json`'s moves with it.** The
dataset loses a field, and a strict reader would otherwise report an old file as a list of
unrelated shape errors. The version gate gives one clear message instead (ADR-0010).

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Keep the flag and keep `<123038009`, and refuse `Any` and `Breath` | The reviewed map codes both as the root. Every entry that says `Any` would stay unseedable, which is the gap this change closes. |
| Keep the flag, and map `Any` to zero values in the transform only | The map would hold a row that the transform then ignores. The flag also needs a core column, a field on every entry response, a toggle and a two-sided guard, all to carry what one code can carry. |
| Keep the map in a Python module (ADR-0008, decision 3) | The reviewer works on rows, not on code. A Python table makes each correction a code change that the reviewer cannot diff as data. The load-time checks answer the drift concern ADR-0008 raised. |
| Derive every drift group, wording included, from the map | The map holds workbook cell strings. Short ones such as `DNA`, `Blood` and `Hair` would match inside many preferred terms and raise a flood of drift candidates, and the phrases a curator writes in a preferred term ("stool") are not in it. |
| Match by prefix, substring or similarity | A wrong code seeded silently is worse than a row that blocks. The workbook is the authority, and the map must name its strings. |
| Read a no-map row as "accepts any specimen" | `N/A` and `Culture` do not say that. The meaning of the root would then rest on guesses about free text. |
| Leave an unmapped string informational | The loader refuses it regardless, so the transform would keep exiting `0` for a dataset that cannot load. |

## Consequences

- **Two workbook strings share a code.** `Any` and `Breath` both seed as `123038009`, and the
  original string is not kept, so a reader cannot tell them apart. A follow-up replaces the
  `Breath` and `Body` rows when the new concepts exist. It must land before production release.
- FR-88 and FR-89 are reworded, the `specimen_unconstrained` row leaves PRD 6.2, and OI-2's
  outcome is revised. ADR-0008's status line points here.
- A database that already holds the old binding is not changed by `seed_system_properties`, which
  only inserts missing rows. A migration updates the `specimen` definition.
- Exports render the root as `Any` for continuity with the published format (FR-89 said the same
  of the flag). The export layer does not exist yet, so that rendering lands with it.
- The reviewed map is validated at load, and its codes are checked against SNOMED CT-AU on
  every `--check-terminology` run, so a map that the terminology has moved past is reported.
