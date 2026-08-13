"""Detects PRD Appendix A.1-A.3 cell-level defects and turns them into findings.

Detection only - nothing here corrects a value. Each defect is reported under
one of two codes chosen here, by shape, so that ``bands.band_for`` can assign
a severity band from the code alone without inspecting content (FR-70,
FR-71): an invisible character normalises deterministically to a space
(auto-correctable) or it doesn't (requires a human decision); a whitespace-
padded cell strips deterministically to its content (auto-correctable) or
stripping empties it entirely (requires a human decision); a non-text code
cell holding a number coerces deterministically to a string (auto-
correctable) or holds a date/boolean/formula/error, for which no coercion to
a valid SCTID exists (data defect).

The structural scans this module added for P0-9/#31 (``_scan_empty_synonym``,
``_scan_compound_value``, ``_scan_specimen``) split ``cell.text`` after
running it through ``corrections.apply_corrections`` first, the same
normalisation ``dataset.py`` applies before splitting the same cell for
emission. Splitting the raw text here while emission splits the corrected
text would let an interior invisible character make the two disagree about
how many values a cell holds - report-only claiming one outcome,
``--emit-dataset`` producing another for the identical cell.
"""

from __future__ import annotations

import math
import re

from nptc_shared.sctid import has_valid_check_digit
from nptc_shared.text import (
    escape_invisible,
    find_invisible_characters,
    has_surrounding_whitespace,
)
from nptc_transform.bands import FindingCode
from nptc_transform.cellref import CellRef
from nptc_transform.corrections import apply_corrections
from nptc_transform.findings import Finding
from nptc_transform.rows import SourceRow, group_rows
from nptc_transform.specimen_table import SPECIMEN_TABLE, SpecimenGroup
from nptc_transform.workbook import Cell, CellType, ColumnRole, Sheet, column_role

# PRD §2.1: "any SCTID of 16 digits or more entered into a numeric cell is
# silently corrupted" - a 15-digit value is exactly representable (Excel's
# ceiling is 15 significant decimal digits), so the finding fires at 16, not
# at the ceiling itself.
NUMERIC_PRECISION_RISK_THRESHOLD = 16

# ALT+ENTER produces a literal U+000A inside a cell's own text - legitimate
# multi-line formatting in these two free-text roles (FR-63's ``Usage
# guidance`` and ``History`` columns), not an Appendix A.1 defect. U+000D is
# exempted alongside it for the same reason (a Windows-origin paste can leave
# a bare \r or a \r\n pair). Scoped to these two roles, not to
# ``nptc_shared.text.is_invisible`` itself: a line break inside a preferred
# term, FSN or code cell is never legitimate, and that module is shared with
# the backend's entry-time prohibition (FR-74), which must not lose the
# ability to catch it.
_FREE_TEXT_ROLES = frozenset({ColumnRole.GUIDANCE, ColumnRole.HISTORY})
_LEGITIMATE_LINE_BREAKS = frozenset({"U+000A", "U+000D"})

# FR-63 documents exactly one worksheet, by this exact name, that is
# hand-written prose rather than SPIA data. Resolving zero SPIA column roles
# is not itself evidence a sheet isn't SPIA data - a data sheet whose FR-63
# header row has drifted entirely (for example, an inserted banner row above
# it) produces the identical signal - so this positive allowlist, not the
# absence of a recognised column, is what gates the informational band.
# Anything else that resolves zero roles is unrecognised layout and blocks.
_NON_SPIA_DATA_SHEET_NAMES = frozenset({"Rev History"})

# FR-04/P0-9: FR-63's documented delimiter is a semicolon; comma-space is
# accepted as a fallback only when no semicolon is present at all, since at
# least one published row (PRD Appendix A.10's "ADA RBC, ADA red cells")
# uses it as its sole delimiter. The fallback requires the space - a bare
# comma with no following space is ordinary SPIA vocabulary (e.g.
# "1,25-dihydroxyvitamin D"), not a delimiter, and splitting on it would
# shatter a real analyte name into two bogus designations. Plain ``str.split``
# (not a regex collapsing runs of delimiters) is deliberate: a doubled
# delimiter must produce an empty part here so ``has_empty_synonym_part`` can
# detect it, not be silently absorbed before detection ever runs.
_SYNONYM_DELIMITER = ";"
_SYNONYM_FALLBACK_DELIMITER = ", "

