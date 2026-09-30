"""FR-71's three defect bands, plus the finding codes that carry them.

Band is a pure function of a finding's ``code`` alone (``band_for``), never of
its content: the detector that produces a ``Finding`` chooses the code, and
this registry alone chooses the band. A defect whose correct band depends on
context (for example, an invisible character that may or may not be
deterministically repairable) is therefore always *two* codes, split at
detection time in ``cell_defects.py``, never one code inspected here. That keeps
"every finding is assigned exactly one band" true by construction
(``findings.Finding.band``), with no way for a future detector to forget to
classify what it produces.

``Band`` has a fourth member, ``INFORMATIONAL``, that FR-71's table does not
name. FR-71 defines exactly three *defect* bands; FR-97 (designation
reconciliation) and FR-75 (semantic-mismatch warnings) both need a non-blocking
outcome that is not a defect at all: "seed silently, but tell someone". See
ADR-0004 for the alternatives this rejected.
"""

from __future__ import annotations

from enum import StrEnum


class Band(StrEnum):
    """A finding's severity band and the behaviour it implies (FR-71)."""

    AUTO_CORRECTABLE = "auto-correctable"
    REQUIRES_HUMAN_DECISION = "requires-human-decision"
    DATA_DEFECT = "data-defect"
    INFORMATIONAL = "informational"


# Enum member bodies must be str (or a value convertible to str): a container
# there is silently treated as another member and raises at class creation, not
# first use. Hence module level.
_BLOCKING_BANDS = frozenset({Band.REQUIRES_HUMAN_DECISION, Band.DATA_DEFECT})


def blocks_import(band: Band) -> bool:
    """True if a finding in ``band`` aborts the import (FR-71)."""
    return band in _BLOCKING_BANDS


class FindingCode(StrEnum):
    """Every finding code the transform can currently emit.

    A ``StrEnum`` member *is* a ``str``, so nothing that compares
    ``Finding.code`` to a literal (``report_writer``, ``sort_key``, existing
    tests) needs to change. Declaring the codes here rather than as bare strings
    in ``cell_defects.py`` makes registry completeness checkable at import time
    instead of by scraping source.
    """

    INVISIBLE_CHARACTER = "INVISIBLE_CHARACTER"
    INVISIBLE_CHARACTER_AMBIGUOUS = "INVISIBLE_CHARACTER_AMBIGUOUS"
    SURROUNDING_WHITESPACE = "SURROUNDING_WHITESPACE"
    WHITESPACE_ONLY_CELL = "WHITESPACE_ONLY_CELL"
    CODE_CELL_NOT_TEXT = "CODE_CELL_NOT_TEXT"
    EMPTY_SYNONYM_REMOVED = "EMPTY_SYNONYM_REMOVED"
    SPECIMEN_UNCONSTRAINED_RESOLVED = "SPECIMEN_UNCONSTRAINED_RESOLVED"
    COMPOUND_VALUE_SPLIT = "COMPOUND_VALUE_SPLIT"
    SPECIMEN_VALUE_UNMAPPED = "SPECIMEN_VALUE_UNMAPPED"
    CODE_CELL_INVALID_TYPE = "CODE_CELL_INVALID_TYPE"
    NUMERIC_PRECISION_RISK = "NUMERIC_PRECISION_RISK"
    UNRECOGNISED_LAYOUT = "UNRECOGNISED_LAYOUT"
    SHEET_NOT_SPIA_DATA = "SHEET_NOT_SPIA_DATA"
    CODE_NOT_WELL_FORMED = "CODE_NOT_WELL_FORMED"
    CODE_NOT_FOUND = "CODE_NOT_FOUND"
    CODE_INACTIVE = "CODE_INACTIVE"
    OUT_OF_SCOPE_HIERARCHY = "OUT_OF_SCOPE_HIERARCHY"
    UNEXPECTED_SEMANTIC_TAG = "UNEXPECTED_SEMANTIC_TAG"
    LABEL_DESIGNATION_DRIFT = "LABEL_DESIGNATION_DRIFT"
    LABEL_BOUND_TO_OTHER_CONCEPT = "LABEL_BOUND_TO_OTHER_CONCEPT"
    LABEL_MATCHES_NO_DESIGNATION = "LABEL_MATCHES_NO_DESIGNATION"
    LABEL_DIFFERS_FROM_PREFERRED_TERM = "LABEL_DIFFERS_FROM_PREFERRED_TERM"
    PROBABLE_MISSPELLING = "PROBABLE_MISSPELLING"
    INCONSISTENT_SPELLING = "INCONSISTENT_SPELLING"
    TERM_SPECIMEN_NOT_MODELLED = "TERM_SPECIMEN_NOT_MODELLED"
    TERM_SPECIMEN_DIFFERS = "TERM_SPECIMEN_DIFFERS"
    TERM_TIMING_NOT_MODELLED = "TERM_TIMING_NOT_MODELLED"
    MISSING_PREFERRED_TERM = "MISSING_PREFERRED_TERM"
    MISSING_CODE_BINDING = "MISSING_CODE_BINDING"


