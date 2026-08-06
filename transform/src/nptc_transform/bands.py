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
    NUMERIC_PRECISION_RISK = "NUMERIC_PRECISION_RISK"
    UNRECOGNISED_LAYOUT = "UNRECOGNISED_LAYOUT"
    SHEET_NOT_SPIA_DATA = "SHEET_NOT_SPIA_DATA"


BAND_BY_CODE: dict[str, Band] = {
    # Auto-correctable: FR-71 names these examples directly.
    FindingCode.INVISIBLE_CHARACTER: Band.AUTO_CORRECTABLE,
    FindingCode.SURROUNDING_WHITESPACE: Band.AUTO_CORRECTABLE,
    FindingCode.CODE_CELL_NOT_TEXT: Band.AUTO_CORRECTABLE,
    # Requires human decision: no deterministic repair exists (FR-70).
    FindingCode.INVISIBLE_CHARACTER_AMBIGUOUS: Band.REQUIRES_HUMAN_DECISION,
    FindingCode.WHITESPACE_ONLY_CELL: Band.REQUIRES_HUMAN_DECISION,
    # Data defect: the value is already lost, or the sheet's rows went unscanned.
    FindingCode.NUMERIC_PRECISION_RISK: Band.DATA_DEFECT,
    FindingCode.UNRECOGNISED_LAYOUT: Band.DATA_DEFECT,
    # Informational: not a defect at all - see the module docstring.
    FindingCode.SHEET_NOT_SPIA_DATA: Band.INFORMATIONAL,
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