# FR-90: "X or Y" is the only compound form the published Discipline/Subgroup
# columns use.
_COMPOUND_VALUE_RE = re.compile(r"\s+or\s+", re.IGNORECASE)

# FR-88: the Specimen column's own documented delimiter, replaced by
# cardinality 0..* in the import dataset.
_SPECIMEN_DELIMITER = ";"

# FR-89: the one specimen value that must never resolve to a specimen code.
_SPECIMEN_ANY = "any"

#: Every ``SpecimenGroup.terms`` surface form, casefolded, to the group it
#: names - an *exact*-match index, deliberately not the word-boundary
#: substring matching ``semantic_drift.py`` uses for its own free-text
#: heuristic: seeding a specimen *code* requires certainty a heuristic
#: cannot provide (see the module docstring's "verbatim always, code only
#: where certain" precedent, FR-88/FR-92).
_SPECIMEN_TERMS_TO_GROUP: dict[str, SpecimenGroup] = {
    term: group for group in SPECIMEN_TABLE for term in group.terms
}


def split_synonyms(text: str) -> tuple[str, ...]:
    """Splits a ``RCPA Synonyms`` cell into individual designation values (FR-04).

    Empty parts produced by a doubled delimiter (``'Zovirax;;Cyclir'``) are
    dropped silently here - ``has_empty_synonym_part`` is the function that
    records one was found, so the two stay independently testable and the
    emitted dataset never carries an empty designation.
    """
    delimiter = _SYNONYM_DELIMITER if _SYNONYM_DELIMITER in text else _SYNONYM_FALLBACK_DELIMITER
    parts = (part.strip() for part in text.split(delimiter))
    return tuple(part for part in parts if part)


def has_empty_synonym_part(text: str) -> bool:
    """True if splitting ``text`` on its synonym delimiter produces an empty
    part - a doubled delimiter, or one with only whitespace between the two.
    """
    delimiter = _SYNONYM_DELIMITER if _SYNONYM_DELIMITER in text else _SYNONYM_FALLBACK_DELIMITER
    return any(not part.strip() for part in text.split(delimiter))


def split_compound_value(text: str) -> tuple[str, ...]:
    """Splits a ``Discipline``/``Subgroup`` cell on ``'X or Y'`` (FR-90)."""
    parts = (part.strip() for part in _COMPOUND_VALUE_RE.split(text))
    return tuple(part for part in parts if part)


def split_specimen_values(text: str) -> tuple[str, ...]:
    """Splits a ``Specimen`` cell into individual asserted values (FR-88)."""
    parts = (part.strip() for part in text.split(_SPECIMEN_DELIMITER))
    return tuple(part for part in parts if part)


def resolve_specimen_term(value: str) -> SpecimenGroup | None:
    """The ``SpecimenGroup`` ``value`` names by an *exact*, casefolded match
    against ``specimen_table.SPECIMEN_TABLE``'s own surface forms, or
    ``None`` if it names none of them (``SPECIMEN_VALUE_UNMAPPED``, FR-88).
    """
    return _SPECIMEN_TERMS_TO_GROUP.get(value.strip().casefold())


def resolves_code_column(sheet: Sheet) -> bool:
    """True if ``sheet``'s header row resolves the code column.

    The same gate ``_scan_layout`` uses to decide a sheet gets cell-level
    scanning at all - exported so ``dataset.py`` can restrict entry-building
    to the identical set of sheets, rather than a second, independently
    drifting notion of "this sheet is SPIA data".
    """
    roles = {column_role(header) for header in sheet.headers} - {ColumnRole.UNKNOWN}
    return ColumnRole.CODE in roles


def _digit_count(value: int) -> int:
    """Counts the digits in ``abs(value)``. Assumes a finite value - callers
    that may see a non-finite float (see ``_scan_numeric_precision_risk``)
    check that first."""
    return len(str(abs(value)))


