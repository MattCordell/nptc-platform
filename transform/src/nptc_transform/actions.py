"""FR-72's required-action registry: one operator-facing sentence or two per
``FindingCode``, stating who acts and what happens to the import.

Kept as its own module, not a dict inside ``bands.py``: that module's own
docstring stakes a precise claim ("this registry alone chooses the band"),
and burying it under 28 x 1-3 sentences of operator prose would obscure
``BAND_BY_CODE``. What's worth copying from ``bands.py`` is the *pattern* -
declare every code, assert completeness at import time, fail safe on an
unregistered code - not the location.

House style: imperative, names who acts (RCPA-QAP for a workbook correction,
a terminologist for an editorial-review candidate), and states what happens
to the import. A blocking band says the import is blocked until the cell is
corrected; a non-blocking band says **"no action required"** explicitly - an
editor scanning 200 findings needs to know what to skip as much as what to
fix. Every string is grounded in the runbook's own ``### Interpreting a X
finding`` sections (FR-97, FR-79, FR-75, FR-84) rather than inventing new
guidance, and is ASCII-only (NFR-38): this prose is copy-pasted from
Markdown into other tools, so no smart quotes or non-breaking spaces.
"""

from __future__ import annotations

from nptc_transform.bands import Band, FindingCode, band_for

ACTION_BY_CODE: dict[str, str] = {
    # Auto-correctable: FR-71 names these examples directly. Applied when
    # --emit-dataset writes the import dataset (P0-9); the import is not
    # blocked on any of them either way.
    FindingCode.INVISIBLE_CHARACTER: (
        "No action required. The transform normalises this invisible "
        "character to an ordinary space automatically. The import is not "
        "blocked."
    ),
    FindingCode.SURROUNDING_WHITESPACE: (
        "No action required. The transform strips the leading and/or "
        "trailing whitespace automatically. The import is not blocked."
    ),
    FindingCode.CODE_CELL_NOT_TEXT: (
        "No action required. The transform coerces this code cell to text, "
        "recovering the SCTID's digits exactly, automatically. The import "
        "is not blocked."
    ),
    FindingCode.EMPTY_SYNONYM_REMOVED: (
        "No action required. The transform removes the empty synonym a "
        "doubled delimiter produces automatically (FR-04). The import is "
        "not blocked."
    ),
    FindingCode.SPECIMEN_UNCONSTRAINED_RESOLVED: (
        "No action required. The transform records specimen_unconstrained "
        "and emits no specimen code for 'Any' automatically (FR-89). The "
        "import is not blocked."
    ),
    FindingCode.COMPOUND_VALUE_SPLIT: (
        "No action required. The transform splits this compound value into "
        "separate property values automatically (FR-90). The import is not "
        "blocked."
    ),
    # Requires human decision: no deterministic repair exists (FR-70).
    FindingCode.INVISIBLE_CHARACTER_AMBIGUOUS: (
        "RCPA-QAP must open the cell and decide the correct value: this "
        "character has no deterministic repair. The import is blocked until "
        "the cell is corrected at source."
    ),
    FindingCode.WHITESPACE_ONLY_CELL: (
        "Confirm whether the cell is meant to be empty or to hold a value, "
        "and set it explicitly. The transform will not decide on your behalf "
        "that whitespace means empty. The import is blocked until the cell "
        "is corrected at source."
    ),
    # Data defect: the value is already lost, was never valid, or the row
    # went unscanned - RCPA-QAP corrects the source, never the transform.
    FindingCode.CODE_CELL_INVALID_TYPE: (
        "RCPA-QAP must retype this cell as text holding the correct SCTID at "
        "source; no coercion exists to recover a valid code from a date, "
        "boolean, formula or error cell. The import is blocked until it is "
        "corrected."
    ),
    FindingCode.NUMERIC_PRECISION_RISK: (
        "RCPA-QAP must re-enter this cell as text holding the correct, "
        "full-precision SCTID at source; Excel has already corrupted the "
        "stored digits. The import is blocked until it is corrected."
    ),
    FindingCode.UNRECOGNISED_LAYOUT: (
        "RCPA-QAP must restore this sheet's published header row so the "
        "transform can find the code column; every row on this sheet went "
        "unscanned as a result. The import is blocked until the layout is "
        "corrected."
    ),
    FindingCode.CODE_NOT_WELL_FORMED: (
        "RCPA-QAP must correct this cell to a well-formed 6-18 digit SCTID "
        "with a valid Verhoeff check digit at source. The import is blocked "
        "until it is corrected."
    ),
    FindingCode.CODE_NOT_FOUND: (
        "RCPA-QAP must rebind this cell to a code that resolves in at least "
        "one validated edition, or correct the transcription error. The "
        "import is blocked until it is corrected."
    ),
    FindingCode.CODE_INACTIVE: (
        "RCPA-QAP must rebind this cell to an active concept; an inactive "
        "concept must not be published as a binding. The import is blocked "
        "until it is corrected."
    ),
    FindingCode.OUT_OF_SCOPE_HIERARCHY: (
        "RCPA-QAP must rebind this cell to a concept subsumed by 71388002 "
        "(Procedure), or document why the exception is justified (FR-84). "
        "The import is blocked until it is resolved."
    ),
    FindingCode.LABEL_BOUND_TO_OTHER_CONCEPT: (
        "RCPA-QAP must check both the code and the label against each "
        "other: one is a transcription error pairing the wrong code with the "
        "right label, or the reverse (FR-97). The import is blocked until it "
        "is corrected at source."
    ),
    FindingCode.LABEL_MATCHES_NO_DESIGNATION: (
        "RCPA-QAP must correct the published label at source; it matches no "
        "designation of the bound code, or of any other code bound elsewhere "
        "in this workbook (FR-97). The import is blocked until it is "
        "corrected."
    ),
    FindingCode.MISSING_PREFERRED_TERM: (
        "RCPA-QAP must supply the 'RCPA Preferred term' value for this row at "
        "source; no entry can be seeded without one. The import is blocked "
        "until it is corrected."
    ),
    # Informational: not a defect at all - see bands.py's module docstring.
    FindingCode.SHEET_NOT_SPIA_DATA: (
        "No action required. This sheet is recognised as prose, not SPIA "
        "data, and was not scanned. The import is not blocked."
    ),
    FindingCode.UNEXPECTED_SEMANTIC_TAG: (
        "No action required. Subsumption does not imply the tag (FR-99); "
        "review the served FSN in context if the tag is unexpected. The "
        "import is not blocked."
    ),
    FindingCode.LABEL_DESIGNATION_DRIFT: (
        "No action required. Server-sourced FSN seeding is deferred (ADR-0010); "
        "the published label is seeded as-is, and the drift is recorded for "
        "editorial review only if unexpected (FR-97). The import is not blocked."
    ),
    FindingCode.LABEL_DIFFERS_FROM_PREFERRED_TERM: (
        "No action required. The current SNOMED CT-AU preferred term "
        "differs from the published label; review only if the drift is "
        "unexpected (FR-97, FR-82). The import is not blocked."
    ),
    FindingCode.PROBABLE_MISSPELLING: (
        "No action required to proceed. A terminologist should review the "
        "flagged token against the cited in-entry reference and correct it "
        "manually if it is genuinely a misspelling; the transform never "
        "auto-corrects it (FR-79). The import is not blocked."
    ),
    FindingCode.INCONSISTENT_SPELLING: (
        "No action required to proceed. A terminologist should review the "
        "flagged token against the cited corpus-common spelling and correct "
        "it manually if it is genuinely inconsistent; the transform never "
        "auto-corrects it (FR-79). The import is not blocked."
    ),
    FindingCode.TERM_SPECIMEN_NOT_MODELLED: (
        "No action required to proceed. A terminologist should review "
        "whether the bound concept ought to model the asserted specimen "
        "(FR-75); this is a candidate for editorial review, not a confirmed "
        "defect. The import is not blocked."
    ),
    FindingCode.TERM_SPECIMEN_DIFFERS: (
        "No action required to proceed. A terminologist should review "
        "whether the bound concept's modelled specimen agrees with the one "
        "asserted by the term (FR-75); this is a candidate for editorial "
        "review, not a confirmed defect. The import is not blocked."
    ),
    FindingCode.TERM_TIMING_NOT_MODELLED: (
        "No action required to proceed. A terminologist should review "
        "whether the asserted timing is genuinely unmodelled (FR-75); this "
        "is a candidate for editorial review, not a confirmed defect. The "
        "import is not blocked."
    ),
    FindingCode.SPECIMEN_VALUE_UNMAPPED: (
        "No action required to proceed. A terminologist should review "
        "whether this specimen value should be added to the specimen "
        "table (FR-88); it is seeded with no specimen code in the meantime. "
        "The import is not blocked."
    ),
}

