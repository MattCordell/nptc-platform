"""Groups a sheet's cells by row (issue #31, P0-9).

Row grouping was reimplemented privately three times before this module
existed (``designation_check._rows_by_role``, ``semantic_drift._rows_by_role``,
``misspelling._group_entries``) - adding a fourth private copy for dataset
emission would have been indefensible. This is the one place that groups
``Sheet.cells`` by ``(sheet, row)``; ``designation_check.py`` and
``semantic_drift.py``'s own ``_rows_by_role`` helpers are now thin,
role-filtered wrappers over ``group_rows`` rather than independent
re-implementations. ``misspelling._group_entries`` is deliberately left
alone - it accumulates a cell *list* per row (multiple cells can share a
role there), a different shape than the one-cell-per-role ``Mapping`` this
module returns, and touching it buys nothing (see the plan's "Out of scope").
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

    A row with more than one cell sharing a role (only ``misspelling.py``'s
    own ``_group_entries`` needs that shape) is not representable here by
    design - the last cell for a given role silently wins during grouping,
    which is why ``group_rows`` is not a drop-in replacement for that
    function.
    """

    sheet: str
    row: int
    cells: Mapping[ColumnRole, Cell]


def group_rows(sheets: Sequence[Sheet]) -> tuple[SourceRow, ...]:
    """Groups every cell in ``sheets`` by ``(sheet.name, row)``, sorted.

    Sorted explicitly by ``(sheet.name, row)`` rather than left in whatever
    order a ``dict``/``defaultdict`` iterates - never relying on insertion
    order alone (FR-73).
    """
    grouped: dict[tuple[str, int], dict[ColumnRole, Cell]] = defaultdict(dict)
    for sheet in sheets:
        for cell in sheet.cells:
            grouped[(sheet.name, cell.row)][cell.role] = cell
    return tuple(
        SourceRow(sheet=sheet_name, row=row, cells=dict(cells))
        for (sheet_name, row), cells in sorted(grouped.items())
    )
