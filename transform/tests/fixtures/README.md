# Test fixtures

## `spia-requesting-sample.xlsx`

A verbatim, unedited excerpt of the published SPIA Requesting workbook,
`RCPA SPIA Requesting Pathology RS_Jun 2026 - Snippet.xlsx`, as published by
RCPA-QAP. Renamed lower-case and hyphenated for this repository - spaces in a
test-fixture path are a cross-platform hazard - but its contents are not
touched: no cell has been added, removed or edited.

- **Source:** `RCPA SPIA Requesting Pathology RS_Jun 2026 - Snippet.xlsx`
- **Publication date:** June 2026
- **Row count:** 50 data rows, matching the PRD's own planning scale
  ("the supplied sample is 50 rows", PRD:227)
- **Provenance:** RCPA-QAP is aware this excerpt is used, in this form, for
  the purpose of building the platform that maintains the SPIA Requesting
  terminology (see `NOTICE`). It is a genuine excerpt of the real published
  data, not synthetic content - FR-76's "synthetic" qualifies the *release*
  a seeding run produces from it, never the data itself.
- **Do not hand-edit this file.** Its entire value as a fixture is being a
  verbatim, real-world sample; editing it defeats that purpose as surely as
  deleting it would. If a test needs a specific defect shape this excerpt
  doesn't happen to contain, build a small in-process `.xlsx` for that test
  instead (see `conftest.py`'s own fixtures) - do not "improve" this file.

`.pre-commit-config.yaml` excludes this directory from `trailing-whitespace`
deliberately: some fixtures under `transform/tests/fixtures/` encode the very
defect being tested, and normalising them would silently break that test.
`.gitattributes` marks `*.xlsx` as `binary`, so it is never subject to line-
ending normalisation either.
