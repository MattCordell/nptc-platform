"""Shared fixtures for transform/tests."""

from __future__ import annotations

import zipfile
from pathlib import Path

import openpyxl
import pytest
from openpyxl.worksheet.worksheet import Worksheet

# FR-63's documented published header layout.
HEADERS = [
    "RCPA Preferred term",
    "RCPA Synonyms",
    "Usage guidance",
    "Length",
    "Discipline",
    "Subgroup",
    "Specimen",
    "Terminology binding (SNOMED CT-AU)",
    "SNOMED CT Fully Specified Name",
    "Version",
    "History",
]

NBSP = chr(0x00A0)  # non-breaking space
NNBSP = chr(0x202F)  # narrow no-break space


def _write_text_cell(worksheet: Worksheet, row: int, column: int, value: str) -> None:
    """Writes ``value`` as an explicitly text-typed cell (FR-06/FR-63)."""
    cell = worksheet.cell(row=row, column=column, value=value)
    cell.data_type = "s"


@pytest.fixture(scope="session")
def sample_workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A minimal, clean .xlsx fixture using the real FR-63 header layout.

    No real SPIA data, built in-process; every cell is clean, so a scan
    against it produces no Appendix A findings - existing FR-70/FR-73 tests
    depend on a run against this fixture succeeding without a layout warning.
    """
    workbook_dir = tmp_path_factory.mktemp("workbook")
    path = workbook_dir / "sample.xlsx"

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Requesting"
    sheet.append(HEADERS)
    row = ["Sample test", "", "", 11, "Chemical", "", "Serum", "", "Sample test", 4, ""]
    sheet.append(row)
    _write_text_cell(sheet, 2, 8, "12345678")
    workbook.save(path)

    return path


@pytest.fixture(scope="session")
def annex_a_workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Reproduces each PRD Appendix A.1-A.3 case, at the PRD's own cell addresses.

    Rows follow the PRD's own numbering where practical:

    - Row 2, 4, 16 (leading indices only), 21, 27, 34, 35: trailing U+00A0 on
      the preferred term (A.1).
    - Row 16: *two consecutive* U+00A0 (A.1).
    - Row 38: U+202F followed by U+00A0 (A.1).
    - H16, H17, H21: trailing U+00A0 on a code cell, itself stored as text
      (A.1, plus the code hygiene it doesn't otherwise violate).
    - Rows 34, 40, 41: 16-digit codes stored as text (clean re: A.2).
    - Rows 20, 24, 42: 18-digit codes stored as text (clean re: A.2).
    - A parallel set of rows with the same codes stored as numbers, to
      exercise CODE_CELL_NOT_TEXT and NUMERIC_PRECISION_RISK together.
    - 45 FSN rows with leading+trailing whitespace, 1 with leading only
      (row 15's pattern), 4 clean (A.3).
    - One fully clean row, to prove a clean cell produces no finding.
    """
    workbook_dir = tmp_path_factory.mktemp("annex_a")
    path = workbook_dir / "annex_a.xlsx"

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Requesting"
    sheet.append(HEADERS)

    def add_row(
        preferred_term: str,
        code: str | int,
        fsn: str,
        *,
        code_as_text: bool = True,
    ) -> int:
        row = sheet.max_row + 1
        sheet.cell(row=row, column=1, value=preferred_term)
        if code_as_text:
            _write_text_cell(sheet, row, 8, str(code))
        else:
            sheet.cell(row=row, column=8, value=code)
        sheet.cell(row=row, column=9, value=fsn)
        return row

    # A.1: trailing U+00A0 on the preferred term.
    add_row(f"Aciclovir level{NBSP}", "111111111", "Aciclovir level")
    # A.1: two consecutive U+00A0.
    add_row(f"Term with double space{NBSP}{NBSP}", "222222222", "Term with double space")
    # A.1: U+202F followed by U+00A0.
    add_row(f"Term with mixed space{NNBSP}{NBSP}", "333333333", "Term with mixed space")
    # A.1: trailing U+00A0 on a text-typed code cell.
    add_row("Clean term one", f"121309009{NBSP}", "Clean term one")

    # A.2: 16-digit and 18-digit codes, stored as text - clean.
    add_row("Clean sixteen digit code", "1393151000168101", "Clean sixteen digit code")
    add_row("Clean eighteen digit code", "933434771000036107", "Clean eighteen digit code")

    # A.2: the same shape, stored as a number - CODE_CELL_NOT_TEXT and
    # NUMERIC_PRECISION_RISK both fire.
    add_row(
        "Sixteen digit code as number",
        1393151000168101,
        "Sixteen digit code as number",
        code_as_text=False,
    )
    add_row(
        "Eighteen digit code as number",
        933434771000036107,
        "Eighteen digit code as number",
        code_as_text=False,
    )
    # A short code stored as a number - CODE_CELL_NOT_TEXT only, no precision risk.
    add_row("Short code as number", 12345678, "Short code as number", code_as_text=False)

    # A.3: leading and trailing whitespace on the FSN.
    add_row("Trichloroethane measurement", "444444444", " 1,1,1-Trichloroethane measurement ")
    # A.3: leading whitespace only.
    add_row(
        "Hydroxymandelate measurement",
        "555555555",
        " 3-Methyl,4-hydroxymandelate measurement",
    )

    # Fully clean row - must produce no finding at all.
    add_row("Clean row", "666666666", "Clean row")

    workbook.save(path)
    return path


@pytest.fixture(scope="session")
def unrecognised_layout_workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A workbook whose header row has no column recognisable as the code column."""
    workbook_dir = tmp_path_factory.mktemp("unrecognised_layout")
    path = workbook_dir / "unrecognised_layout.xlsx"

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Requesting"
    sheet.append(["Some Column", "Another Column"])
    sheet.append(["value", "value"])
    workbook.save(path)

    return path


@pytest.fixture()
def corrupt_workbook(tmp_path: Path) -> Path:
    """A file with an .xlsx extension that is not a valid zip/workbook."""
    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(b"not a real workbook")
    return path


@pytest.fixture()
def corrupt_worksheet_workbook(tmp_path: Path) -> Path:
    """A valid zip and workbook manifest, but a corrupt individual sheet XML.

    Distinct from ``corrupt_workbook``: read-only mode parses each
    worksheet's XML lazily, only when it's iterated, so a corrupt sheet part
    surfaces only during ``iter_rows()`` - not at ``load_workbook()`` time.
    """
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Requesting"
    sheet.append(["A"])
    sheet.append(["x"])
    good_path = tmp_path / "good.xlsx"
    workbook.save(good_path)

    corrupt_path = tmp_path / "corrupt_worksheet.xlsx"
    with (
        zipfile.ZipFile(good_path) as source,
        zipfile.ZipFile(corrupt_path, "w") as dest,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = b"<not-valid-xml"
            dest.writestr(item, data)

    return corrupt_path
