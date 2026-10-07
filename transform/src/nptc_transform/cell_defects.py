"""Detects PRD Appendix A.1-A.3 cell-level defects and turns them into findings.

Detection only: nothing here corrects a value. Each defect is reported under one
of two codes chosen by shape, so ``bands.band_for`` can assign a severity band
from the code alone (FR-70, FR-71):

- an invisible character normalises to a space (auto-correctable) or has no
  single repair (needs a human decision);
- a whitespace-padded cell strips to its content (auto-correctable) or strips to
  nothing (needs a human decision);
- a non-text code cell holding a number coerces to a string (auto-correctable),
  while a date, boolean, formula or error has no coercion to a valid SCTID
  (data defect).

The structural scans (``_scan_empty_synonym``, ``_scan_compound_value``,
``_scan_specimen``) split ``cell.text`` after ``corrections.apply_corrections``,
the normalisation ``dataset.py`` applies before splitting the same cell for
emission. Splitting raw text here would let an interior invisible character make
the two disagree on how many values a cell holds, so report-only would claim one
outcome and ``--emit-dataset`` produce another.
"""

from __future__ import annotations

import math
import re

from nptc_shared.sctid import has_valid_check_digit
from nptc_shared.terminology.models import SPECIMEN_ROOT_CODE
from nptc_shared.text import (
    escape_invisible,
    find_invisible_characters,
    has_surrounding_whitespace,
)
from nptc_transform.bands import FindingCode
from nptc_transform.cellref import CellRef
from nptc_transform.corrections import apply_corrections
from nptc_transform.findings import Finding
from nptc_transform.rows import SourceRow, group_rows, has_code_binding
from nptc_transform.specimen_map import SPECIMEN_MAP, SpecimenMapEntry
from nptc_transform.workbook import Cell, CellType, ColumnRole, Sheet, column_role

# PRD §2.1: "any SCTID of 16 digits or more entered into a numeric cell is
# silently corrupted". Excel keeps 15 significant digits exactly, so the finding
# fires at 16, not at the ceiling.
NUMERIC_PRECISION_RISK_THRESHOLD = 16

# ALT+ENTER puts a literal U+000A in a cell: legitimate multi-line formatting in
# these two free-text roles (FR-63's ``Usage guidance`` and ``History``), not an
# Appendix A.1 defect. U+000D is exempt too, because a Windows-origin paste can
# leave a bare \r or a \r\n pair. Scoped to these roles, not to
# ``nptc_shared.text.is_invisible``: a line break in a preferred term, FSN or
# code cell is never legitimate, and the backend's entry-time prohibition
# (FR-74) shares that module and must still catch it.
_FREE_TEXT_ROLES = frozenset({ColumnRole.GUIDANCE, ColumnRole.HISTORY})
_LEGITIMATE_LINE_BREAKS = frozenset({"U+000A", "U+000D"})

# FR-63 documents exactly one worksheet, by this name, that is hand-written prose
# rather than SPIA data. Resolving zero SPIA column roles is not evidence of that,
# because a data sheet whose header row has drifted entirely (an inserted banner
# row, say) gives the same signal. This allowlist, not the absence of a
# recognised column, gates the informational band; any other sheet that resolves
# zero roles is unrecognised layout and blocks.
_NON_SPIA_DATA_SHEET_NAMES = frozenset({"Rev History"})

# FR-04: FR-63's delimiter is a semicolon. Comma-space is a fallback only when no
# semicolon is present, because a published row (PRD Appendix A.10's "ADA RBC,
# ADA red cells") uses it alone. The fallback needs the space: a bare comma is
# ordinary SPIA vocabulary ("1,25-dihydroxyvitamin D"), and splitting on it would
# shatter an analyte name. Plain ``str.split``, not a regex collapsing runs, is
# deliberate: a doubled delimiter must leave an empty part for
# ``has_empty_synonym_part`` to detect.
_SYNONYM_DELIMITER = ";"
_SYNONYM_FALLBACK_DELIMITER = ", "

# FR-90: "X or Y" is the only compound form the published Discipline/Subgroup
# columns use.
_COMPOUND_VALUE_RE = re.compile(r"\s+or\s+", re.IGNORECASE)

