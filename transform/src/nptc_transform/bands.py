"""FR-71's three defect bands, plus the finding codes that carry them.

Band is a pure function of a finding's ``code`` alone (``band_for``), never of
its content: the detector that produces a ``Finding`` chooses the code, and
this registry alone chooses the band. A defect whose correct band depends on
context (for example, an invisible character that may or may not be
deterministically repairable) is therefore always *two* codes, split at
detection time in ``cell_defects.py`` - never one code inspected here. This
keeps "every finding is assigned exactly one band" true by construction
(``findings.Finding.band``), with no plumbing and no way for a future
detector to forget to classify what it produces.

``Band`` has a fourth member, ``INFORMATIONAL``, that FR-71's own table does
not name. FR-71 defines exactly three *defect* bands; FR-97 (designation
reconciliation) and FR-75 (semantic-mismatch warnings) both require a
non-blocking outcome that is not a defect at all - "seed silently, but tell
someone" - and neither P0-3 nor those requirements have anywhere else to put
it. See ADR-0004 for the alternatives this rejected.
"""

from __future__ import annotations

from enum import StrEnum


class Band(StrEnum):
    """A finding's severity band and the behaviour it implies (FR-71)."""

    AUTO_CORRECTABLE = "auto-correctable"
    REQUIRES_HUMAN_DECISION = "requires-human-decision"
    DATA_DEFECT = "data-defect"
    INFORMATIONAL = "informational"


# Enum member bodies must be str (or a value convertible to str) - a
# container here is silently treated as another member and raises at class
# creation time, not at first use. Kept at module level for that reason, not
# out of style preference.
_BLOCKING_BANDS = frozenset({Band.REQUIRES_HUMAN_DECISION, Band.DATA_DEFECT})


def blocks_import(band: Band) -> bool:
    """True if a finding in ``band`` aborts the import (FR-71)."""
    return band in _BLOCKING_BANDS


class FindingCode(StrEnum):
    """Every finding code the transform can currently emit.

    A ``StrEnum`` member *is* a ``str``, so nothing that already compares
    ``Finding.code`` to a literal (``report_writer``, ``sort_key``, existing
    tests) needs to change. Declaring the codes here, rather than as bare
    string literals in ``cell_defects.py``, is what makes registry
    completeness checkable at import time instead of by scraping source.
    """

    INVISIBLE_CHARACTER = "INVISIBLE_CHARACTER"
    INVISIBLE_CHARACTER_AMBIGUOUS = "INVISIBLE_CHARACTER_AMBIGUOUS"
    SURROUNDING_WHITESPACE = "SURROUNDING_WHITESPACE"
    WHITESPACE_ONLY_CELL = "WHITESPACE_ONLY_CELL"
    CODE_CELL_NOT_TEXT = "CODE_CELL_NOT_TEXT"
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


