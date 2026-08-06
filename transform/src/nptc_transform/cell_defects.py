"""Detects PRD Appendix A.1-A.3 cell-level defects and turns them into findings.

Detection only - nothing here corrects a value or assigns a severity band.
Band classification (auto-correctable / requires human decision / data
defect) is P0-3/#25's; this module only ever produces ``Finding`` values for
P0-3 to classify later (FR-70, FR-71).
"""

from __future__ import annotations

from nptc_shared.text import (
    escape_invisible,
    find_invisible_characters,
    has_surrounding_whitespace,
)
from nptc_transform.pipeline import Finding
from nptc_transform.workbook import Cell, CellType, ColumnRole, Sheet, column_role

# PRD §2.1: "the underlying IEEE 754 double holds about 15.95 decimal digits,
# so 15 is the guaranteed-safe ceiling; Excel truncates to it rather than
# rounding at the binary boundary." A numeric cell at or beyond this many
# significant digits is a precision-risk finding regardless of column.
NUMERIC_PRECISION_RISK_THRESHOLD = 15


def _significant_digit_count(value: int | float) -> int:
    return len(str(abs(int(value))))


def _scan_invisible_characters(cell: Cell) -> Finding | None:
    found = find_invisible_characters(cell.text)
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
    edges = []
    if cell.text != cell.text.lstrip():
        edges.append("leading")
    if cell.text != cell.text.rstrip():
        edges.append("trailing")
    return Finding(
        code="SURROUNDING_WHITESPACE",
        location=cell.reference,
        message=f"'{escape_invisible(cell.header)}' cell has {' and '.join(edges)} whitespace",
    )


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
    digits = _significant_digit_count(cell.raw)
    if digits < NUMERIC_PRECISION_RISK_THRESHOLD:
        return None
    return Finding(
        code="NUMERIC_PRECISION_RISK",
        location=cell.reference,
        message=(
            f"'{escape_invisible(cell.header)}' cell holds a {digits}-digit number; "
            "Excel's 15-digit precision ceiling risks silent corruption"
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
    """Scans every sheet's cells for PRD Appendix A.1-A.3 defects."""
    findings: list[Finding] = []
    for sheet in sheets:
        layout_finding = _scan_layout(sheet)
        if layout_finding is not None:
            findings.append(layout_finding)
        for cell in sheet.cells:
            findings.extend(_scan_cell(cell))
    return tuple(findings)