# FR-88: the Specimen column's own documented delimiter, replaced by
# cardinality 0..* in the import dataset.
_SPECIMEN_DELIMITER = ";"


def split_synonyms(text: str) -> tuple[str, ...]:
    """Splits a ``RCPA Synonyms`` cell into individual designation values (FR-04).

    Empty parts from a doubled delimiter (``'Zovirax;;Cyclir'``) are dropped
    here. ``has_empty_synonym_part`` records that one was found, so the two stay
    independently testable and the dataset never carries an empty designation.
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


def resolve_specimen_term(value: str) -> SpecimenMapEntry | None:
    """The reviewed-map row ``value`` names, or ``None`` if the map does not cover it
    (``SPECIMEN_VALUE_UNMAPPED``). Equality after trimming and casefolding, never a
    heuristic: seeding a specimen *code* needs certainty (FR-88, ADR-0044).
    """
    return SPECIMEN_MAP.resolve(value)


def _digit_count(value: int) -> int:
    """Counts the digits in ``abs(value)``. Assumes a finite value; callers that
    may see a non-finite float (``_scan_numeric_precision_risk``) check first."""
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
        # The one case FR-71 names as auto-correctable: the digits are intact
        # (unless NUMERIC_PRECISION_RISK also fires, which blocks the row), so
        # coercing to a string recovers the SCTID.
        return Finding(
            code=FindingCode.CODE_CELL_NOT_TEXT,
            location=cell.reference,
            message=f"code cell stored as {cell.cell_type.value}, not text (FR-06)",
        )
    # A date, boolean, formula or error has no coercion to a valid SCTID: unlike a
    # number, there is no value to recover (FR-06, FR-70).
    return Finding(
        code=FindingCode.CODE_CELL_INVALID_TYPE,
        location=cell.reference,
        message=(
            f"code cell stored as {cell.cell_type.value}, not text (FR-06); "
            "no deterministic coercion to a valid SCTID exists for this cell type"
        ),
    )


def _scan_code_well_formed(cell: Cell) -> Finding | None:
    """Reports a text-typed code cell whose corrected text isn't a well-formed
    SCTID (FR-06), so ``--emit-dataset`` without ``--check-terminology`` never
    seeds an unvalidated code.

    Checks ``apply_corrections(cell.text)``, the text ``dataset.py`` seeds, so an
    interior invisible character that collapses to a space (a code with an
    interior NBSP) is caught. The message quotes the *raw*, escaped text, because
    that is what an operator has to find in the workbook.

    Only for ``CellType.TEXT``: ``_scan_code_cell_type`` already reports a NUMBER
    or other-typed code cell, and a second finding would be redundant.

    Skips a cell that is blank once stripped (``WHITESPACE_ONLY_CELL``, a
    different needs-a-human defect) and one carrying a non-normalisable invisible
    character (``INVISIBLE_CHARACTER_AMBIGUOUS`` already blocks emission, and the
    code may be well-formed once that is resolved; reporting both asks for two
    remedies on one cell).
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
    # A NUMBER-typed cell's raw value is always int or float: that is workbook.py's
    # contract (_DATA_TYPE_TO_CELL_TYPE / _cell_type).
    assert isinstance(cell.raw, int | float)
    header = escape_invisible(cell.header)

    if isinstance(cell.raw, float) and not math.isfinite(cell.raw):
        # openpyxl's _cast_number returns inf, without raising, for raw XML text
        # that overflows a double (e.g. "1E400"). The number is unrecoverable, so
        # say so rather than invent a digit count.
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
    coded: list[tuple[str, str]] = []
    for value in split_specimen_values(apply_corrections(cell.text)):
        entry = resolve_specimen_term(value)
        shown = escape_invisible(value)
        if entry is None:
            findings.append(
                Finding(
                    code=FindingCode.SPECIMEN_VALUE_UNMAPPED,
                    location=cell.reference,
                    message=(
                        f"'{header}' cell value '{shown}' is not in the reviewed specimen "
                        "map; add it to the map with a verified code, or correct the "
                        "workbook value (FR-88)"
                    ),
                )
            )
        elif entry.code is None:
            findings.append(
                Finding(
                    code=FindingCode.SPECIMEN_VALUE_NO_EQUIVALENT,
                    location=cell.reference,
                    message=(
                        f"'{header}' cell value '{shown}' is marked as needing no specimen "
                        "in the specimen map; no specimen is seeded for it (FR-88)"
                    ),
                )
            )
        else:
            coded.append((shown, entry.code))
    roots = [shown for shown, code in coded if code == SPECIMEN_ROOT_CODE]
    named = [shown for shown, code in coded if code != SPECIMEN_ROOT_CODE]
    if roots and named:
        findings.append(
            Finding(
                code=FindingCode.SPECIMEN_ROOT_WITH_OTHERS,
                location=cell.reference,
                message=(
                    f"'{header}' cell value {_quoted(roots)} maps to {SPECIMEN_ROOT_CODE} "
                    f"(any specimen), which must stand alone, but the cell also lists "
                    f"{_quoted(named)} (FR-89)"
                ),
            )
        )
    return tuple(findings)


