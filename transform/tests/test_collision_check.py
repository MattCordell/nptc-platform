"""``collision_check.check_collisions`` (FR-05).

The loader writes entries in ``(sheet, row)`` order and refuses an entry that collides at
error severity with one already written. These tests pin each error rule, the cases that
must not report, and the message a RCPA-QAP editor acts on.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from nptc_transform.bands import Band, FindingCode
from nptc_transform.collision_check import check_collisions
from nptc_transform.workbook import Sheet, read_workbook

NBSP = chr(0xA0)
HEADERS = ["RCPA Preferred term", "RCPA Synonyms", "Terminology binding (SNOMED CT-AU)"]

#: (preferred term, synonyms cell, code); a code of None leaves the cell empty.
Row = tuple[str, str | None, str | None]


def _sheets(
    tmp_path: Path, rows: list[Row], *, sheet_name: str = "Requesting", name: str = "collisions"
) -> tuple[Sheet, ...]:
    path = tmp_path / f"{name}.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = sheet_name
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return read_workbook(path)


def _codes(count: int) -> list[str]:
    return [str(10000000 + index) for index in range(count)]


def _rows(*terms: tuple[str, str | None]) -> list[Row]:
    return [
        (preferred, synonyms, code)
        for (preferred, synonyms), code in zip(terms, _codes(len(terms)), strict=True)
    ]


@pytest.mark.req("FR-05")
def test_a_preferred_term_equal_to_an_earlier_synonym_is_reported(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("Adrenal antibody", "Adrenal Ab"), ("Adrenal Ab", None)))

    findings = check_collisions(sheets)

    assert [f.code for f in findings] == [FindingCode.DESIGNATION_COLLISION]
    assert str(findings[0].location) == "Requesting!A3"
    assert findings[0].band is Band.DATA_DEFECT


@pytest.mark.req("FR-05")
def test_a_synonym_equal_to_an_earlier_preferred_term_is_reported(tmp_path: Path) -> None:
    sheets = _sheets(
        tmp_path, _rows(("Cortisol", None), ("Serum cortisol", "Cortisol;Hydrocortisone"))
    )

    findings = check_collisions(sheets)

    assert [str(f.location) for f in findings] == ["Requesting!B3"]
    assert "'Cortisol'" in findings[0].message


@pytest.mark.req("FR-05")
def test_two_equal_preferred_terms_are_reported_on_the_later_entry(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("Ferritin", None), ("Ferritin", None)))

    findings = check_collisions(sheets)

    assert [str(f.location) for f in findings] == ["Requesting!A3"]


@pytest.mark.req("FR-05")
def test_punctuation_and_case_do_not_hide_a_collision(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("17-OHP", None), ("17 ohp", None)))

    assert len(check_collisions(sheets)) == 1


@pytest.mark.req("FR-05")
def test_a_non_breaking_space_does_not_hide_a_collision(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("Anti DNA", None), ("Anti" + NBSP + "DNA", None)))

    assert len(check_collisions(sheets)) == 1


@pytest.mark.req("FR-05")
def test_a_synonym_shared_by_two_entries_is_not_an_error_collision(tmp_path: Path) -> None:
    """Warning severity in the backend, so it must not block the import."""
    sheets = _sheets(tmp_path, _rows(("Glucose", "Sugar"), ("Blood sugar test", "Sugar")))

    assert check_collisions(sheets) == ()


@pytest.mark.req("FR-05")
def test_a_synonym_equal_to_its_own_preferred_term_is_not_a_collision(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("Glucose", "Glucose;Sugar")))

    assert check_collisions(sheets) == ()


@pytest.mark.req("FR-05")
def test_a_repeated_synonym_inside_one_entry_is_not_a_collision(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("Glucose", "Sugar;sugar")))

    assert check_collisions(sheets) == ()


@pytest.mark.req("FR-05")
def test_a_token_boundary_keeps_distinct_terms_apart(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("AntiDNA", None), ("Anti-DNA", None)))

    assert check_collisions(sheets) == ()


@pytest.mark.req("FR-05")
def test_a_row_with_no_code_binding_is_not_compared(tmp_path: Path) -> None:
    """The loader never seeds it, so it cannot collide. MISSING_CODE_BINDING reports it."""
    rows: list[Row] = [("Ferritin", None, "10000000"), ("Ferritin", None, None)]

    assert check_collisions(_sheets(tmp_path, rows)) == ()


@pytest.mark.req("FR-05")
def test_a_later_entry_is_reported_against_every_earlier_holder(tmp_path: Path) -> None:
    sheets = _sheets(tmp_path, _rows(("Ferritin", None), ("Ferritin", None), ("Ferritin", None)))

    findings = check_collisions(sheets)

    assert [str(f.location) for f in findings] == ["Requesting!A3", "Requesting!A4"]
    assert "row 2" in findings[1].message
    assert "row 3" in findings[1].message


@pytest.mark.req("FR-05")
def test_one_entry_with_two_colliding_terms_gets_one_finding_for_each(tmp_path: Path) -> None:
    sheets = _sheets(
        tmp_path,
        _rows(("Cortisol", None), ("Ferritin", None), ("Iron studies", "Cortisol;Ferritin")),
    )

    findings = check_collisions(sheets)

    assert len(findings) == 2
    assert {str(f.location) for f in findings} == {"Requesting!B4"}


@pytest.mark.req("FR-05")
def test_the_message_names_both_entries_by_sheet_and_row(tmp_path: Path) -> None:
    sheets = _sheets(
        tmp_path, _rows(("Adrenal antibody", "Adrenal Ab"), ("Adrenal Ab", None)), sheet_name="Req"
    )

    (finding,) = check_collisions(sheets)

    assert "'Req' row 3" in finding.message
    assert "'Req' row 2" in finding.message
    assert "'Adrenal Ab'" in finding.message
    assert "FR-05" in finding.message


@pytest.mark.req("FR-05")
def test_the_message_escapes_invisible_characters(tmp_path: Path) -> None:
    term = "Ab​X"
    sheets = _sheets(tmp_path, _rows((term, None), (term, None)))

    (finding,) = check_collisions(sheets)

    assert "<U+200B>" in finding.message
    assert "​" not in finding.message


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-05")
def test_findings_do_not_depend_on_the_order_the_sheets_are_read(tmp_path: Path) -> None:
    """The loader's order is (sheet name, row), so the first sheet by name holds the earlier entry."""
    first = _sheets(tmp_path, _rows(("Cortisol", "Hydrocortisone")), sheet_name="A", name="a")
    second = _sheets(tmp_path, _rows(("Hydrocortisone", None)), sheet_name="B", name="b")

    forward = check_collisions((*first, *second))
    backward = check_collisions((*second, *first))

    assert forward == backward
    assert [str(f.location) for f in forward] == ["B!A2"]


@pytest.mark.req("FR-05")
def test_blank_preferred_terms_are_not_reported_as_a_collision(tmp_path: Path) -> None:
    """A blank cell is WHITESPACE_ONLY_CELL's to report, and it has no term to compare."""
    sheets = _sheets(tmp_path, _rows(("   ", None), ("   ", None)))

    assert check_collisions(sheets) == ()