def _scan_invisible_characters(cell: Cell) -> tuple[Finding, ...]:
    found = find_invisible_characters(cell.text)
    if cell.role in _FREE_TEXT_ROLES:
        found = tuple(ic for ic in found if ic.codepoint not in _LEGITIMATE_LINE_BREAKS)
    if not found:
        return ()

    header = escape_invisible(cell.header)
    findings = []
    for code, group in (
        (FindingCode.INVISIBLE_CHARACTER, tuple(ic for ic in found if ic.normalisable)),
        (
            FindingCode.INVISIBLE_CHARACTER_AMBIGUOUS,
            tuple(ic for ic in found if not ic.normalisable),
        ),
    ):
        if not group:
            continue
        detail = ", ".join(f"{ic.codepoint} ({ic.name}) at offset {ic.offset}" for ic in group)
        findings.append(
            Finding(
                code=code,
                location=cell.reference,
                message=f"'{header}' cell contains invisible character(s): {detail}",
            )
        )
    return tuple(findings)


def _scan_surrounding_whitespace(cell: Cell) -> Finding | None:
    if not has_surrounding_whitespace(cell.text):
        return None
    header = escape_invisible(cell.header)
    if not cell.text.strip():
        return Finding(
            code=FindingCode.WHITESPACE_ONLY_CELL,
            location=cell.reference,
            message=f"'{header}' cell contains only whitespace",
        )
    edges = []
    if cell.text != cell.text.lstrip():
        edges.append("leading")
    if cell.text != cell.text.rstrip():
        edges.append("trailing")
    message = f"'{header}' cell has {' and '.join(edges)} whitespace"
    return Finding(
        code=FindingCode.SURROUNDING_WHITESPACE, location=cell.reference, message=message
    )


def _scan_code_cell_type(cell: Cell) -> Finding | None:
    if cell.role is not ColumnRole.CODE or cell.cell_type is CellType.TEXT:
        return None
    if cell.cell_type is CellType.NUMBER:
        # The only case FR-71 names as auto-correctable: the digits are
        # intact (unless NUMERIC_PRECISION_RISK also fires on this cell,
        # which blocks the row regardless), so coercing to a string
        # deterministically recovers the SCTID.
        return Finding(
            code=FindingCode.CODE_CELL_NOT_TEXT,
            location=cell.reference,
            message=f"code cell stored as {cell.cell_type.value}, not text (FR-06)",
        )
    # A date, boolean, formula or error in the code column has no
    # deterministic coercion to a valid SCTID string - unlike a number, there
    # is no value to recover, only a wrong one to report (FR-06, FR-70).
    return Finding(
        code=FindingCode.CODE_CELL_INVALID_TYPE,
        location=cell.reference,
        message=(
            f"code cell stored as {cell.cell_type.value}, not text (FR-06); "
            "no deterministic coercion to a valid SCTID exists for this cell type"
        ),
    )


def _scan_code_well_formed(cell: Cell) -> Finding | None:
    """Reports a text-typed code cell whose corrected text isn't a
    well-formed SCTID (FR-06), so ``--emit-dataset`` alone (no
    ``--check-terminology``) never seeds an unvalidated code.

    Checks ``apply_corrections(cell.text)`` - the same text ``dataset.py``
    seeds - not the raw text, so an interior invisible character that
    collapses to a space (e.g. a code with an interior NBSP) is caught here
    rather than slipping through as a "well-formed" cell. The message still
    quotes the *raw*, escaped text: the corrected form is what the check
    runs against, but it is the raw form an operator has to find in the
    workbook and fix.

    Only for ``CellType.TEXT``: a NUMBER or other-typed code cell is already
    reported by ``_scan_code_cell_type`` (``CODE_CELL_NOT_TEXT`` or
    ``CODE_CELL_INVALID_TYPE``), and checking well-formedness there too would
    add a second, redundant finding for the same cell.

    Never called on a cell that's blank once stripped (that's
    ``WHITESPACE_ONLY_CELL``, a different, requires-human-decision defect -
    #132 owns deciding whether an absent code should also be
    ``CODE_NOT_WELL_FORMED``) or one carrying a non-normalisable invisible
    character (``INVISIBLE_CHARACTER_AMBIGUOUS`` already blocks emission,
    and the code may well be well-formed once that character is resolved -
    reporting both asks for two different remediations on one cell).
    """
    if cell.role is not ColumnRole.CODE or cell.cell_type is not CellType.TEXT:
        return None
    raw = cell.text.strip()
    if not raw:
        return None
    if any(not ic.normalisable for ic in find_invisible_characters(cell.text)):
        return None
    if has_valid_check_digit(apply_corrections(cell.text)):
        return None
    return Finding(
        code=FindingCode.CODE_NOT_WELL_FORMED,
        location=cell.reference,
        message=(
            f"code '{escape_invisible(raw)}' is not a well-formed SCTID "
            "(6-18 digits with a valid Verhoeff check digit, FR-06)"
        ),
    )


