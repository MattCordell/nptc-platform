# ADR-0004: A fourth, non-blocking `INFORMATIONAL` band; band assigned from the finding code, never from content

**Status:** Accepted
**Date:** 2026-08-06

## Context

FR-71 requires the transform to classify every finding into one of three bands -
auto-correctable, requires-human-decision, data-defect - and for the band to determine
behaviour: auto-correctable is fixed and itemised, the other two abort the import. P0-3
(issue #25) implements that classifier.

Two problems surfaced immediately that FR-71's own three-band table does not resolve.

**A fourth outcome that isn't a defect at all.** FR-97 (designation reconciliation) requires
that a published label matching another active designation on the correct concept be
"[reported] as informational. Seed with the served FSN" - explicitly not aborted. FR-75
(semantic-mismatch heuristics) requires its findings be reported "as warnings for editorial
review... candidates for review, not confirmed defects." Neither fits any of FR-71's three
bands, all of which either auto-correct or abort. P0-3 also has its own instance: a sheet
that resolves no SPIA column at all (the published workbook's own `Rev History` worksheet,
FR-63) is not SPIA data and must not abort an otherwise-clean import, but it still needs
reporting so an operator can see the sheet was skipped rather than silently trusting a low
`finding_count`.

**Where band assignment should live.** `cell_defects.py` (P0-2) produces five finding codes
with no severity attached. The two invisible-character and whitespace codes for A.1/A.3 each
cover more than one underlying defect: `INVISIBLE_CHARACTER` fires for any invisible
character regardless of Unicode category, and `SURROUNDING_WHITESPACE` fires whether or not
stripping leaves anything behind. Within each of those two codes, FR-71 puts different cases
in different bands - a non-breaking space (`Zs`) normalises deterministically to a space
(auto-correctable), but a zero-width space or bidi override (`Cf`) has no single correct
repair (requires-human-decision); a padded cell strips to real content (auto-correctable),
but a cell that's nothing *but* whitespace would be emptied entirely by stripping
(requires-human-decision).

## Decision

1. Add a fourth `Band` member, `INFORMATIONAL`, with `blocks_import() is False`. It is
   explicitly **not** one of FR-71's three defect bands - it exists for the "this is not a
   defect, but tell someone" outcome that FR-97 and FR-75 require and FR-71 does not name.
2. Band is a pure function of a finding's `code` alone (`nptc_transform.bands.band_for`),
   never of its content. `Finding.band` is a property computed from `code`, not a field a
   detector sets, so every `Finding` that can be constructed is classified by construction.
3. Where FR-71 puts different cases of the same PRD Appendix defect in different bands, the
   *detector* (`cell_defects.py`) splits the finding code by shape at detection time, so the
   classifier never inspects content:
   - `INVISIBLE_CHARACTER` (a `Zs` non-ASCII space; deterministic repair) vs.
     `INVISIBLE_CHARACTER_AMBIGUOUS` (`Cc`/`Cf`/`Zl`/`Zp`; no deterministic repair).
   - `SURROUNDING_WHITESPACE` (padding around real content) vs. `WHITESPACE_ONLY_CELL`
     (stripping would empty the cell).
   - `UNRECOGNISED_LAYOUT` (some SPIA columns resolved, not the code column: genuine FR-63
     layout drift, rows went unscanned, blocks) vs. `SHEET_NOT_SPIA_DATA` (no SPIA column
     resolved at all: not SPIA data, informational, does not block).
4. The Unicode category test behind the first split (`is_normalisable_space`) lives in
   `nptc_shared.text`, not in the transform - the same module already exists so the
   transform's detection and the backend's entry-time prohibition (FR-63, FR-74) can never
   diverge on what counts as an invisible character, and the backend will need the same
   "does this repair deterministically" predicate at entry time.
5. An unrecognised finding code fails safe to `Band.DATA_DEFECT` - the band that blocks -
   rather than being silently treated as clean.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Keep `Band` at exactly FR-71's three members; model the informational outcome as a separate `RunResult.notices` collection | Most faithful to FR-71's literal wording, but threads a second collection through `report_writer.py` and P0-8's grouped rendering, both of which then need parallel handling for "is this a finding or a notice". One band enum with a documented fourth member that predates FR-97/FR-75's own P0-6/P0-7 landing is a smaller surface than two collections that both need report support from day one. |
| Defer the informational outcome and the `Rev History` sheet's non-blocking status to whichever of P0-6/P0-7 lands first | Leaves `_scan_layout`'s only options as "blocking data defect" or "no finding at all" *today*, in this PR - and blocking is wrong: the published workbook always has a `Rev History` sheet, so every real run would exit 1 forever regardless of data quality, making the exit code meaningless. The `Rev History` case has to be resolved in P0-3, which means the fourth band has to exist in P0-3. |
| Classify a finding by inspecting its content (e.g. re-deriving the Unicode category of each character in the message) instead of splitting the code at detection time | Keeps `cell_defects.py` untouched, but makes the classifier re-derive knowledge the detector already had at the moment it built the message, duplicates the Unicode-category test in two places, and produces a code that spans two bands - unusable as `report.json`'s grouping key and as P0-8's defect-class key (FR-72: "organised by defect class"). |
| Give `Finding` a settable `band` field, populated by each detector | Requires every current and future detector to remember to set it - the exact "a detector forgot to classify what it produces" failure this design exists to make structurally impossible. A property derived from `code` cannot be forgotten. |

## Consequences

- `Band` has four members where FR-71 names three; every place that iterates `Band` (report
  band-count tables, the CLI's stderr summary) must render `informational` even though it is
  not one of FR-71's defect bands, and this ADR is the answer to "why four, not three" when
  that's asked in review.
- P0-3 emits eight finding codes instead of P0-2's original five. `docs/operations/runbooks/transform.md`'s
  finding-code table and this decision are now the two places that must stay in sync if a
  future PRD change adds a ninth.
- P0-6 (designation reconciliation, FR-97) and P0-7 (semantic-mismatch heuristics, FR-75)
  both land their informational outcomes on the `INFORMATIONAL` band already defined here,
  rather than inventing their own non-blocking mechanism.
- `nptc_shared.text.is_normalisable_space` becomes a second predicate the backend's
  entry-time prohibition (FR-63/FR-74) can reuse when it lands, alongside `is_invisible`.