if set(ACTION_BY_CODE) != set(FindingCode):
    # Same import-time completeness assert as BAND_BY_CODE - a code with no
    # required action would defeat FR-72's own acceptance criterion.
    missing = set(FindingCode) - set(ACTION_BY_CODE)
    raise AssertionError(f"FindingCode member(s) missing from ACTION_BY_CODE: {missing}")

_ACTION_BY_BAND: dict[Band, str] = {
    Band.AUTO_CORRECTABLE: (
        "No action required. The transform corrects this automatically. The import is not blocked."
    ),
    Band.REQUIRES_HUMAN_DECISION: (
        "RCPA-QAP must open the cell and decide the correct value; no "
        "deterministic repair exists. The import is blocked until the cell "
        "is corrected at source."
    ),
    Band.DATA_DEFECT: (
        "RCPA-QAP must correct this at source; the transform cannot recover "
        "or infer a valid value. The import is blocked until it is "
        "corrected."
    ),
    Band.INFORMATIONAL: (
        "No action required. This is not a defect; review at your "
        "discretion. The import is not blocked."
    ),
}

if set(_ACTION_BY_BAND) != set(Band):
    missing_bands = set(Band) - set(_ACTION_BY_BAND)
    raise AssertionError(f"Band member(s) missing from _ACTION_BY_BAND: {missing_bands}")


def action_for(code: str) -> str:
    """The required-action sentence(s) for ``code``. Never returns "".

    Falls back to the band's own fallback text for a code this registry
    doesn't recognise - the same fail-safe shape ``bands.band_for`` uses: an
    unregistered code already resolves to ``Band.DATA_DEFECT``, so the reader
    still gets "fix at source, the import is blocked" rather than a blank
    action line under a heading, which is precisely the FR-72 failure this
    fallback guards against.
    """
    return ACTION_BY_CODE.get(code, _ACTION_BY_BAND[band_for(code)])