def _scan_numeric_precision_risk(cell: Cell) -> Finding | None:
    if cell.cell_type is not CellType.NUMBER:
        return None
    # A NUMBER-typed cell's raw value is always int or float - that mapping is
    # workbook.py's own contract (_DATA_TYPE_TO_CELL_TYPE / _cell_type).
    assert isinstance(cell.raw, int | float)
    header = escape_invisible(cell.header)

    if isinstance(cell.raw, float) and not math.isfinite(cell.raw):
        # openpyxl's _cast_number returns inf without raising for a numeric
        # cell's raw XML text that overflows a double (e.g. "1E400") - the
        # original number is unrecoverable, so say that rather than
        # fabricating a digit count for a value that isn't a number.
        return Finding(
            code=FindingCode.NUMERIC_PRECISION_RISK,
            location=cell.reference,
            message=f"'{header}' cell holds a value beyond Excel's numeric range",
        )

    digits = _digit_count(int(cell.raw))
    if digits < NUMERIC_PRECISION_RISK_THRESHOLD:
        return None
    return Finding(
        code=FindingCode.NUMERIC_PRECISION_RISK,
        location=cell.reference,
        message=(
            f"'{header}' cell holds a {digits}-digit number; "
            "Excel corrupts a numeric cell at 16 or more significant digits"
        ),
    )


def _scan_empty_synonym(cell: Cell) -> Finding | None:
    if cell.role is not ColumnRole.SYNONYMS or not has_empty_synonym_part(
        apply_corrections(cell.text)
    ):
        return None
    header = escape_invisible(cell.header)
    return Finding(
        code=FindingCode.EMPTY_SYNONYM_REMOVED,
        location=cell.reference,
        message=(
            f"'{header}' cell has a doubled delimiter; the empty synonym it "
            "produces is removed (FR-04)"
        ),
    )


def _scan_compound_value(cell: Cell) -> Finding | None:
    if cell.role not in (ColumnRole.DISCIPLINE, ColumnRole.SUBGROUP):
        return None
    parts = split_compound_value(apply_corrections(cell.text))
    if len(parts) <= 1:
        return None
    header = escape_invisible(cell.header)
    return Finding(
        code=FindingCode.COMPOUND_VALUE_SPLIT,
        location=cell.reference,
        message=(
            f"'{header}' cell holds a compound value '{escape_invisible(cell.text)}'; "
            f"split into {len(parts)} values (FR-90)"
        ),
    )


def _scan_specimen(cell: Cell) -> tuple[Finding, ...]:
    if cell.role is not ColumnRole.SPECIMEN:
        return ()
    header = escape_invisible(cell.header)
    findings: list[Finding] = []
    for value in split_specimen_values(apply_corrections(cell.text)):
        if value.casefold() == _SPECIMEN_ANY:
            findings.append(
                Finding(
                    code=FindingCode.SPECIMEN_UNCONSTRAINED_RESOLVED,
                    location=cell.reference,
                    message=(
                        f"'{header}' cell value 'Any' resolves to specimen_unconstrained; "
                        "no specimen code is ever emitted for it (FR-89)"
                    ),
                )
            )
            continue
        if resolve_specimen_term(value) is None:
            findings.append(
                Finding(
                    code=FindingCode.SPECIMEN_VALUE_UNMAPPED,
                    location=cell.reference,
                    message=(
                        f"'{header}' cell value '{escape_invisible(value)}' matches no "
                        "entry in the specimen table; seeded verbatim with no specimen "
                        "code (FR-88)"
                    ),
                )
            )
    return tuple(findings)


def _scan_missing_preferred_term(row: SourceRow) -> Finding | None:
    """A row that resolves a code binding but carries no 'RCPA Preferred
    term' value has nothing to seed a designation from at all (P0-9/#31).

    Row-level, not cell-level: there is no single defective cell to point
    at, since the defect is the *absence* of one. Reported against the code
    cell's own reference, the only cell on the row this check can be certain
    exists - never silently dropping the row the way ``build_dataset`` did
    before this code existed (ADR-0010 §8's own "nothing downstream can
    distinguish 'every row is clean' from 'some rows were silently dropped'"
    concern, applied to the report itself, not only to the dataset).
    """
    if ColumnRole.CODE not in row.cells or ColumnRole.PREFERRED_TERM in row.cells:
        return None
    code_cell = row.cells[ColumnRole.CODE]
    return Finding(
        code=FindingCode.MISSING_PREFERRED_TERM,
        location=code_cell.reference,
        message="row has a code binding but no 'RCPA Preferred term' value; no entry can be "
        "seeded for this row",
    )


