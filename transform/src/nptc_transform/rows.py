"""Groups a sheet's cells by row.

The one place that groups ``Sheet.cells`` by ``(sheet, row)``. The
``_rows_by_role`` helpers in ``designation_check.py`` and ``semantic_drift.py``
are role-filtered wrappers over ``group_rows``. ``misspelling._group_entries`` is
left alone: it accumulates a cell *list* per row because several cells can share
a role there, a different shape from the one-cell-per-role ``Mapping`` returned
here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nptc_transform.workbook import Cell, ColumnRole, Sheet, column_role


@dataclass(frozen=True)
class SourceRow:
    """One worksheet row: its sheet name, row number, and every cell on it,
    keyed by role.

    A row with several cells sharing a role (only ``misspelling.py``'s
    ``_group_entries`` needs that) is not representable: the last cell for a role
    wins during grouping, so ``group_rows`` is not a drop-in replacement for that
    function.
    """

    sheet: str
    row: int
    cells: Mapping[ColumnRole, Cell]


def group_rows(sheets: Sequence[Sheet]) -> tuple[SourceRow, ...]:
    """Groups every cell in ``sheets`` by ``(sheet.name, row)``, sorted.

    Sorted explicitly by ``(sheet.name, row)``, never by ``dict`` iteration order
    (FR-73).
    """
    grouped: dict[tuple[str, int], dict[ColumnRole, Cell]] = defaultdict(dict)
    for sheet in sheets:
        for cell in sheet.cells:
            grouped[(sheet.name, cell.row)][cell.role] = cell
    return tuple(
        SourceRow(sheet=sheet_name, row=row, cells=dict(cells))
        for (sheet_name, row), cells in sorted(grouped.items())
    )


def resolves_code_column(sheet: Sheet) -> bool:
    """True if ``sheet``'s header row resolves the code column.

    The gate for a sheet to yield entries: ``dataset.py`` and ``collision_check.py``
    both read rows through ``seedable_rows``, so they see the same sheets.
    """
    roles = {column_role(header) for header in sheet.headers} - {ColumnRole.UNKNOWN}
    return ColumnRole.CODE in roles


def has_code_binding(row_cells: Mapping[ColumnRole, Cell]) -> bool:
    """True if ``row_cells`` resolves a non-empty code binding.

    A code cell holding only empty or whitespace text does not count. This is the
    test ``cell_defects._row_has_code`` and ``terminology_check.collect_code_bindings``
    apply, so all three agree on what "resolves a code binding" means.
    """
    code_cell = row_cells.get(ColumnRole.CODE)
    return code_cell is not None and bool(code_cell.text.strip())


def seedable_rows(sheets: Sequence[Sheet]) -> tuple[SourceRow, ...]:
    """The rows the baseline loader would seed, in the order it writes them.

    A row qualifies when it has both a preferred term and a code binding, on a
    sheet that resolves the code column, in ``(sheet name, row)`` order. A row
    missing either is already a blocking finding (``MISSING_PREFERRED_TERM``,
    ``MISSING_CODE_BINDING``), or is not a SPIA data row, so it never becomes an
    entry. Order matters to the loader: an entry meets only the entries before it.
    """
    codeable = sorted(
        (sheet for sheet in sheets if resolves_code_column(sheet)), key=lambda sheet: sheet.name
    )
    return tuple(
        row
        for row in group_rows(codeable)
        if ColumnRole.PREFERRED_TERM in row.cells and has_code_binding(row.cells)
    )
