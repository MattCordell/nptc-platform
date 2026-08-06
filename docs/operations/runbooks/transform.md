# Transform CLI

`nptc-transform` converts the published SPIA Requesting workbook into either a
defect report or an import dataset (PRD §12). This runbook covers the `run`
command: the entrypoint, the report-only guarantee (FR-70) and the
determinism/idempotency contract (FR-73), delivered with backlog issue
[P0-1](https://github.com/MattCordell/nptc-platform/issues/23); the
workbook reader and PRD Appendix A.1-A.3 cell defect detection, delivered
with backlog issue [P0-2](https://github.com/MattCordell/nptc-platform/issues/24);
and the three-band defect classification engine, delivered with backlog issue
[P0-3](https://github.com/MattCordell/nptc-platform/issues/25). It does not
yet correct an auto-correctable finding, produce an import dataset, or
validate a code against a live terminology server - see
"Not implemented yet" below.

## Usage

```powershell
uv run nptc-transform run --workbook path/to/SPIA-Requesting.xlsx
```

| Flag | Default | Meaning |
|---|---|---|
| `--workbook` | *(required)* | Path to the source `.xlsx`. Must exist and be readable. |
| `--report-dir` | `transform-report` | Directory the report files are written into. Created if missing. Must be a directory path, not an existing file. |
| `--report-only` | on | Write a report and mutate nothing. This is the default; the flag exists so a script can state the mode explicitly. Mutually exclusive with `--emit-dataset`. |
| `--emit-dataset` | off | Opt into the mutating mode. **Not implemented yet** - see below. |

Running with no flags at all prints help and exits 0; `--workbook` is required
to actually run.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Ran to completion and no finding blocks the import - check `band_counts` in `report.json` for auto-correctable findings even so. |
| `1` | The report contains at least one finding banded `requires-human-decision` or `data-defect` (FR-71); the import must not proceed. Report-only mode still writes the report before exiting `1`. |
| `2` | Usage error: the workbook doesn't exist or isn't readable, the workbook isn't a valid `.xlsx` (corrupt zip or unparsable worksheet XML), `--report-dir` names an existing file or can't be written to, both mode flags were passed together, or `--emit-dataset` was passed. |

A filesystem refusal on `--report-dir` reports the path and the reason on
stderr and exits `2`. It never exits `1` - that code is reserved for
"the report contains blocking findings" - and never prints a traceback.

## The report-only guarantee (FR-70)

Report-only is the default and, until P0-9 lands, the *only* mode that writes
anything. A `run` invocation without `--emit-dataset` writes exactly two files
into `--report-dir` - `report.json` and `report.md` - and touches nothing else
on disk. `--emit-dataset` is reserved: passing it writes nothing at all,
prints an explanation to stderr, and exits `2`. This is deliberate, not a
missing feature - see "Not implemented yet".

## The determinism and idempotency contract (FR-73)

Running the transform twice against the same workbook, or re-running it into
the same `--report-dir`, produces byte-identical `report.json` and
`report.md` files. This holds because:

- **No clock-derived value appears in either report file.** The run start
  time, its duration, and the tool version banner are written to stderr only
  - never into `report.json` or `report.md`. If you need to know when a
  report was produced, check the file's modified time or the stderr log from
  the run that produced it.
- **No absolute path appears in either report file.** The report identifies
  the source workbook by filename and SHA-256 hash, not by the path it was
  read from.
- Every collection in the report is explicitly sorted before being written -
  finding order is never dict- or set-iteration order, and does not depend on
  `PYTHONHASHSEED`.
- Both files are written as UTF-8 with `\n` line endings, regardless of
  platform. Workbook-derived text in `report.md` is escaped before it reaches
  a table cell: a `|` in a finding's message is shown literally rather than
  splitting the row, and a line break becomes `<br>` rather than a raw CRLF.
- Both files are overwritten in place on every run - never appended to, never
  numbered (`report-2.json`). Re-running into a report directory that already
  holds a report from a previous run replaces it exactly; it does not
  accumulate findings or skip the write.

## What the reader detects (FR-70)

The workbook is read cell-by-cell, row 1 as the header row. A cell's original
storage type - text, number, date, boolean, formula or error - is captured
exactly as recorded; it is never coerced, because the type itself is often
the defect. Every worksheet column is mapped to a role (code, preferred
term, FSN, and so on) by its header text, using the published layout FR-63
documents (`RCPA Preferred term`, `Terminology binding (SNOMED CT-AU)`, ...).

A cell is scanned for PRD Appendix A.1-A.3, detection only - nothing is
corrected, and each defect is reported under one of two codes chosen by
*shape*, so that band classification (below) never has to inspect content:

| Finding code | Appendix | What it means |
|---|---|---|
| `INVISIBLE_CHARACTER` | A.1 | The cell's text contains a non-ASCII space (category `Zs`) - for example a non-breaking space (U+00A0) or narrow no-break space (U+202F). It collapses deterministically to an ordinary space with no loss of meaning. Every such character is invisible on screen and named by codepoint in the finding, never reproduced literally. |
| `INVISIBLE_CHARACTER_AMBIGUOUS` | A.1 | The cell's text contains a control, format, or line/paragraph separator character (a zero-width space, a bidi override, a stray line break, and so on) that has no single deterministic repair. An ALT+ENTER line break (U+000A, and U+000D for a Windows-origin paste) is exempted **only** in the `Usage guidance` and `History` columns - ordinary multi-line formatting there, but still a defect anywhere else (a preferred term, an FSN, a code cell), since a line break in those is never legitimate. |
| `SURROUNDING_WHITESPACE` | A.3 | The cell's text has leading and/or trailing whitespace, but stripping it leaves real content behind. |
| `WHITESPACE_ONLY_CELL` | A.3 | The cell's text is nothing *but* whitespace - stripping it would empty the cell entirely, which only a curator can confirm is the intended value. |
| `CODE_CELL_NOT_TEXT` | A.2 | The code column holds a numeric cell rather than text (FR-06). The digits are intact, so coercing the number to a string deterministically recovers the SCTID. |
| `CODE_CELL_INVALID_TYPE` | A.2 | The code column holds a date, boolean, formula or error cell rather than text. Unlike a number, there is no value to recover here - only a wrong one to report, so this is not treated the same as `CODE_CELL_NOT_TEXT`. |
| `NUMERIC_PRECISION_RISK` | A.2 | Any numeric-typed cell, in any column, holding an integer of 16 or more significant digits - the point past which Excel's own 15-significant-decimal-digit ceiling silently corrupts a long SCTID. (15 digits is exactly representable, so it is *not* flagged.) A cell whose raw value has already overflowed Excel's numeric range entirely (rare - a malformed numeric cell text openpyxl parses as `inf`) is flagged with a distinct message rather than a fabricated digit count. The digits are already gone by the time this fires, so there is nothing left to coerce. |
| `UNRECOGNISED_LAYOUT` | - | A sheet's header row doesn't resolve the code column - whether it resolves some other SPIA columns (genuine header drift) or none at all (for example, a banner row inserted above the real FR-63 headers). Reported once per sheet, naming every header actually found and how many data rows went unscanned as a result, rather than silently skipping A.2/A.3 detection on a drifted workbook. |
| `SHEET_NOT_SPIA_DATA` | - | A sheet named in FR-63's own documented non-SPIA-data list (currently just `Rev History`) resolves no SPIA column - it isn't SPIA data to begin with. Gated on the sheet's *name*, not merely on resolving zero columns: a genuine data sheet whose header row has drifted completely produces the identical "no column resolved" signal and must still be `UNRECOGNISED_LAYOUT`, not this. |

Either layout finding above means the sheet gets no further cell-level
scanning. A finding's `location` is a `Sheet!CellRef` reference (for example
`Requesting!H16`); both layout findings point at `Sheet!A1`, the header row.
A clean cell produces no finding at all.

**No generated report ever contains an invisible character itself** (NFR-38
test 2), even though every `INVISIBLE_CHARACTER`/`INVISIBLE_CHARACTER_AMBIGUOUS`
finding is about one: the character is always named by its `U+XXXX`
codepoint, never quoted verbatim - the same rule PRD Appendix A.1 applies to
itself.

## Band classification (FR-71)

Every finding is classified into exactly one band, which determines whether
the import can proceed. A finding's band is a pure function of its code
(`nptc_transform.bands.band_for`) - never of its content - so the table above
and this one together are the complete classification.

| Band | Blocks import | Codes | What it means |
|---|---|---|---|
| `auto-correctable` | No | `INVISIBLE_CHARACTER`, `SURROUNDING_WHITESPACE`, `CODE_CELL_NOT_TEXT` | The defect has one deterministic repair. **Not yet applied** - the report itemises it, but nothing is corrected on disk until P0-9's `--emit-dataset` lands. |
| `requires-human-decision` | Yes | `INVISIBLE_CHARACTER_AMBIGUOUS`, `WHITESPACE_ONLY_CELL` | No deterministic repair exists; a curator must decide the correct value. The import aborts until it's resolved. |
| `data-defect` | Yes | `CODE_CELL_INVALID_TYPE`, `NUMERIC_PRECISION_RISK`, `UNRECOGNISED_LAYOUT` | The source data itself is wrong or unrecoverable; RCPA-QAP must fix it at source. The import aborts until it's resolved. |
| `informational` | No | `SHEET_NOT_SPIA_DATA` | Not a defect at all - not one of FR-71's three bands, see [ADR-0004](../../adr/0004-informational-band-and-code-level-band-assignment.md). Reported so an operator can see a sheet was skipped, without treating it as something to fix. |

A run's exit code (above) is `1` if *any* finding blocks - a single
`requires-human-decision` or `data-defect` finding aborts the whole run, no
matter how many other findings are merely auto-correctable or informational.
`report.json`'s `band_counts` and `blocking` fields, and `report.md`'s band
summary table, report this without needing to open the full finding list.

An unrecognised finding code (there should never be one) fails safe to
`data-defect` rather than being silently treated as clean.

## Not implemented yet

The following are owned by later P0 issues (see the P0 milestone on GitHub
for the current issue numbers) and will change what `report.json`/`report.md`
contain, but not the guarantees above:

- **Applying the auto-correctable band's corrections** and emitting the import
  dataset, including the synthetic baseline release - P0-9
  (`--emit-dataset`)
- Batch terminology validation and hierarchy check - P0-5
- Designation reconciliation - P0-6
- Misspelling and semantic-drift heuristics - P0-7
- Report content grouped by defect class with cell references - P0-8