BAND_BY_CODE: dict[str, Band] = {
    # Auto-correctable: FR-71 names these examples directly.
    FindingCode.INVISIBLE_CHARACTER: Band.AUTO_CORRECTABLE,
    FindingCode.SURROUNDING_WHITESPACE: Band.AUTO_CORRECTABLE,
    FindingCode.CODE_CELL_NOT_TEXT: Band.AUTO_CORRECTABLE,
    # Requires human decision: no deterministic repair exists (FR-70).
    FindingCode.INVISIBLE_CHARACTER_AMBIGUOUS: Band.REQUIRES_HUMAN_DECISION,
    FindingCode.WHITESPACE_ONLY_CELL: Band.REQUIRES_HUMAN_DECISION,
    # Data defect: the value is already lost or was never a valid SCTID to
    # begin with, or the sheet's rows went unscanned.
    FindingCode.CODE_CELL_INVALID_TYPE: Band.DATA_DEFECT,
    FindingCode.NUMERIC_PRECISION_RISK: Band.DATA_DEFECT,
    FindingCode.UNRECOGNISED_LAYOUT: Band.DATA_DEFECT,
    # Data defect, terminology pass (P0-5): FR-71's own data-defect column
    # names "codes failing Verhoeff check-digit validation", "codes not
    # resolving in either edition" and "codes not subsumed by <<71388002"
    # explicitly. Each is a defect in the source RCPA-QAP must correct - no
    # repair the transform could make is deterministic, or even knowable.
    FindingCode.CODE_NOT_WELL_FORMED: Band.DATA_DEFECT,
    FindingCode.CODE_NOT_FOUND: Band.DATA_DEFECT,
    FindingCode.CODE_INACTIVE: Band.DATA_DEFECT,
    FindingCode.OUT_OF_SCOPE_HIERARCHY: Band.DATA_DEFECT,
    # FR-97's two blocking outcomes, named verbatim in FR-71's own data-defect
    # column: "stored text matching no designation on the concept, or
    # matching the FSN of a different concept". Both are the transcription
    # error PRD:856 calls "the most dangerous outcome" - a plausible label
    # paired with the wrong code - and both abort rather than repair, because
    # which half (the code or the label) is wrong cannot be decided
    # automatically.
    FindingCode.LABEL_BOUND_TO_OTHER_CONCEPT: Band.DATA_DEFECT,
    FindingCode.LABEL_MATCHES_NO_DESIGNATION: Band.DATA_DEFECT,
    # Informational: not a defect at all - see the module docstring.
    FindingCode.SHEET_NOT_SPIA_DATA: Band.INFORMATIONAL,
    # FR-99 is explicit that an unexpected semantic tag is a warning and not
    # an error, because subsumption does not imply the tag: 71388002
    # |Procedure| subsumes 243120004 |Regime/therapy (regime/therapy)|. A
    # blocking band here would abort the import over a concept that is a
    # perfectly valid procedure binding.
    FindingCode.UNEXPECTED_SEMANTIC_TAG: Band.INFORMATIONAL,
    # FR-71 is explicit that a published label which is merely a synonym or a
    # superseded FSN is *not* a data defect: "the catalogue lagging the
    # terminology is expected rather than defective". FR-97 has the transform
    # seed the served FSN and only tell someone.
    FindingCode.LABEL_DESIGNATION_DRIFT: Band.INFORMATIONAL,
    # FR-97's separate, always-informational list: the current AU preferred
    # term differs from the published label. Never blocking, regardless of
    # the axis-1 outcome for the same cell.
    FindingCode.LABEL_DIFFERS_FROM_PREFERRED_TERM: Band.INFORMATIONAL,
    # FR-79/H-04: both misspelling-heuristic codes are candidates for
    # editorial review only - never auto-corrections, never blocking. A
    # heuristic guess about spelling is not the kind of "value is already
    # lost or a data defect at source" claim the data-defect band makes.
    FindingCode.PROBABLE_MISSPELLING: Band.INFORMATIONAL,
    FindingCode.INCONSISTENT_SPELLING: Band.INFORMATIONAL,
    # FR-75/H-03: a semantic mismatch between the RCPA preferred term's own
    # specimen/timing wording and the bound concept's modelled Has specimen
    # value is a candidate for editorial review, never a confirmed defect -
    # PRD Annex A.9's own worked examples show roughly as many benign rows as
    # genuine ones, which makes a blocking band indefensible at that
    # false-positive rate (see ADR-0008).
    FindingCode.TERM_SPECIMEN_NOT_MODELLED: Band.INFORMATIONAL,
    FindingCode.TERM_SPECIMEN_DIFFERS: Band.INFORMATIONAL,
    FindingCode.TERM_TIMING_NOT_MODELLED: Band.INFORMATIONAL,
}

if set(BAND_BY_CODE) != set(FindingCode):
    # Every code this module declares must be classified - a code with no
    # band would defeat the one guarantee this module exists to provide.
    missing = set(FindingCode) - set(BAND_BY_CODE)
    raise AssertionError(f"FindingCode member(s) missing from BAND_BY_CODE: {missing}")


def band_for(code: str) -> Band:
    """Returns the band for ``code``.

    Falls back to ``Band.DATA_DEFECT`` - the most conservative band, since it
    blocks import - for a code this registry doesn't recognise, rather than
    raising. A finding must always resolve to exactly one band; failing safe
    here means a detector that emits an unregistered code blocks the import
    it should have blocked anyway, instead of silently passing as clean.
    """
    return BAND_BY_CODE.get(code, Band.DATA_DEFECT)
