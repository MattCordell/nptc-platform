# Transform CLI

`nptc-transform` converts the published SPIA Requesting workbook into either a
defect report or an import dataset (PRD §12). This runbook covers the `run`
command: the entrypoint, the report-only guarantee (FR-70) and the
determinism/idempotency contract (FR-73), delivered with backlog issue
[P0-1](https://github.com/MattCordell/nptc-platform/issues/23), and the
workbook reader and PRD Appendix A.1-A.3 cell defect detection, delivered
with backlog issue [P0-2](https://github.com/MattCordell/nptc-platform/issues/24).
It does not yet classify a finding into a severity band - see
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
| `0` | Ran to completion. In report-only mode, this does not mean the workbook is clean - check `finding_count` in `report.json`. |
| `1` | Reserved for blocking findings once band classification (P0-3) lands. Unreachable today. |
| `2` | Usage error: the workbook doesn't exist or isn't readable, the workbook isn't a valid `.xlsx` (corrupt zip or unparsable worksheet XML), `--report-dir` names an existing file or can't be written to, both mode flags were passed together, or `--emit-dataset` was passed. |

A filesystem refusal on `--report-dir` reports the path and the reason on
stderr and exits `2`. It never exits `1` - that code stays reserved for
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
corrected and no severity band is assigned (that's P0-3):

| Finding code | Appendix | What it means |
|---|---|---|
| `INVISIBLE_CHARACTER` | A.1 | The cell's text contains a control, format, line/paragraph separator, or non-ASCII space character - for example a non-breaking space (U+00A0) or narrow no-break space (U+202F). Every such character is invisible on screen and named by codepoint in the finding, never reproduced literally. An ALT+ENTER line break (U+000A, and U+000D for a Windows-origin paste) is exempted **only** in the `Usage guidance` and `History` columns - ordinary multi-line formatting there, but still a defect anywhere else (a preferred term, an FSN, a code cell), since a line break in those is never legitimate. |
| `SURROUNDING_WHITESPACE` | A.3 | The cell's text has leading and/or trailing whitespace. A cell that's nothing *but* whitespace is reported as "contains only whitespace" rather than a leading-and-trailing message that implies there's content between the two edges. |
| `CODE_CELL_NOT_TEXT` | A.2 | The code column holds a cell that isn't stored as text (FR-06). |
| `NUMERIC_PRECISION_RISK` | A.2 | Any numeric-typed cell, in any column, holding an integer of 16 or more significant digits - the point past which Excel's own 15-significant-decimal-digit ceiling silently corrupts a long SCTID. (15 digits is exactly representable, so it is *not* flagged.) A cell whose raw value has already overflowed Excel's numeric range entirely (rare - a malformed numeric cell text openpyxl parses as `inf`) is flagged with a distinct message rather than a fabricated digit count. |
| `UNRECOGNISED_LAYOUT` | - | A sheet has data rows but no column recognised as the code column. Reported once per sheet, naming every header actually found, rather than silently skipping A.2 detection on a drifted workbook. Such a sheet gets no further cell-level scanning - the published workbook's own `Rev History` worksheet (FR-63, FR-60) is exactly this case: hand-written prose with no SPIA columns at all, not a sheet whose whitespace and line breaks are worth reporting on. |

A finding's `location` is a `Sheet!CellRef` reference (for example
`Requesting!H16`); `UNRECOGNISED_LAYOUT` points at `Sheet!A1`, the header row.
A clean cell produces no finding at all.

**No generated report ever contains an invisible character itself** (NFR-38
test 2), even though every `INVISIBLE_CHARACTER` finding is about one: the
character is always named by its `U+XXXX` codepoint, never quoted verbatim -
the same rule PRD Appendix A.1 applies to itself.

## Not implemented yet

The following are owned by later P0 issues (see the P0 milestone on GitHub
for the current issue numbers) and will change what `report.json`/`report.md`
contain, but not the guarantees above:

- Three-band defect classification - P0-3
- Terminology client and batch validation - P0-4, P0-5
- Designation reconciliation - P0-6
- Misspelling and semantic-drift heuristics - P0-7
- Report content grouped by defect class with cell references - P0-8
- **`--emit-dataset`: import dataset emission and the synthetic baseline release** - P0-9
- SCTID/Verhoeff library - P0-10
