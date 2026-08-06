"""Tests for PRD Appendix A.1-A.3 cell defect detection (FR-70)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from typer.testing import CliRunner

from nptc_transform.cell_defects import scan_workbook
from nptc_transform.cli import app
from nptc_transform.pipeline import Finding
from nptc_transform.workbook import Sheet, read_workbook

runner = CliRunner()


def _findings_at(sheets: tuple[Sheet, ...], reference: str) -> list[Finding]:
    findings = scan_workbook(sheets)
    return [f for f in findings if f.location == reference]


@pytest.mark.req("FR-70")
def test_clean_workbook_produces_no_findings(sample_workbook: Path) -> None:
    sheets = read_workbook(sample_workbook)
    assert scan_workbook(sheets) == ()


@pytest.mark.req("FR-70")
def test_trailing_nbsp_on_preferred_term_flagged_invisible_character(
    annex_a_workbook: Path,
) -> None:
    sheets = read_workbook(annex_a_workbook)
    findings = _findings_at(sheets, "Requesting!A2")
    codes = {f.code for f in findings}
    assert "INVISIBLE_CHARACTER" in codes
    finding = next(f for f in findings if f.code == "INVISIBLE_CHARACTER")
    assert "U+00A0" in finding.message
    assert chr(0x00A0) not in finding.message


@pytest.mark.req("FR-70")
def test_two_consecutive_nbsp_both_reported_in_offset_order(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    finding = next(
        f for f in _findings_at(sheets, "Requesting!A3") if f.code == "INVISIBLE_CHARACTER"
    )
    # "Term with double space" is 22 characters, so the two U+00A0 sit at 22, 23.
    assert "offset 22" in finding.message
    assert "offset 23" in finding.message
    assert finding.message.index("offset 22") < finding.message.index("offset 23")


@pytest.mark.req("FR-70")
def test_narrow_no_break_space_then_nbsp_both_reported(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    finding = next(
        f for f in _findings_at(sheets, "Requesting!A4") if f.code == "INVISIBLE_CHARACTER"
    )
    assert "U+202F" in finding.message
    assert "U+00A0" in finding.message
    assert finding.message.index("U+202F") < finding.message.index("U+00A0")


@pytest.mark.req("FR-70")
def test_trailing_nbsp_on_code_cell_flagged(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    finding = next(
        f for f in _findings_at(sheets, "Requesting!H5") if f.code == "INVISIBLE_CHARACTER"
    )
    assert "U+00A0" in finding.message


@pytest.mark.req("FR-70")
def test_sixteen_and_eighteen_digit_codes_stored_as_text_are_clean(
    annex_a_workbook: Path,
) -> None:
    sheets = read_workbook(annex_a_workbook)
    for reference in ("Requesting!H6", "Requesting!H7"):
        findings = _findings_at(sheets, reference)
        codes = {f.code for f in findings}
        assert "CODE_CELL_NOT_TEXT" not in codes
        assert "NUMERIC_PRECISION_RISK" not in codes


@pytest.mark.req("FR-70")
def test_sixteen_digit_code_as_number_flags_both_defects(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    codes = {f.code for f in _findings_at(sheets, "Requesting!H8")}
    assert "CODE_CELL_NOT_TEXT" in codes
    assert "NUMERIC_PRECISION_RISK" in codes


@pytest.mark.req("FR-70")
def test_eighteen_digit_code_as_number_flags_both_defects(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    codes = {f.code for f in _findings_at(sheets, "Requesting!H9")}
    assert "CODE_CELL_NOT_TEXT" in codes
    assert "NUMERIC_PRECISION_RISK" in codes


@pytest.mark.req("FR-70")
def test_short_code_as_number_flags_type_only_not_precision_risk(
    annex_a_workbook: Path,
) -> None:
    sheets = read_workbook(annex_a_workbook)
    codes = {f.code for f in _findings_at(sheets, "Requesting!H10")}
    assert "CODE_CELL_NOT_TEXT" in codes
    assert "NUMERIC_PRECISION_RISK" not in codes


@pytest.mark.req("FR-70")
def test_leading_and_trailing_whitespace_on_fsn_flagged(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    finding = next(
        f for f in _findings_at(sheets, "Requesting!I11") if f.code == "SURROUNDING_WHITESPACE"
    )
    assert "leading" in finding.message
    assert "trailing" in finding.message


@pytest.mark.req("FR-70")
def test_leading_only_whitespace_on_fsn_flagged(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    finding = next(
        f for f in _findings_at(sheets, "Requesting!I12") if f.code == "SURROUNDING_WHITESPACE"
    )
    assert "leading" in finding.message
    assert "trailing" not in finding.message


@pytest.mark.req("FR-70")
def test_clean_row_produces_no_finding(annex_a_workbook: Path) -> None:
    sheets = read_workbook(annex_a_workbook)
    assert _findings_at(sheets, "Requesting!A13") == []
    assert _findings_at(sheets, "Requesting!H13") == []
    assert _findings_at(sheets, "Requesting!I13") == []


@pytest.mark.req("FR-70")
def test_unrecognised_layout_flags_the_missing_code_column(
    unrecognised_layout_workbook: Path,
) -> None:
    sheets = read_workbook(unrecognised_layout_workbook)
    findings = scan_workbook(sheets)
    assert any(f.code == "UNRECOGNISED_LAYOUT" for f in findings)
    finding = next(f for f in findings if f.code == "UNRECOGNISED_LAYOUT")
    assert finding.location == "Requesting!A1"
    assert "Some Column" in finding.message


@pytest.mark.req("FR-70")
def test_headers_only_sheet_with_no_code_column_is_not_flagged(tmp_path: Path) -> None:
    """No data rows means nothing to validate yet - an empty template sheet
    with an unfamiliar header row is not itself a defect."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Requesting"
    sheet.append(["Some Column", "Another Column"])
    path = tmp_path / "headers_only.xlsx"
    workbook.save(path)

    sheets = read_workbook(path)
    findings = scan_workbook(sheets)
    assert findings == ()


@pytest.mark.req("FR-70")
def test_report_never_contains_a_raw_invisible_character(
    tmp_path: Path, annex_a_workbook: Path
) -> None:
    """NFR-38 test 2: no generated output may contain U+00A0 or U+202F, even
    though every finding in this report is about them."""
    report_dir = tmp_path / "report"
    result = runner.invoke(
        app, ["run", "--workbook", str(annex_a_workbook), "--report-dir", str(report_dir)]
    )
    assert result.exit_code == 0, result.output

    for name in ("report.json", "report.md"):
        text = (report_dir / name).read_text(encoding="utf-8")
        assert chr(0x00A0) not in text
        assert chr(0x202F) not in text