BAND_BY_CODE: dict[str, Band] = {
    # Auto-correctable: FR-71 names these examples directly.
    FindingCode.INVISIBLE_CHARACTER: Band.AUTO_CORRECTABLE,
    FindingCode.SURROUNDING_WHITESPACE: Band.AUTO_CORRECTABLE,
    FindingCode.CODE_CELL_NOT_TEXT: Band.AUTO_CORRECTABLE,
    # Auto-correctable: a doubled synonym delimiter, a discipline/subgroup
    # "X or Y" compound value, and "Any" as a specimen value each have one
    # deterministic repair (drop the empty synonym, split into separate property
    # values, resolve to specimen_unconstrained with no specimen code).
    FindingCode.EMPTY_SYNONYM_REMOVED: Band.AUTO_CORRECTABLE,
    FindingCode.SPECIMEN_UNCONSTRAINED_RESOLVED: Band.AUTO_CORRECTABLE,
    FindingCode.COMPOUND_VALUE_SPLIT: Band.AUTO_CORRECTABLE,
    # Requires human decision: no deterministic repair exists (FR-70).
    FindingCode.INVISIBLE_CHARACTER_AMBIGUOUS: Band.REQUIRES_HUMAN_DECISION,
    FindingCode.WHITESPACE_ONLY_CELL: Band.REQUIRES_HUMAN_DECISION,
    # Data defect: the value is already lost or was never a valid SCTID, or the
    # sheet's rows went unscanned.
    FindingCode.CODE_CELL_INVALID_TYPE: Band.DATA_DEFECT,
    FindingCode.NUMERIC_PRECISION_RISK: Band.DATA_DEFECT,
    FindingCode.UNRECOGNISED_LAYOUT: Band.DATA_DEFECT,
    # Data defect: FR-71's data-defect column names codes failing Verhoeff
    # validation, codes not resolving in either edition and codes not subsumed
    # by <<71388002. Each is a source defect RCPA-QAP must correct; no repair
    # the transform could make is deterministic, or even knowable.
    FindingCode.CODE_NOT_WELL_FORMED: Band.DATA_DEFECT,
    FindingCode.CODE_NOT_FOUND: Band.DATA_DEFECT,
    FindingCode.CODE_INACTIVE: Band.DATA_DEFECT,
    FindingCode.OUT_OF_SCOPE_HIERARCHY: Band.DATA_DEFECT,
    # FR-97's two blocking outcomes, named in FR-71's data-defect column: stored
    # text matching no designation on the concept, or matching the FSN of a
    # different concept. Both are the transcription error the PRD calls "the most
    # dangerous outcome" (a plausible label paired with the wrong code), and both
    # abort rather than repair, because which half is wrong cannot be decided
    # automatically.
    FindingCode.LABEL_BOUND_TO_OTHER_CONCEPT: Band.DATA_DEFECT,
    FindingCode.LABEL_MATCHES_NO_DESIGNATION: Band.DATA_DEFECT,
    # Data defect: a row that resolves a code binding but has no 'RCPA Preferred
    # term' has no preferred designation to seed. No repair is deterministic, and
    # silently omitting the row is the "some rows were silently dropped" hazard
    # ADR-0010 §8 blocks emission to prevent.
    FindingCode.MISSING_PREFERRED_TERM: Band.DATA_DEFECT,
    # Data defect: the mirror case, a 'RCPA Preferred term' value with no code
    # binding. Not seeded with an empty binding list: a code-less row is more
    # likely layout (a heading, a continuation line) than an entry awaiting a
    # code, so dataset.py omits it, and this finding tells the operator it
    # happened.
    FindingCode.MISSING_CODE_BINDING: Band.DATA_DEFECT,
    # Informational: not a defect at all (see the module docstring).
    FindingCode.SHEET_NOT_SPIA_DATA: Band.INFORMATIONAL,
    # FR-99: an unexpected semantic tag is a warning, not an error, because
    # subsumption does not imply the tag (71388002 |Procedure| subsumes 243120004
    # |Regime/therapy (regime/therapy)|). A blocking band would abort the import
    # over a valid procedure binding.
    FindingCode.UNEXPECTED_SEMANTIC_TAG: Band.INFORMATIONAL,
    # FR-71: a published label that is merely a synonym or superseded FSN is not
    # a data defect: "the catalogue lagging the terminology is expected rather
    # than defective". FR-97 has the transform seed the served FSN and only tell
    # someone.
    FindingCode.LABEL_DESIGNATION_DRIFT: Band.INFORMATIONAL,
    # FR-97's separate, always-informational list: the current AU preferred term
    # differs from the published label, whatever the axis-1 outcome for the cell.
    FindingCode.LABEL_DIFFERS_FROM_PREFERRED_TERM: Band.INFORMATIONAL,
    # FR-79/H-04: candidates for editorial review only. A heuristic guess about
    # spelling is not the "lost value or source defect" claim the data-defect
    # band makes.
    FindingCode.PROBABLE_MISSPELLING: Band.INFORMATIONAL,
    FindingCode.INCONSISTENT_SPELLING: Band.INFORMATIONAL,
    # FR-75/H-03: a specimen or timing mismatch is a candidate for editorial
    # review, never a confirmed defect. PRD Annex A.9's examples show roughly as
    # many benign rows as genuine ones, so a blocking band is indefensible at that
    # false-positive rate (ADR-0008).
    FindingCode.TERM_SPECIMEN_NOT_MODELLED: Band.INFORMATIONAL,
    FindingCode.TERM_SPECIMEN_DIFFERS: Band.INFORMATIONAL,
    FindingCode.TERM_TIMING_NOT_MODELLED: Band.INFORMATIONAL,
    # FR-88: the specimen table is an allowlist, not a finding generator. A
    # specimen value with no exact match is seeded verbatim as a provisional value
    # with no code, never blocked; this is the coverage signal that it happened.
    FindingCode.SPECIMEN_VALUE_UNMAPPED: Band.INFORMATIONAL,
}

