"""Detects PRD Appendix A.1-A.3 cell-level defects and turns them into findings.

Detection only - nothing here corrects a value or assigns a severity band.
Band classification (auto-correctable / requires human decision / data
defect) is P0-3/#25's; this module only ever produces ``Finding`` values for
P0-3 to classify later (FR-70, FR-71).
"""

from __future__ import annotations

import math

from nptc_shared.text import (
    escape_invisible,
    find_invisible_characters,
    has_surrounding_whitespace,
)
from nptc_transform.findings import Finding
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


def _digit_count(value: int) -> int:
    """Counts the digits in ``abs(value)``. Assumes a finite value - callers
    that may see a non-finite float (see ``_scan_numeric_precision_risk``)
    check that first."""
    return len(str(abs(value)))


def _scan_invisible_characters(cell: Cell) -> Finding | None:
    found = find_invisible_characters(cell.text)
    if cell.role in _FREE_TEXT_ROLES:
        found = tuple(ic for ic in found if ic.codepoint not in _LEGITIMATE_LINE_BREAKS)
    if not found:
        return None
    detail = ", ".join(f"{ic.codepoint} ({ic.name}) at offset {ic.offset}" for ic in found)
    return Finding(
        code="INVISIBLE_CHARACTER",
        location=cell.reference,
        message=f"'{escape_invisible(cell.header)}' cell contains invisible character(s): {detail}",
    )


def _scan_surrounding_whitespace(cell: Cell) -> Finding | None:
    if not has_surrounding_whitespace(cell.text):
        return None
    header = escape_invisible(cell.header)
    if not cell.text.strip():
        message = f"'{header}' cell contains only whitespace"
    else:
        edges = []
        if cell.text != cell.text.lstrip():
            edges.append("leading")
        if cell.text != cell.text.rstrip():
            edges.append("trailing")
        message = f"'{header}' cell has {' and '.join(edges)} whitespace"
    return Finding(code="SURROUNDING_WHITESPACE", location=cell.reference, message=message)


def _scan_code_cell_type(cell: Cell) -> Finding | None:
    if cell.role is not ColumnRole.CODE or cell.cell_type is CellType.TEXT:
        return None
    return Finding(
        code="CODE_CELL_NOT_TEXT",
        location=cell.reference,
        message=f"code cell stored as {cell.cell_type.value}, not text (FR-06)",
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
            code="NUMERIC_PRECISION_RISK",
            location=cell.reference,
            message=f"'{header}' cell holds a value beyond Excel's numeric range",
        )

    digits = _digit_count(int(cell.raw))
    if digits < NUMERIC_PRECISION_RISK_THRESHOLD:
        return None
    return Finding(
        code="NUMERIC_PRECISION_RISK",
        location=cell.reference,
        message=(
            f"'{header}' cell holds a {digits}-digit number; "
            "Excel corrupts a numeric cell at 16 or more significant digits"
        ),
    )


def _scan_cell(cell: Cell) -> tuple[Finding, ...]:
    findings = (
        _scan_invisible_characters(cell),
        _scan_surrounding_whitespace(cell),
        _scan_code_cell_type(cell),
        _scan_numeric_precision_risk(cell),
    )
    return tuple(finding for finding in findings if finding is not None)


def _scan_layout(sheet: Sheet) -> Finding | None:
    if not sheet.cells:
        return None
    roles = {column_role(header) for header in sheet.headers}
    if ColumnRole.CODE in roles:
        return None
    headers_text = ", ".join(escape_invisible(header) for header in sheet.headers) or "(no headers)"
    return Finding(
        code="UNRECOGNISED_LAYOUT",
        location=f"{sheet.name}!A1",
        message=f"no column recognised as the code column; headers were: {headers_text}",
    )


def scan_workbook(sheets: tuple[Sheet, ...]) -> tuple[Finding, ...]:
    """Scans every sheet's cells for PRD Appendix A.1-A.3 defects.

    Only a sheet that resolves a code column gets cell-level scanning - a
    sheet that doesn't (for example the published workbook's own
    ``Rev History`` worksheet, FR-63, a hand-written prose paragraph with no
    SPIA columns at all) already gets a single ``UNRECOGNISED_LAYOUT``
    finding from ``_scan_layout``, and isn't SPIA data to begin with, so
    scanning its prose cells for A.1/A.3 would just add noise, not defects.
    """
    findings: list[Finding] = []
    for sheet in sheets:
        layout_finding = _scan_layout(sheet)
        if layout_finding is not None:
            findings.append(layout_finding)
            continue
        for cell in sheet.cells:
            findings.extend(_scan_cell(cell))
    return tuple(findings)
