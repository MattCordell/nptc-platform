# Transform CLI

`nptc-transform` converts the published SPIA Requesting workbook into either a
defect report or an import dataset (PRD §12). This runbook covers the `run`
command: the entrypoint, the report-only guarantee (FR-70) and the
determinism/idempotency contract (FR-73), delivered with backlog issue
[P0-1](https://github.com/MattCordell/nptc-platform/issues/23); the
workbook reader and PRD Appendix A.1-A.3 cell defect detection, delivered
with backlog issue [P0-2](https://github.com/MattCordell/nptc-platform/issues/24);
the three-band defect classification engine, delivered with backlog issue
[P0-3](https://github.com/MattCordell/nptc-platform/issues/25); batch
terminology validation with the hierarchy check, delivered with backlog issue
[P0-5](https://github.com/MattCordell/nptc-platform/issues/27); designation
reconciliation, delivered with backlog issue
[P0-6](https://github.com/MattCordell/nptc-platform/issues/28); and the FR-79
misspelling heuristics and the FR-75 semantic-drift review, both delivered
with backlog issue
[P0-7](https://github.com/MattCordell/nptc-platform/issues/29); and the
grouped, actionable defect report, delivered with backlog issue
[P0-8](https://github.com/MattCordell/nptc-platform/issues/30). It does not
yet correct an auto-correctable finding or produce an import dataset - see
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
| `--check-terminology` | off | Validate every code binding against SNOMED CT-AU and International (FR-52, FR-74, FR-84, FR-99), reconcile every published label against its bound concept's designation set (FR-97), give the FR-79 misspelling heuristics an authority whitelist built from the served designations (see "Interpreting a misspelling finding" below), and run the FR-75 semantic-drift review of specimen/timing wording (see "Interpreting a semantic-drift finding" below). **The only part of the run that uses the network**; reads `NPTC_TX_*` (see [configuration](../configuration.md)). |

Running with no flags at all prints help and exits 0; `--workbook` is required
to actually run.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Ran to completion and no finding blocks the import - check `band_counts` in `report.json` for auto-correctable findings even so. |
| `1` | The report contains at least one finding banded `requires-human-decision` or `data-defect` (FR-71); the import must not proceed. Report-only mode still writes the report before exiting `1`. |
| `2` | Usage error: the workbook doesn't exist or isn't readable, the workbook isn't a valid `.xlsx` (corrupt zip or unparsable worksheet XML), `--report-dir` names an existing file or can't be written to, both mode flags were passed together, `--emit-dataset` was passed, or (with `--check-terminology`) an `NPTC_TX_*` environment variable is malformed (e.g. `NPTC_TX_CHUNK_SIZE` not a positive integer) - a deployment typo, not a server outage, so it lands here rather than exit `3`. |
| `3` | `--check-terminology` was passed and the terminology sweep could not complete (server unreachable, rate limited past the retry budget, a malformed response). **No report is written at all** - a report with the cell defects complete and the terminology findings silently missing would look exactly like a run in which every code validated cleanly (FR-54). Re-run without `--check-terminology` for the cell-defect report alone. |

A filesystem refusal on `--report-dir` reports the path and the reason on
stderr and exits `2`. It never exits `1` - that code is reserved for
"the report contains blocking findings" - and never prints a traceback.
The workbook is always read - and any `WorkbookReadError` reported - before any
`NPTC_TX_*` value is even parsed, so a corrupt workbook's message is never
pre-empted by an unrelated configuration error.

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
| `CODE_NOT_WELL_FORMED` | - | The code cell's text (after stripping whitespace) is not a well-formed SCTID: not 6-18 digits, or the Verhoeff check digit fails (FR-06). Only raised when `--check-terminology` is passed, and the code is **not** submitted to the server - a malformed value reported as "not found" would read as a terminology outcome rather than as the transcription defect it is. |
| `CODE_NOT_FOUND` | - | The code resolves in **no** validated edition (FR-71). A code present in SNOMED CT-AU but absent from International is *not* this - that is what Australian extension content looks like (FR-47) and produces no finding. |
| `CODE_INACTIVE` | - | The concept is inactive in every edition that has it. Inactive in International while still active in AU is a *forecast*, not a current error (FR-47), and is deliberately not reported here - it belongs to the scheduled validation sweep, not to a seeding run. |
| `OUT_OF_SCOPE_HIERARCHY` | - | The concept is not subsumed by `<<71388002` \|Procedure (procedure)\| (FR-84). See "Interpreting hierarchy violations" below. |
| `UNEXPECTED_SEMANTIC_TAG` | - | The concept *is* subsumed by `<<71388002` but its FSN's semantic tag is not `(procedure)` (FR-99). A warning, not an error - see below. |
| `LABEL_DESIGNATION_DRIFT` | - | The workbook's `SNOMED CT Fully Specified Name` column value isn't the concept's tag-stripped FSN, but it matches another active designation on the *same* concept - a valid synonym, or an FSN before it changed (FR-97). Informational: the served FSN is what would be seeded, not the stored value. |
| `LABEL_BOUND_TO_OTHER_CONCEPT` | - | The column value matches no designation of the bound concept, but does match a designation of a *different* code bound elsewhere in the workbook (FR-97). Likely a transcription error pairing the wrong code with the right label, or the reverse - see "Interpreting a designation finding" below. |
| `LABEL_MATCHES_NO_DESIGNATION` | - | The column value matches no designation on the bound concept, and no other bound concept's designation either (FR-97). The label is wrong, or was never a SNOMED designation at all. |
| `LABEL_DIFFERS_FROM_PREFERRED_TERM` | - | The current SNOMED CT-AU preferred term differs from the column value, independently of the above (FR-97, FR-82) - informational, and reported even on a row with no other designation finding at all. |
| `PROBABLE_MISSPELLING` | - | A preferred-term/synonym token differs by one or two characters from another designation in the *same* entry, or from the served FSN/designations of the concept that entry's code binds to (FR-79, H-04) - informational, never auto-corrected. See "Interpreting a misspelling finding" below. |
| `INCONSISTENT_SPELLING` | - | A preferred-term/synonym token used in only one or two entries differs by one or two characters from a spelling used in many more, across the whole workbook (FR-79, H-04) - informational, never auto-corrected. |
| `TERM_SPECIMEN_NOT_MODELLED` | - | The RCPA preferred term asserts a specimen (e.g. "urine", "CSF") the bound concept constrains no `Has specimen` (116686009) value for at all (FR-75, H-03) - informational, a candidate for editorial review. See "Interpreting a semantic-drift finding" below. |
| `TERM_SPECIMEN_DIFFERS` | - | The RCPA preferred term asserts a specimen, and the bound concept *does* constrain a `Has specimen` value, but not one subsumed by the asserted specimen's root (FR-75, H-03) - informational. |
| `TERM_TIMING_NOT_MODELLED` | - | The RCPA preferred term asserts a timing (e.g. "24 hour") that appears in neither the bound concept's own served designations nor its asserted specimen concept's - only reported when the specimen aspect itself is not asserted or already agrees (FR-75, H-03) - informational. |
| `UNRECOGNISED_LAYOUT` | - | A sheet's header row doesn't resolve the code column - whether it resolves some other SPIA columns (genuine header drift) or none at all (for example, a banner row inserted above the real FR-63 headers). Reported once per sheet, naming every header actually found and how many data rows went unscanned as a result, rather than silently skipping A.2/A.3 detection on a drifted workbook. |
| `SHEET_NOT_SPIA_DATA` | - | A sheet named in FR-63's own documented non-SPIA-data list (currently just `Rev History`) resolves no SPIA column - it isn't SPIA data to begin with. Gated on the sheet's *name*, not merely on resolving zero columns: a genuine data sheet whose header row has drifted completely produces the identical "no column resolved" signal and must still be `UNRECOGNISED_LAYOUT`, not this. |

Either layout finding above means the sheet gets no further cell-level
scanning. A finding's `location` is a structured `CellRef` (sheet name,
column letters, row number - `nptc_transform.cellref`), not a plain string:
rendered, it reads as `Sheet!CellRef` (for example `Requesting!H16`), but a
consumer that needs the parts (the report renderer, a future export) reads
them off the value directly rather than re-splitting the rendered string -
a sheet name can itself contain `!`, which would make that split ambiguous.
Both layout findings point at `Sheet!A1`, the header row. A clean cell
produces no finding at all.

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
| `data-defect` | Yes | `CODE_CELL_INVALID_TYPE`, `NUMERIC_PRECISION_RISK`, `UNRECOGNISED_LAYOUT`, `CODE_NOT_WELL_FORMED`, `CODE_NOT_FOUND`, `CODE_INACTIVE`, `OUT_OF_SCOPE_HIERARCHY`, `LABEL_BOUND_TO_OTHER_CONCEPT`, `LABEL_MATCHES_NO_DESIGNATION` | The source data itself is wrong or unrecoverable; RCPA-QAP must fix it at source. The import aborts until it's resolved. |
| `informational` | No | `SHEET_NOT_SPIA_DATA`, `UNEXPECTED_SEMANTIC_TAG`, `LABEL_DESIGNATION_DRIFT`, `LABEL_DIFFERS_FROM_PREFERRED_TERM`, `PROBABLE_MISSPELLING`, `INCONSISTENT_SPELLING`, `TERM_SPECIMEN_NOT_MODELLED`, `TERM_SPECIMEN_DIFFERS`, `TERM_TIMING_NOT_MODELLED` | Not a defect at all - not one of FR-71's three bands, see [ADR-0004](../../adr/0004-informational-band-and-code-level-band-assignment.md). Reported so an operator can see it, without treating it as something to fix. |

A run's exit code (above) is `1` if *any* finding blocks - a single
`requires-human-decision` or `data-defect` finding aborts the whole run, no
matter how many other findings are merely auto-correctable or informational.
`report.json`'s `band_counts` and `blocking` fields, and `report.md`'s band
summary table, report this without needing to open the full finding list.

An unrecognised finding code (there should never be one) fails safe to
`data-defect` rather than being silently treated as clean.

## The report files (FR-72)

`report.json` and `report.md` are the only two output files - no CSV.
FR-72 asks for three things: the report must be machine-readable, must be
human-readable, and the human-readable form must be organised by defect
class, cite exact cell references, and state the required action. Both
files satisfy all three from the same grouped data
(`report_writer._group_findings`); neither is derived from the other.

### `report.json` (`schema_version` 7)

Findings are grouped by `code` into a `defect_classes` array - the flat,
per-finding `findings` list schema 6 had is gone; nothing outside
`transform/` reads `report.json`, so there was no reason to keep both and
let the two copies drift.

```json
"defect_classes": [
  {
    "band": "requires-human-decision",
    "blocks_import": true,
    "code": "WHITESPACE_ONLY_CELL",
    "action": "Confirm whether the cell is meant to be empty ...",
    "finding_count": 2,
    "findings": [
      {
        "location": {"sheet": "Requesting", "column": "B", "row": 12, "ref": "Requesting!B12"},
        "message": "the cell contains only whitespace (U+00A0 x2)"
      }
    ]
  }
]
```

- `band`, `code` and `action` are stated once per group, not once per
  finding - that is what "organised by defect class" means structurally.
- `blocks_import` is denormalised deliberately onto the group: a consumer
  must never re-derive it from the band string itself.
- `location` carries the resolvable parts (`sheet`/`column`/`row`) plus
  `ref`, the same rendered string `report.md` shows, as a convenience -
  never the only form, since `Sheet!Q1!B12` is ambiguous for a sheet named
  `Sales!Q1` in a way the three separate fields never are.
- Group order is explicit: blocking bands first
  (`nptc_transform.bands.BAND_REPORT_ORDER`), then each band's codes in
  their declared `FindingCode` order, with an unregistered code sorting
  last rather than raising. `json.dumps(sort_keys=True)` only sorts object
  *keys*, never array elements, so this order is never left to chance.
  Findings within a group keep the report's own canonical order for free.

### `report.md`

```markdown
### requires-human-decision - blocks import

#### `WHITESPACE_ONLY_CELL` - 2 finding(s)

**Required action:** Confirm whether the cell is meant to be empty or to
hold a value, and set it explicitly. The transform will not decide on your
behalf that whitespace means empty. The import is blocked until the cell is
corrected at source.

| Cell | Detail |
|---|---|
| `Requesting!B12` | the cell contains only whitespace (U+00A0 x2) |
```

Blocking bands first, in the same `BAND_REPORT_ORDER` the JSON groups use -
one presentation order for the whole artefact. A defect class with zero
findings is omitted entirely (the band-count table above already states
the zero, so there is no "not run vs found nothing" ambiguity for an empty
section to guard against); a report with zero findings overall still emits
the `## Findings by defect class` heading, followed by `No findings.`, so
anchor links into the section stay stable either way. The `| Cell | Detail
|` table carries only the cell and the message - the code and band are the
enclosing headings, and the required action is its own paragraph above the
table, so neither is repeated once per row the way schema 6's flat table did.

Every `FindingCode` this transform can emit, its band and its required
action:

| Defect class | Band | Required action |
|---|---|---|
| `INVISIBLE_CHARACTER` | auto-correctable | No action required. The transform will normalise this invisible character to an ordinary space automatically once dataset emission (P0-9) lands. The import is not blocked. |
| `SURROUNDING_WHITESPACE` | auto-correctable | No action required. The transform will strip the leading and/or trailing whitespace automatically once dataset emission (P0-9) lands. The import is not blocked. |
| `CODE_CELL_NOT_TEXT` | auto-correctable | No action required. The transform will coerce this code cell to text, recovering the SCTID's digits exactly, automatically once dataset emission (P0-9) lands. The import is not blocked. |
| `INVISIBLE_CHARACTER_AMBIGUOUS` | requires-human-decision | RCPA-QAP must open the cell and decide the correct value: this character has no deterministic repair. The import is blocked until the cell is corrected at source. |
| `WHITESPACE_ONLY_CELL` | requires-human-decision | Confirm whether the cell is meant to be empty or to hold a value, and set it explicitly. The transform will not decide on your behalf that whitespace means empty. The import is blocked until the cell is corrected at source. |
| `CODE_CELL_INVALID_TYPE` | data-defect | RCPA-QAP must retype this cell as text holding the correct SCTID at source; no coercion exists to recover a valid code from a date, boolean, formula or error cell. The import is blocked until it is corrected. |
| `NUMERIC_PRECISION_RISK` | data-defect | RCPA-QAP must re-enter this cell as text holding the correct, full-precision SCTID at source; Excel has already corrupted the stored digits. The import is blocked until it is corrected. |
| `UNRECOGNISED_LAYOUT` | data-defect | RCPA-QAP must restore this sheet's published header row so the transform can find the code column; every row on this sheet went unscanned as a result. The import is blocked until the layout is corrected. |
| `CODE_NOT_WELL_FORMED` | data-defect | RCPA-QAP must correct this cell to a well-formed 6-18 digit SCTID with a valid Verhoeff check digit at source. The import is blocked until it is corrected. |
| `CODE_NOT_FOUND` | data-defect | RCPA-QAP must rebind this cell to a code that resolves in at least one validated edition, or correct the transcription error. The import is blocked until it is corrected. |
| `CODE_INACTIVE` | data-defect | RCPA-QAP must rebind this cell to an active concept; an inactive concept must not be published as a binding. The import is blocked until it is corrected. |
| `OUT_OF_SCOPE_HIERARCHY` | data-defect | RCPA-QAP must rebind this cell to a concept subsumed by 71388002 (Procedure), or document why the exception is justified (FR-84). The import is blocked until it is resolved. |
| `LABEL_BOUND_TO_OTHER_CONCEPT` | data-defect | RCPA-QAP must check both the code and the label against each other: one is a transcription error pairing the wrong code with the right label, or the reverse (FR-97). The import is blocked until it is corrected at source. |
| `LABEL_MATCHES_NO_DESIGNATION` | data-defect | RCPA-QAP must correct the published label at source; it matches no designation of the bound code, or of any other code bound elsewhere in this workbook (FR-97). The import is blocked until it is corrected. |
| `SHEET_NOT_SPIA_DATA` | informational | No action required. This sheet is recognised as prose, not SPIA data, and was not scanned. The import is not blocked. |
| `UNEXPECTED_SEMANTIC_TAG` | informational | No action required. Subsumption does not imply the tag (FR-99); review the served FSN in context if the tag is unexpected. The import is not blocked. |
| `LABEL_DESIGNATION_DRIFT` | informational | No action required. The served FSN will be seeded in place of the stored label; review only if the drift is unexpected (FR-97). The import is not blocked. |
| `LABEL_DIFFERS_FROM_PREFERRED_TERM` | informational | No action required. The current SNOMED CT-AU preferred term differs from the published label; review only if the drift is unexpected (FR-97, FR-82). The import is not blocked. |
| `PROBABLE_MISSPELLING` | informational | No action required to proceed. A terminologist should review the flagged token against the cited in-entry reference and correct it manually if it is genuinely a misspelling; the transform never auto-corrects it (FR-79). The import is not blocked. |
| `INCONSISTENT_SPELLING` | informational | No action required to proceed. A terminologist should review the flagged token against the cited corpus-common spelling and correct it manually if it is genuinely inconsistent; the transform never auto-corrects it (FR-79). The import is not blocked. |
| `TERM_SPECIMEN_NOT_MODELLED` | informational | No action required to proceed. A terminologist should review whether the bound concept ought to model the asserted specimen (FR-75); this is a candidate for editorial review, not a confirmed defect. The import is not blocked. |
| `TERM_SPECIMEN_DIFFERS` | informational | No action required to proceed. A terminologist should review whether the bound concept's modelled specimen agrees with the one asserted by the term (FR-75); this is a candidate for editorial review, not a confirmed defect. The import is not blocked. |
| `TERM_TIMING_NOT_MODELLED` | informational | No action required to proceed. A terminologist should review whether the asserted timing is genuinely unmodelled (FR-75); this is a candidate for editorial review, not a confirmed defect. The import is not blocked. |

This table is generated by hand from `nptc_transform.actions.ACTION_BY_CODE`
and is kept in sync with it by a dedicated test
(`transform/tests/test_actions.py::test_every_action_matches_the_runbooks_table`)
- if you change one, change the other in the same PR.

## Terminology validation (`--check-terminology`)

Off by default. With the flag, every code cell in the workbook is validated
against **both** SNOMED CT-AU and International (FR-74), at the latest release
of each - no version is pinned, and the version the server reports it resolved
against is recorded in the report (FR-48).

### What it costs in requests

The shape is fixed by FR-52 and is the reason this pass is usable at
catalogue scale at all:

1. **Bulk status.** `ceil(N / NPTC_TX_CHUNK_SIZE)` `ValueSet/$expand` calls per
   edition over an ECL enumerating each chunk, with `activeOnly=true`. Codes
   are de-duplicated first, so a concept bound by twenty rows costs one slot.
2. **The delta only.** One `CodeSystem/$lookup` for each code the bulk pass did
   not return - that is what separates "inactive" from "not in this edition" -
   at most `NPTC_TX_MAX_CONCURRENCY` at a time.
3. **One** `$expand` of `(codes) MINUS <<71388002` per edition for the whole
   hierarchy check (FR-84).
4. **One `CodeSystem/$validate-code` per label the first three steps' own
   designation data couldn't already settle** (FR-97) - bounded by how many
   published labels matched nothing locally, never by row count. On the PRD's
   50-row sample this is 1 request, not 50: `report.json`'s `designations`
   block prints the exact count (`label_confirmations`) so this stays
   auditable rather than assumed.
5. **The FR-75 semantic-drift review (H-03) costs `2 + G` further `$expand`-
   style calls, never one per row and never one per code.** One call resolves
   the whole specimen table's own vocabulary (`describe`); one classifies
   which asserting codes constrain no `Has specimen` value at all
   (`codes_without_attribute`); one more *per distinct specimen group still
   asserted after the visibility filter* (`codes_with_attribute_value`,
   `G` in the count above) - `G` is bounded by the size of the specimen
   table (16 groups), never by the catalogue. A row whose own served
   designations (or its asserted specimen concept's) already carry the
   wording it asserts needs no classification call at all - see the worked
   examples below.

There is never one request per code per edition. A 429 is retried honouring
`Retry-After` with exponential backoff, by the shared client (see the
[terminology client architecture doc](../../architecture/terminology-client.md));
if it persists past the retry budget the run exits `3` and writes nothing.

### Interpreting hierarchy violations (`OUT_OF_SCOPE_HIERARCHY`, FR-84)

The check expands `(every code) MINUS <<71388002` and reports whatever comes
back. A code in that result is one that **exists in the edition** and is
**not** a descendant of `71388002` \|Procedure (procedure)\|. A code that does
not exist in the edition cannot appear here - an ECL enumerating codes only
returns concepts that exist - so absence is always reported as `CODE_NOT_FOUND`
and never as a hierarchy violation.

What to do with one, in order:

1. **Look the code up.** The usual cause is a binding to a concept in the wrong
   hierarchy - an observable entity, a substance, a specimen, or a finding -
   which reads as plausible in the spreadsheet because the label is right and
   only the code is wrong.
2. **Rebind, or justify the exception.** FR-84 gives exactly these two
   remedies. There is no "acknowledge and continue" in the transform: the
   finding is banded `data-defect` and the run exits `1` until the source is
   corrected.
3. **Check the message's edition list.** A violation reported for `int` but not
   `au` means the concept sits under Procedure in the AU edition only, which is
   worth a terminologist's eye rather than an immediate rebinding.

Do **not** treat `UNEXPECTED_SEMANTIC_TAG` as a weaker form of the same thing.
Subsumption does not imply the tag: `71388002` \|Procedure\| subsumes
`243120004` \|Regime/therapy (regime/therapy)\|, so a structurally valid
procedure binding can carry a different tag (PRD Appendix A.10). FR-99 makes
that a warning precisely so it cannot abort a seeding run, and it is reported
once per cell, informationally, alongside the served FSN so the tag can be
judged in context.

### Interpreting a designation finding (FR-97)

The workbook's `SNOMED CT Fully Specified Name` column, despite its header,
holds neither FSNs nor preferred terms consistently - it is free text RCPA-QAP
typed over more than a decade. Each row's value is classified against the
bound concept's whole designation set:

| Outcome | Finding | Action |
|---|---|---|
| Matches the tag-stripped FSN | *(none - seeded silently)* | Nothing to do. |
| Matches another active designation on the concept | `LABEL_DESIGNATION_DRIFT` | Nothing required; the served FSN is seeded, not the stored value. Review if the drift is unexpected. |
| Matches a designation of a **different** bound concept | `LABEL_BOUND_TO_OTHER_CONCEPT` | Check both rows: the code or the label transcribed the wrong concept. This is the outcome most worth a careful look, because both halves can look individually correct. |
| Matches no designation anywhere in the workbook | `LABEL_MATCHES_NO_DESIGNATION` | The label is wrong, or was never a SNOMED designation. Correct it at source. |

**"Matches a designation of a different concept" is workbook-scoped, not
catalogue-wide.** There is no reverse designation search in the FR-53 client
contract, so this checks only against concepts *this workbook binds
somewhere*. A label that happens to belong to some other SNOMED concept
entirely reads as `LABEL_MATCHES_NO_DESIGNATION`, not
`LABEL_BOUND_TO_OTHER_CONCEPT` - both block, so this only narrows *why*, never
*whether*.

**The server probe can only make an outcome more benign, never less.** A
label matching nothing in the designations the bulk `$expand` already fetched
gets one `CodeSystem/$validate-code` call; if the server confirms a match,
the outcome downgrades to `LABEL_DESIGNATION_DRIFT`, but a rejection never
escalates past whatever the workbook-scoped check above already found. A run
can never abort *because* of the probe - only because the label already
didn't match anything before it was asked.

The comparison itself is Unicode NFC plus edge-whitespace stripping, never
casefolding and never NFKC - two designations differing only in case, or in a
compatibility-equivalent character (a micro sign vs. a Greek mu, for example),
are treated as genuinely different, not folded into a false match. A label
still carrying an interior invisible character after that stripping is
skipped rather than reconciled - `INVISIBLE_CHARACTER`/
`INVISIBLE_CHARACTER_AMBIGUOUS` already own that cell.

`LABEL_DIFFERS_FROM_PREFERRED_TERM` is separate from all four outcomes above
and always informational: it fires whenever the current SNOMED CT-AU
preferred term differs from the column value, for every row the four-outcome
check found benign - never for a row already reported as one of the two
defects, so a single cell is never on both lists at once.

### Interpreting a misspelling finding (FR-79)

The pass reads only the `RCPA Preferred term` and `RCPA Synonyms` columns, tokenises each
cell (delimiter-independent - a comma, a semicolon, and a bare space are all equally valid
separators, sidestepping FR-71's own unresolved question about which the `RCPA Synonyms`
column actually uses), and runs two heuristics, in order of reliability:

1. **Intra-entry near-match** (`PROBABLE_MISSPELLING`). A token in one entry's own
   preferred-term/synonym cells is compared against every other comparable token in the
   *same* entry, and - when `--check-terminology` ran - against the served designations and
   FSN of the concept that entry's code binds to. This is the strongest signal: the correct
   spelling is present right there, in the same row or on the server. It is what catches
   PRD row 47/48's `Epinephine` even with only that one row present.
2. **Cross-entry corpus frequency** (`INCONSISTENT_SPELLING`). A token used in only one or
   two entries across the *whole* workbook that near-matches a token used in three or more,
   with the common spelling at least three times as frequent - PRD row 51's `antental`
   against the far more common `antenatal`.

At most one finding per cell per token, across both heuristics: heuristic 1 always wins when
both would fire for the same token.

**The authority whitelist and its two precision regimes.** Every token that appears in a
served designation or FSN, across every edition `--check-terminology` swept, can be cited as
a *reference* but never itself flagged as a *suspect* - this is the PRD's own "domain word
list assembled from the SNOMED FSNs" (PRD:880), read as a veto inside both heuristics rather
than a third, separate check. `report.json`'s `misspellings.authority_source` records which
of two honestly different regimes produced the findings in front of you:

- **`SWEEP`** - `--check-terminology` ran, and the whitelist is built from what the server
  actually served. Higher precision: a genuine SNOMED-served spelling that also happens to
  be corpus-rare is correctly left alone.
- **`WORKBOOK_ONLY`** - no sweep ran. Both heuristics still run in full, over the workbook's
  own content alone, but the whitelist is empty - lower precision, and the report says so
  explicitly in both `report.json` and `report.md` rather than reading identically to a
  sweep-backed run.

**Accepted, documented misses - do not "fix" these by narrowing the whitelist:**

- A genuinely rare-but-correct word that also happens to be a real, served designation of
  some entirely unrelated concept reads as authoritative and is never flagged, even if it
  is, in a specific row, actually a typo. The whitelist cannot distinguish "genuinely this
  word" from "coincidentally spelled the same as this word" - narrowing it to try would
  reintroduce the false positives it exists to suppress on the common case (two genuinely
  distinct, both-real, one-edit-apart analytes that are each legitimately served).

Both codes are `Band.INFORMATIONAL` (never blocking) and are candidates for editorial review
only - see ADR-0007's Rejected alternatives for why neither a blocking band nor
auto-correction was considered acceptable (PRD:884: "Automatically 'fixing' a term in a
clinical terminology on the basis of an edit-distance heuristic is not acceptable").

### Interpreting a semantic-drift finding (FR-75)

PRD Annex A.9's own worked examples show roughly as many benign rows as
genuine ones - a false-positive rate that makes a blocking band indefensible
(see ADR-0008). All three codes are `Band.INFORMATIONAL`: candidates for a
terminologist's review, never automated corrections. Four worked rows from
Annex A.9, run through this pass:

| Term | Code | Bound concept's `Has specimen` | Outcome |
|---|---|---|---|
| `Acetone urine` | `47615003` | `122575003` (urine) | No finding - the asserted specimen (urine) agrees with the modelled value. |
| `14-3-3 protein CSF` | `430551003` | `258450006` (CSF) | No finding, and no classification request at all - the term's own FSN literally contains "CSF", so the visibility filter suppresses it before any server call. |
| `4-Hydroxy-3-methoxymandelate urine 24h` | `121302000` | *(none)* | `TERM_SPECIMEN_NOT_MODELLED` - the term asserts urine and a `24 h` timing, and the concept constrains no specimen at all. |
| `Adenovirus Ag faeces` | `121960004` | *(none)* | `TERM_SPECIMEN_NOT_MODELLED` - asserts faeces, no specimen modelled. |

**The check is a heuristic over free text, not a lookup.** A term's asserted
specimen/timing comes from matching a small, hand-typed table of surface
forms (`nptc_transform.specimen_table.SPECIMEN_TABLE`) against the RCPA
preferred term, word-boundary and case-folded. The table is deliberately not
exhaustive - see "The specimen table is an allowlist" below.

**The free visibility filter is what keeps a row like row 7 silent.** Before
any classification request is issued, a term whose own served designations
(FSN, preferred term, any synonym, across every edition it resolved in) - or
its asserted specimen concept's own served designations - already carry the
wording it asserts needs no further check at all: the wording is expected,
not drift. This is checked against *hand-typed table terms union whatever the
specimen concept's own `describe()` call served* - a specimen concept with a
served synonym the hand-typed table doesn't happen to list (e.g. CSF's own
"CSF specimen"/"CSF - Cerebrospinal fluid sample") still suppresses a term
that uses it, even though the table itself never mentions that exact wording.

**Precedence: exactly one finding per row.** `TERM_SPECIMEN_NOT_MODELLED` >
`TERM_SPECIMEN_DIFFERS` > `TERM_TIMING_NOT_MODELLED`. A row with both an
unmodelled specimen and an unmodelled timing gets one finding, at the
strongest code, whose message names both assertions - never two findings for
the one row.

**Unlike FR-97, a hierarchy violation (`OUT_OF_SCOPE_HIERARCHY`) is not
excluded here.** A specimen/timing drift finding is about the term's own
content, and survives whatever rebinding would fix the hierarchy violation -
the two findings can, and sometimes should, fire on the same cell together.

**The specimen table is an allowlist, never a finding generator.** A term
asserting a specimen no group in the table covers is silently never
inspected for that aspect at all. The mitigation is a coverage *audit*, never
an assertion source: the workbook's own `Specimen` column (free text, not
controlled vocabulary) is checked only for how many distinct values map to no
group - `specimen_column_values_unmapped` in `report.json`'s `drift` block -
so a systematically-uncovered specimen shows up as a number to investigate,
not a silent zero.

`specimen_table_entries_unresolved` is the same "the check did not actually
run for that many, not a silent pass" signal `unresolved_fsn_count` already
gives FR-99: a specimen-table SCTID the server didn't resolve at all still
functions on its hand-typed terms alone, just without server augmentation -
persistently nonzero here is worth investigating the server's response for
that concept.

### Determinism with terminology on (FR-73)

Two runs against the same workbook stay byte-identical only while the server
resolves the same edition versions. That is the intended reading - the SNOMED
release is an input to the run - and `report.json`'s `terminology` block is
what records which release each edition resolved to. A run without
`--check-terminology` reports `"terminology": null`, which is a different fact
from a run that checked and found nothing.

## Not implemented yet

The following are owned by later P0 issues (see the P0 milestone on GitHub
for the current issue numbers) and will change what `report.json`/`report.md`
contain, but not the guarantees above:

- **Applying the auto-correctable band's corrections** and emitting the import
  dataset, including the synthetic baseline release - P0-9
  (`--emit-dataset`)
- FR-36's "same check on save in the application" half of FR-79 - a second PR
  against the backend, not part of the transform

FR-45's own check table (FSN drift, preferred-term drift, inactivation reason
and historical association) is the *steady-state* descendant of this pass,
not part of it: it belongs to the backend's scheduled P3 validation sweep,
run against catalogue entries already stored as served (FR-82), where
"matches nothing" is structurally impossible - see FR-97's own PRD text for
why that distinction matters.
