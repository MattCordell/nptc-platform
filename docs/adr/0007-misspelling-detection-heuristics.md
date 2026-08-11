# ADR-0007: Misspelling detection heuristics — banded Levenshtein in `shared/`, no new dependency, thresholds as constants

**Status:** Accepted
**Date:** 2026-08-12

## Context

FR-79 requires the transform to detect probable misspellings in the `RCPA Preferred
term`/`RCPA Synonyms` columns - PRD:874-886 names it twice as a defect class the source
data actually contains (row 47/48's `Epinephine`, row 51's `antental`) and prescribes three
heuristics "in order of reliability": (1) an intra-entry near-match against another
designation on the same entry or the bound FSN, (2) a cross-entry frequency gap (a token
common in one spelling, rare in another), and (3) a dictionary check "against a domain word
list assembled from the SNOMED FSNs of all bound concepts... **a general English dictionary
is useless here** and would generate overwhelming noise." Findings are "warnings for
editorial review, never auto-corrections" (PRD:884) - H-04's mitigation is exactly this,
not a spelling correction. PRD:886 also requires the same check "on save in the application
(FR-36)", not only in the transform - a second requirement this PR does not implement, but
one that shapes where the comparison logic must live.

Two decisions have no single obvious answer:

**Where the string-comparison primitive lives.** FR-36's on-save check and this PR's
seeding-transform check must never independently drift on what counts as a near-match
(FR-74, ADR-0003's own reasoning for the terminology client) - two edit-distance
implementations that happen to agree today are one refactor away from disagreeing.

**What "domain word list" (PRD heuristic 3) actually means as code.** The PRD names it as
a third, independent heuristic; this ADR instead makes it a *cross-cutting whitelist* over
heuristics 1 and 2 - see Decision 3 below for why that is a refinement, not a deviation.

## Decision

**1. A new module, `shared/src/nptc_shared/similarity.py`, owns tokenising and bounded
edit distance; `transform/src/nptc_transform/misspelling.py` owns the two heuristics and
their thresholds.**

`shared/` is where FR-74/ADR-0003 already put the terminology client for the identical
reason: FR-36's on-save check (deferred to a second PR) and the seeding transform's check
are two call sites that must call the same function, not two independently maintained
ideas of "near enough". `nptc_shared.text` was not extended in place - its own docstring
scopes it to Unicode hygiene (NFC normalisation, invisible-character detection), and
`similarity.py`'s comparison logic is a different concern that happens to build on it
(`tokenise` calls `normalise_for_comparison` rather than reimplementing it).

**2. Bounded edit distance is a from-scratch banded dynamic-program (plain Levenshtein), not
`difflib.SequenceMatcher.ratio()`, not a new dependency, and not a general English
dictionary.**

See Rejected alternatives for each. The banding (width `2 * max_distance + 1` around the
main diagonal, with an early-exit once a row's minimum exceeds the budget) keeps a
worst-case comparison cheap even though the pass compares many token pairs across a
multi-thousand-row catalogue: a distance capped at 2 never needs the full `O(len(a) *
len(b))` table.

**3. The PRD's heuristic 3 ("dictionary check... domain word list assembled from the SNOMED
FSNs") is implemented as a whitelist that both heuristics 1 and 2 consult, not as a third,
independent detector.**

A token whose `token_key` appears anywhere in the FR-52 sweep's served designation values
or FSNs, across every edition, can never itself be named a *suspect* - only a *reference*.
This is a refinement of the PRD's own instinct (build the word list from the bound FSNs,
not a general dictionary) applied at the point where it actually changes an outcome: as a
veto inside the two detection heuristics, rather than a separate pass that would have
nothing to detect on its own (a domain word list has no notion of "probable misspelling" by
itself - it only ever answers "is this word known", which is exactly what heuristics 1/2's
tie-break needs). The whitelist is empty when no sweep is available
(`AuthoritySource.WORKBOOK_ONLY`) - both heuristics still run, only with lower precision -
never a reason to skip the pass.

**4. Distance 1 is always admissible; distance 2 only between tokens where the shorter one
is at least 8 characters (`LONG_TOKEN_LENGTH`); every comparable token is at least 5
characters (`MIN_TOKEN_LENGTH`) and carries no digit.**

`urine`/`urate` (both length 5, edit distance 2) must be refused - two edits is too large a
fraction of a 5-character word to be a confident signal rather than two genuinely different
short words. The digit exclusion is what keeps `ADA2`/`5HIAA`/`7DHC`/`8DHC` - genuine
alphanumeric lab shorthand - out of the comparison entirely, rather than needing a
per-code exception list.

**5. An all-uppercase surface form can be cited as a reference but never named a suspect.**

`ALPHAFETOPROTEIN` (PRD row 51's own entry, `AFP antenatal`) is a fine word to compare
*against*; flagging it as *itself* a probable misspelling would treat every initialism in
the catalogue as suspect. This restriction is deliberately layered in
`misspelling.py`, not in `similarity.is_comparable_token` - see that function's own
docstring for why the split sits there.

**6. Heuristic 1 (intra-entry) always takes precedence over heuristic 2 (cross-entry) for
the same cell/token - at most one finding per cell per token, across both heuristics.**

PRD:876's "in order of reliability" is read here as a strict precedence, not merely a
suggested reading order: a token with an in-entry reference has direct, local evidence
(the correct spelling is present right there), which is categorically stronger than a
corpus-wide frequency gap that says nothing about *this* entry specifically.

**7. Thresholds are module constants (`similarity.py`'s three, plus `misspelling.py`'s rare/
common/ratio trio), never an `NPTC_TX_*` environment variable.**

Contrast ADR-0005, whose chunk-size/concurrency defaults are configurable precisely because
FR-52 explicitly requires the concurrency ceiling to be tunable and the chunk size to be
tuned *per Ontoserver instance* - a live, external, per-deployment constraint. Nothing
here has an external counterpart to tune against: `MIN_TOKEN_LENGTH`, `LONG_TOKEN_LENGTH`,
and the rare/common/ratio trio are editorial judgement calls about English/pathology
vocabulary shape, not properties of a server a deployer configures. Making them
configurable would imply a tuning procedure that does not exist, and would let two
deployments' reports disagree about what "probable" means for the same input.

### How to tune these constants

Unlike ADR-0005, there is no live server to tune against - these are read, not measured.
If a specific catalogue turns out to produce too many/few findings:

1. Read a sample of `PROBABLE_MISSPELLING`/`INCONSISTENT_SPELLING` findings against the
   real workbook and judge, by eye, whether the false-positive or false-negative rate is
   the problem.
2. Too many false positives on short lab shorthand: raise `MIN_TOKEN_LENGTH`. Too many
   missed genuine typos on short words: this is the wrong lever - a shorter minimum
   trades directly against the digit-free abbreviation problem FR-79's own examples
   (`ADA`, `AFP`) exist to avoid; don't lower it without re-reading PRD Appendix A.5 first.
3. Too many cross-entry false positives on genuinely-common short words: raise
   `MIN_COMMON_COUNT` or `COMMON_TO_RARE_RATIO`.
4. Too many missed corpus-wide drifts where the rare spelling occurs more than twice:
   raise `MAX_RARE_COUNT`, but recognise this is trading precision for recall directly -
   a spelling used many times is a weaker "rare" signal by construction.
5. Change the constant, re-run against the real workbook, and record the new value and the
   reasoning here as a dated amendment - state plainly that it is a judgement call, not a
   measurement, the same honesty ADR-0005 insists on for its own untuned defaults.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| A general English dictionary as the whitelist | PRD:880 rejects this by name: "useless here and would generate overwhelming noise" - pathology vocabulary (`Acanthamoeba`, `hydroxymandelate`) is not English-dictionary vocabulary, and a general dictionary would flag most of the catalogue as unknown while missing the domain-specific typos FR-79 actually cares about. |
| `rapidfuzz` / `jellyfish` / another C-extension fuzzy-matching library | A new runtime dependency for a ~30-line algorithm this codebase can own and test directly (NFR-37's offline test discipline already extends to "no network to install a wheel against" in spirit); the banded early-exit this ADR needs is not a documented guarantee of any of these libraries' pure-Python fallback paths. |
| `difflib.SequenceMatcher.ratio()` | Wrong shape: a normalised similarity ratio, not a count of edits. FR-79's own words are "differ by one or two characters" - a character count, not a 0-1 score a caller would then have to reverse-engineer a threshold for. |
| Trigram/n-gram similarity | Answers a different question (shared substrings) than "differ by N characters" - and PRD:878 states the requirement as edit distance explicitly, alongside trigram similarity as a *complementary* signal it does not actually specify how to combine. Implementing only what the requirement pins down avoids inventing a fusion rule nobody asked for. |
| Whole-designation string comparison (compare `RCPA Synonyms` cell text directly, not per-token) | Forces exactly the delimiter question FR-71 leaves open for that column (comma vs. semicolon, PRD Appendix A.4) - tokenising first sidesteps it entirely, since every non-word character is an equally valid separator (see `similarity.tokenise`'s own tests). |
| An `NPTC_TX_*` environment variable per threshold | See Decision 7 - contrast ADR-0005, where the configurability is a direct answer to FR-52's explicit tunability requirement against a live server. Nothing plays that role here. |
| Treating adjacent workbook rows as heuristic-1 reference material | Annex A.5's own negative controls are near-identical adjacent entries differing only by specimen (PRD's collision-detection examples) - row adjacency is not evidence of a shared designation, and using it would manufacture false positives on exactly the rows FR-05's own hazard log entries are about. |
| A blocking band (`data-defect`/`requires-human-decision`) for either finding code | PRD:884 states plainly: "warnings for editorial review, never auto-corrections." A heuristic guess about spelling is not the kind of "the source data is wrong or unrecoverable" claim the data-defect band makes (see `bands.py`), and aborting an otherwise-clean seeding run over a guess would be a worse failure mode than an occasional false positive in a report nobody is forced to act on. |
| Auto-correcting the flagged token | PRD:884's second sentence, verbatim: "Automatically 'fixing' a term in a clinical terminology on the basis of an edit-distance heuristic is not acceptable." Both codes are informational precisely so nothing downstream ever treats a heuristic guess as ground truth. |

## Consequences

- `shared/src/nptc_shared/similarity.py` is a new module with no dependency on
  `nptc_transform` - FR-36's future on-save check (backend) imports it directly, the same
  way the backend will eventually import `nptc_shared.terminology`.
- `transform/src/nptc_transform/misspelling.py` is a new pass, wired into
  `pipeline.run_transform_sheets` on both branches (with and without `--check-terminology`)
  - unlike designation reconciliation (ADR-0006), this pass does not require a sweep to run
  at all, only to run at full precision.
- Two new finding codes (`PROBABLE_MISSPELLING`, `INCONSISTENT_SPELLING`), both
  `Band.INFORMATIONAL` - never blocking, per PRD:884.
- `report_writer.SCHEMA_VERSION` moves from 4 to 5; `report.json` gains a `misspellings`
  block whose `thresholds` object echoes this module's constants verbatim, and whose
  `authority_source` (`SWEEP`/`WORKBOOK_ONLY`) tells a reader which of two honestly
  different precision regimes produced the findings in front of them.
- FR-79 moves to `in-progress` in `docs/requirements/requirements.yaml`, not `implemented`
  - PRD:886's on-save half (FR-36) is a second PR against the backend, not part of this one.
- H-04 in `docs/governance/hazard-log.md` moves to reflect that its mitigation is now
  implemented in the transform - the same hazard's on-save half (FR-36) remains open until
  that second PR lands.
