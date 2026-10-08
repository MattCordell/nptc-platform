# Specimen map

`specimen_map.tsv` maps each specimen string in the RCPA workbook to a SNOMED CT code. The
transform reads it to code every specimen value, and it refuses any string the file does not
cover. ADR-0044 records the decision.

## Provenance

| Field | Value |
|---|---|
| Source file | `RCPA Specimen map_0.1.tsv`, version 0.1 |
| Prepared by | Matt Cordell (maintainer) |
| Reviewed by | The terminology reviewer (the "TA" in the project's records) |
| Terminology | SNOMED CT-AU, September 2026 release |
| Adopted | 2026-10-07 |
| Rows | 87: 70 equivalent, 12 inexact, 2 broader, 3 no map |

The committed file differs from the source file in its line endings (LF), in having no byte
order mark, and in one row. `Breath` was `123038009 |Specimen|` (broader) and is now marked no
map, because only `Any` may use the specimen root (see below). No other cell was changed.

## Columns

The transform reads `Source display`, `Target code`, `Target display`, `Relationship type code`,
`No map flag` and `Status`. It keeps `Source code` (an identifier from the mapping tool) and
`Relationship type display` for traceability, and ignores them.

- `Source display` is the string as it appears in the workbook. Matching trims it and ignores case.
- A row marked `true` in `No map flag` has no target. It names a test that needs no specimen, or
  a specimen that SNOMED CT has no concept for yet.
- Only `Any` may target `123038009`. Every rule downstream reads that code as "any specimen", so
  the loader refuses any other row that uses it.
- Two strings may share a target. The workbook spells some specimens in more than one way.
- `Target display` is the concept's FSN with its tag, kept for the reviewer. The transform does not
  use it. The import dataset's specimen display is the code's SNOMED CT-AU preferred term, read from
  the terminology server under `--check-terminology`.

## Known follow-ups

Two rows are placeholders until SNOMED CT has a more precise concept:

- `Breath` is marked no map. The reviewed target was the root `123038009 |Specimen|`, which only
  `Any` may use. It needs a new concept through SCTAU.
- `Body` maps to `371784004`, an inexact match. A new concept is scheduled for the October release.

Replace both rows before the production release.

## Changing the map

1. Edit the TSV. Keep one row for each string, spelled as it appears in the workbook.
2. Update the provenance table above.
3. Run `uv run pytest transform`, and run the transform with `--check-terminology`. The check
   refuses any code that is not under `<<123038009`.
