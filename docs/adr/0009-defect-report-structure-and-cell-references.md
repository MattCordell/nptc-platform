# ADR-0009: Grouped defect report structure and a structured `CellRef` location

**Status:** Accepted
**Date:** 2026-08-12

## Context

FR-72 requires the transform's defect report to be machine-readable and human-readable, and
requires the human-readable form specifically to be organised by defect class, cite exact
cell references, and state the required action. `report_writer.py`, `pipeline.py`,
`findings.py` and `__init__.py` all deferred this to P0-8 (issue #30); until now `report.json`
carried one flat `findings` array and `report.md` rendered one flat table, sorted by an opaque
`Sheet!CellRef` string, with no statement of what an editor should do about any finding.
ADR-0004 had already settled that the finding code is the defect-class key ("organised by
defect class" needs a key, and `code` is the only field every finding carries that maps
1:1 onto one required action - `band` alone is too coarse, since two codes can share a band
but need different remediation, e.g. `CODE_NOT_FOUND` vs. `OUT_OF_SCOPE_HIERARCHY`).

## Decision

1. **Grouping is two-level: Band -> FindingCode, blocking bands first.** A new
   `bands.BAND_REPORT_ORDER` (`REQUIRES_HUMAN_DECISION`, `DATA_DEFECT`, `AUTO_CORRECTABLE`,
   `INFORMATIONAL`) is a presentation order, applied to both the band-count table and the
   grouped findings sections - deliberately *not* a reordering of `Band`'s own declaration
   order, which faithfully transcribes FR-71's table plus ADR-0004's fourth-member narrative.
2. **`Finding.location` becomes a structured `CellRef(sheet, column_letter, row)`**, a new
   leaf module (`cellref.py`, imports nothing local), replacing the opaque
   `f"{sheet}!{column}{row}"` string end-to-end - every construction site in `workbook.py`,
   `cell_defects.py`, `terminology_check.py` and every module that consumes them.
   `CellRef.sort_key()` sorts columns and rows numerically (`B2 < B10`, `B1 < AA1`), which a
   plain string comparison cannot: `"B10" < "B2"` and `"AA1" < "B1"` lexicographically, both
   backwards from what an editor working through cell references in a spreadsheet expects.
3. **A required-action registry, `actions.py`**, mirrors `bands.py`'s own pattern
   (`ACTION_BY_CODE`, an import-time completeness assert against every `FindingCode`, a
   band-level fallback for an unregistered code) without living inside `bands.py` itself -
   that module's docstring stakes the precise claim "this registry alone chooses the band",
   and 23 x 1-3 sentences of operator prose would bury it.
4. **No CSV.** `report.json` and `report.md` stay the only two output files - nothing outside
   `transform/` reads `report.json` today, and a third format would need its own grouping,
   its own escaping discipline, and its own place to drift from the other two.
5. **`report.json`'s flat `findings` array is replaced by `defect_classes`, not kept alongside
   it** (`schema_version` 6 -> 7). `code`/`band`/`action`/`blocks_import` live once per group;
   each finding underneath carries only `location` (structured, plus a rendered `ref`
   convenience string) and `message`. Keeping both would serialise every finding twice and let
   the copies drift - exactly the failure FR-72's "organised by defect class" exists to forbid.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| CSV as the machine-readable form, or a third file alongside JSON/Markdown | FR-72 asks for "JSON or CSV" - JSON already satisfies it. A third file needs its own grouping and escaping discipline and is one more place for the two existing files to drift from; nothing in this repo or its consumers reads CSV today. |
| Keep `Finding.location` as an opaque string, only reformatting the report's presentation | Does not fix the actual defect: a plain string cannot sort cell references numerically, and a sheet name containing `!` (legal in Excel) makes `Sheet!Q1!B12` unparseable - genuinely ambiguous, not just inconvenient. `report.json`'s own `location` field would still need to expose the parts eventually; doing it at the type level once removes every re-splitting call site rather than adding one more. |
| Serialise both a flat `findings` array and the grouped `defect_classes` array in `report.json`, for backwards compatibility | No current consumer reads `report.json` outside this PR - the runbook and ADR-0007 only mention it in prose, and P0-9 will consume `RunResult.findings` in process, never the file. Backwards compatibility has no claimant here, and two copies of the same data is exactly the drift risk FR-72's "organised by defect class" language exists to prevent. |
| Reorder the `Band` enum itself to put blocking bands first | `Band`'s declaration order is a citation of FR-71's own table plus ADR-0004's fourth-member narrative, not a presentation choice - reordering it would make a future reader of `bands.py` re-derive "why this order" from git history instead of from the enum reading top to bottom. `BAND_REPORT_ORDER` names the presentation concern once, separately, and is reused everywhere presentation order matters. |
| Put `ACTION_BY_CODE` inside `bands.py`, next to `BAND_BY_CODE` | `bands.py`'s docstring already stakes a precise, narrow claim: "this registry alone chooses the band." Burying 23 multi-sentence action strings in the same module obscures that claim for a reader trying to verify it, for no benefit - `actions.py` imports `bands.py`, not the reverse, so the dependency direction stays the same either way. |

## Consequences

- **The report's finding sort order changes.** Today `Requesting!B10` sorts before
  `Requesting!B2` (string comparison); after this PR, `B2` sorts before `B10`, and `B1` before
  `AA1`. Accepted deliberately - see Decision 2 - and costs nothing: there are no committed
  golden report fixtures (`test_idempotency.py`/`test_determinism.py` both compare a live run
  against a live run), so nothing outside this PR's own updated tests pins the old order.
- **`report.json`'s `schema_version` moves from 6 to 7**, and any external tooling reading the
  flat `findings` array (there is none known today) would need to move to `defect_classes`.
- **The runbook's required-action table and `actions.ACTION_BY_CODE` are two copies of the
  same 23 strings.** `transform/tests/test_actions.py::test_every_action_matches_the_runbooks_table`
  guards this - a future edit to one without the other fails that test, naming the stale code.
- **`CellRef` becomes load-bearing for `checkable_locations` sets** in `designation_check.py`
  and `semantic_drift.py` (`code_cell.reference not in checkable_locations`). Before this PR,
  a mismatched type there (`Cell.reference: CellRef` against `CodeBinding.location: str`)
  would have made that check silently always-`True`, skipping every row - both types moved
  together in one PR specifically to avoid that window ever existing in a committed state.
