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

from nptc_transform.workbook import Cell, ColumnRole, Sheet


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