if set(BAND_BY_CODE) != set(FindingCode):
    # A code with no band would defeat the one guarantee this module provides.
    missing = set(FindingCode) - set(BAND_BY_CODE)
    raise AssertionError(f"FindingCode member(s) missing from BAND_BY_CODE: {missing}")


def band_for(code: str) -> Band:
    """Returns the band for ``code``.

    Falls back to ``Band.DATA_DEFECT``, the most conservative band because it
    blocks import, for a code the registry doesn't recognise, rather than
    raising. A finding must resolve to exactly one band; failing safe means a
    detector that emits an unregistered code blocks the import it should have
    blocked, instead of passing as clean.
    """
    return BAND_BY_CODE.get(code, Band.DATA_DEFECT)


#: The order findings are *presented* in a report (FR-72), blocking bands first.
#: Deliberately not ``Band``'s declaration order, which transcribes FR-71's table
#: plus ADR-0004's fourth member and must not be reordered for presentation. One
#: name serves the band-count table and the grouped findings sections
#: (``report_writer.py``), so the report has a single presentation order.
BAND_REPORT_ORDER: tuple[Band, ...] = (
    Band.REQUIRES_HUMAN_DECISION,
    Band.DATA_DEFECT,
    Band.AUTO_CORRECTABLE,
    Band.INFORMATIONAL,
)

if set(BAND_REPORT_ORDER) != set(Band) or len(BAND_REPORT_ORDER) != len(Band):
    raise AssertionError("BAND_REPORT_ORDER must contain every Band member exactly once")
