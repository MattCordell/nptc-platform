"""``rows.group_rows`` (issue #31, P0-9): the one place that groups a sheet's
cells by row."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from nptc_transform.rows import group_rows
from nptc_transform.workbook import ColumnRole, Sheet, read_workbook


@pytest.fixture()
def two_sheet_workbook(tmp_path: Path) -> Path:
    path = tmp_path / "two_sheets.xlsx"
    workbook = openpyxl.Workbook()
    first = workbook.active
    assert first is not None
    first.title = "Requesting"
    first.append(["RCPA Preferred term", "Terminology binding (SNOMED CT-AU)"])
    first.append(["First term", "111111111"])
    first.append(["Second term", "222222222"])

    second = workbook.create_sheet("Another")
    second.append(["RCPA Preferred term", "Terminology binding (SNOMED CT-AU)"])
    second.append(["Third term", "333333333"])

    workbook.save(path)
    return path


def _sheets(path: Path) -> tuple[Sheet, ...]:
    return read_workbook(path)


def test_group_rows_groups_every_cell_by_sheet_and_row(two_sheet_workbook: Path) -> None:
    grouped = group_rows(_sheets(two_sheet_workbook))

    assert [(row.sheet, row.row) for row in grouped] == [
        ("Another", 2),
        ("Requesting", 2),
        ("Requesting", 3),
    ]
    first_row = next(row for row in grouped if row.sheet == "Requesting" and row.row == 2)
    assert first_row.cells[ColumnRole.PREFERRED_TERM].text == "First term"
    assert first_row.cells[ColumnRole.CODE].text == "111111111"


def test_group_rows_is_sorted_by_sheet_name_then_row_not_workbook_order(
    two_sheet_workbook: Path,
) -> None:
    """'Another' sorts before 'Requesting' lexicographically, even though it
    is the second sheet in the workbook (FR-73: never rely on the workbook's
    own sheet order)."""
    grouped = group_rows(_sheets(two_sheet_workbook))

    assert grouped[0].sheet == "Another"


def test_group_rows_on_an_empty_sequence_is_empty() -> None:
    assert group_rows(()) == ()