def _quoted(values: list[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _row_has_code(row: SourceRow) -> bool:
    """Both row-level scans below share this test, so for any row both fire or neither does."""
    return has_code_binding(row.cells)


def _scan_missing_preferred_term(row: SourceRow) -> Finding | None:
    """A row that resolves a code binding but carries no 'RCPA Preferred term'
    value has nothing to seed a designation from.

    Row-level, not cell-level: the defect is the *absence* of a cell. Reported
    against the code cell, the only cell on the row known to exist, so the row is
    never silently dropped. ADR-0010 §8's concern applies to the report as well as
    the dataset: nothing downstream can tell "every row is clean" from "some rows
    were silently dropped".
    """
    if not _row_has_code(row) or ColumnRole.PREFERRED_TERM in row.cells:
        return None
    code_cell = row.cells[ColumnRole.CODE]
    return Finding(
        code=FindingCode.MISSING_PREFERRED_TERM,
        location=code_cell.reference,
        message="row has a code binding but no 'RCPA Preferred term' value; no entry can be "
        "seeded for this row",
    )


def _scan_missing_code_binding(row: SourceRow) -> Finding | None:
    """A row that carries a 'RCPA Preferred term' value but resolves no code
    binding has nothing to bind an entry to.

    The mirror of ``_scan_missing_preferred_term``, row-level for the same reason,
    and reported against the preferred-term cell. Unlike ``MISSING_PREFERRED_TERM``,
    ``build_dataset`` does not seed this row: a code-less row is more likely
    layout (a heading, a continuation line) than an entry awaiting a code.
    """
    if ColumnRole.PREFERRED_TERM not in row.cells or _row_has_code(row):
        return None
    term_cell = row.cells[ColumnRole.PREFERRED_TERM]
    return Finding(
        code=FindingCode.MISSING_CODE_BINDING,
        location=term_cell.reference,
        message="row has a 'RCPA Preferred term' value but no code binding; no entry can be "
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
    """Reports a sheet the code column can't be found on, and *why*.

    A sheet named in ``_NON_SPIA_DATA_SHEET_NAMES`` (FR-63's ``Rev History``, the
    one documented hand-written prose sheet) that also resolves zero SPIA column
    roles is reported as not SPIA data, without blocking. Every other sheet
    lacking the code column has drifted (FR-63): all its rows went unscanned for
    Appendix A.2/A.3 defects, which is what FR-71's data-defect band is for. The
    message gives the skipped row count so a low ``finding_count`` does not read
    as "nearly clean".
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

    Only a sheet that resolves a code column gets cell-level scanning. One that
    doesn't gets exactly one finding from ``_scan_layout``
    (``SHEET_NOT_SPIA_DATA`` or ``UNRECOGNISED_LAYOUT``), and scanning its cells
    for A.1/A.3 would add noise. A sheet that does also gets two row-level
    passes, ``_scan_missing_preferred_term`` and ``_scan_missing_code_binding``.
    Each finds the absence of a cell, which ``_scan_cell`` iterating cells cannot.
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
        for row_finding in (
            _scan_missing_preferred_term(row),
            _scan_missing_code_binding(row),
        ):
            if row_finding is not None:
                findings.append(row_finding)
    return tuple(findings)