def _scan_cell(cell: Cell) -> tuple[Finding, ...]:
    findings: list[Finding] = list(_scan_invisible_characters(cell))
    findings.extend(
        finding
        for finding in (
            _scan_surrounding_whitespace(cell),
            _scan_code_cell_type(cell),
            _scan_code_well_formed(cell),
            _scan_numeric_precision_risk(cell),
            _scan_empty_synonym(cell),
            _scan_compound_value(cell),
        )
        if finding is not None
    )
    findings.extend(_scan_specimen(cell))
    return tuple(findings)


def _scan_layout(sheet: Sheet) -> Finding | None:
    """Reports a sheet the code column can't be found on - splitting *why*.

    Only a sheet named in ``_NON_SPIA_DATA_SHEET_NAMES`` (FR-63's own ``Rev
    History`` worksheet is the sole documented case: hand-written prose, not
    SPIA data) that also resolves zero SPIA column roles is treated as not
    being SPIA data - reported so an operator can see the sheet was skipped,
    but not blocking. Every other sheet that fails to resolve the code
    column - whether it resolves some SPIA columns and not the code column,
    or resolves none at all - has drifted (FR-63) and every one of its rows
    went unscanned for Appendix A.2/A.3 defects; that's the case FR-71's
    data-defect band exists for, so the message says how many rows were
    skipped rather than letting a low ``finding_count`` read as "nearly
    clean".
    """
    if not sheet.cells:
        return None
    roles = {column_role(header) for header in sheet.headers} - {ColumnRole.UNKNOWN}
    if ColumnRole.CODE in roles:
        return None
    headers_text = ", ".join(escape_invisible(header) for header in sheet.headers) or "(no headers)"
    unscanned_rows = len({cell.row for cell in sheet.cells})
    if not roles and sheet.name in _NON_SPIA_DATA_SHEET_NAMES:
        return Finding(
            code=FindingCode.SHEET_NOT_SPIA_DATA,
            location=CellRef(sheet.name, "A", 1),
            message=(
                f"no column recognised as SPIA data; {unscanned_rows} data row(s) on "
                f"this sheet were not scanned; headers were: {headers_text}"
            ),
        )
    return Finding(
        code=FindingCode.UNRECOGNISED_LAYOUT,
        location=CellRef(sheet.name, "A", 1),
        message=(
            f"no column recognised as the code column; {unscanned_rows} data row(s) on "
            f"this sheet were not scanned for cell defects; headers were: {headers_text}"
        ),
    )


def scan_workbook(sheets: tuple[Sheet, ...]) -> tuple[Finding, ...]:
    """Scans every sheet's cells for PRD Appendix A.1-A.3 defects.

    Only a sheet that resolves a code column gets cell-level scanning - a
    sheet that doesn't already gets exactly one finding from ``_scan_layout``
    (``SHEET_NOT_SPIA_DATA`` or ``UNRECOGNISED_LAYOUT``, depending on whether
    it looks like SPIA data at all), and scanning its cells for A.1/A.3 would
    just add noise, not defects. Every sheet that *does* also gets one
    row-level pass (``_scan_missing_preferred_term``, P0-9/#31) - a defect
    that is the absence of a cell, not the content of one, so it cannot be
    found by ``_scan_cell`` iterating cells alone.
    """
    findings: list[Finding] = []
    codeable_sheets: list[Sheet] = []
    for sheet in sheets:
        layout_finding = _scan_layout(sheet)
        if layout_finding is not None:
            findings.append(layout_finding)
            continue
        codeable_sheets.append(sheet)
        for cell in sheet.cells:
            findings.extend(_scan_cell(cell))
    for row in group_rows(codeable_sheets):
        row_finding = _scan_missing_preferred_term(row)
        if row_finding is not None:
            findings.append(row_finding)
    return tuple(findings)
