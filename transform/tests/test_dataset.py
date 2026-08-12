"""``dataset.build_dataset``/``write_dataset`` (FR-70, FR-76, issue #31, P0-9)."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from nptc_transform.dataset import (
    DATASET_JSON_NAME,
    ImportDataset,
    PropertyValue,
    build_dataset,
    write_dataset,
)
from nptc_transform.pipeline import Mode, RunResult, run_transform
from nptc_transform.workbook import Sheet, read_workbook

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


def _workbook(tmp_path: Path, rows: list[list[object]]) -> Path:
    path = tmp_path / "dataset.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Requesting"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def _build(workbook_path: Path, *, release_name: str = "2026-06") -> ImportDataset:
    result: RunResult = run_transform(workbook_path, mode=Mode.EMIT_DATASET)
    sheets: tuple[Sheet, ...] = read_workbook(workbook_path)
    return build_dataset(sheets, result, release_name=release_name)


def _dataset_payload(report_dir: Path) -> dict[str, object]:
    return json.loads((report_dir / DATASET_JSON_NAME).read_text(encoding="utf-8"))


@pytest.mark.req("FR-76")
def test_baseline_release_block_carries_the_release_name_and_note(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path,
        [["A term", "", "", 11, "Chemical", "", "Serum", "12345678", "A term", 4, ""]],
    )

    dataset = _build(workbook_path)

    assert dataset.baseline_release.name == "2026-06"
    assert dataset.baseline_release.note == (
        "Synthetic baseline release representing the state at seeding (FR-76)."
    )


@pytest.mark.req("FR-06")
@pytest.mark.req("FR-07")
def test_long_codes_round_trip_as_exact_strings(tmp_path: Path) -> None:
    long_codes = ["1393151000168101", "12345678901234567", "933434771000036107"]
    rows = [
        [f"Term {index}", "", "", 11, "Chemical", "", "Serum", code, f"Term {index}", 4, ""]
        for index, code in enumerate(long_codes)
    ]
    workbook_path = _workbook(tmp_path, rows)

    dataset = _build(workbook_path)

    emitted_codes = [entry.code_bindings[0].code for entry in dataset.entries]
    assert emitted_codes == long_codes
    assert all(isinstance(code, str) for code in emitted_codes)


@pytest.mark.req("FR-71")
def test_the_three_auto_correctable_repairs_change_the_emitted_value(tmp_path: Path) -> None:
    nbsp = chr(0x00A0)
    workbook_path = tmp_path / "corrections.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Requesting"
    sheet.append(HEADERS)
    # A number-typed code cell (CODE_CELL_NOT_TEXT) alongside a padded
    # preferred term (SURROUNDING_WHITESPACE, with an interior NBSP too).
    sheet.append(
        [f"{nbsp}Padded term{nbsp}", "", "", 11, "Chemical", "", "Serum", 12345678, "x", 4, ""]
    )
    workbook.save(workbook_path)

    result: RunResult = run_transform(workbook_path, mode=Mode.EMIT_DATASET)
    assert not result.has_blocking_findings, result.findings

    sheets = read_workbook(workbook_path)
    dataset = build_dataset(sheets, result, release_name="2026-06")

    entry = dataset.entries[0]
    assert entry.preferred_term == "Padded term"
    assert entry.code_bindings[0].code == "12345678"
    assert isinstance(entry.code_bindings[0].code, str)


@pytest.mark.req("FR-89")
def test_any_specimen_yields_unconstrained_and_zero_specimen_values(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path, [["A term", "", "", 11, "Chemical", "", "Any", "12345678", "A term", 4, ""]]
    )

    dataset = _build(workbook_path)

    entry = dataset.entries[0]
    assert entry.specimen_unconstrained is True
    assert entry.properties.specimen == ()


@pytest.mark.req("FR-89")
def test_a_named_specimen_never_sets_unconstrained(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path, [["A term", "", "", 11, "Chemical", "", "Serum", "12345678", "A term", 4, ""]]
    )

    dataset = _build(workbook_path)

    entry = dataset.entries[0]
    assert entry.specimen_unconstrained is False
    assert entry.properties.specimen == (PropertyValue(value="Serum", code="119364003"),)


@pytest.mark.req("FR-88")
def test_an_unmapped_specimen_value_is_seeded_verbatim_with_no_code(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path,
        [["A term", "", "", 11, "Chemical", "", "Nasal swab thing", "12345678", "x", 4, ""]],
    )

    dataset = _build(workbook_path)

    entry = dataset.entries[0]
    assert entry.specimen_unconstrained is False
    assert entry.properties.specimen == (PropertyValue(value="Nasal swab thing", code=None),)


@pytest.mark.req("FR-04")
def test_a_doubled_delimiter_yields_two_synonyms_not_three(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path,
        [["Zovirax", "Zovirax;;Cyclir", "", 11, "Chemical", "", "Serum", "12345678", "x", 4, ""]],
    )

    dataset = _build(workbook_path)

    entry = dataset.entries[0]
    synonym_terms = [d.term for d in entry.designations if d.use == "synonym"]
    assert synonym_terms == ["Zovirax", "Cyclir"]


@pytest.mark.req("FR-04")
def test_a_comma_delimited_synonym_cell_is_split(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path,
        [
            [
                "ADA",
                "ADA RBC, ADA red cells",
                "",
                11,
                "Chemical",
                "",
                "Serum",
                "12345678",
                "x",
                4,
                "",
            ]
        ],
    )

    dataset = _build(workbook_path)

    entry = dataset.entries[0]
    synonym_terms = [d.term for d in entry.designations if d.use == "synonym"]
    assert synonym_terms == ["ADA RBC", "ADA red cells"]


@pytest.mark.req("FR-03")
def test_business_key_sequence_is_stable_and_gap_free(tmp_path: Path) -> None:
    rows = [
        [f"Term {i}", "", "", 11, "Chemical", "", "Serum", str(100000000 + i), f"Term {i}", 4, ""]
        for i in range(5)
    ]
    workbook_path = _workbook(tmp_path, rows)

    first = _build(workbook_path)
    assert [entry.business_key for entry in first.entries] == [
        "NPTC-000001",
        "NPTC-000002",
        "NPTC-000003",
        "NPTC-000004",
        "NPTC-000005",
    ]

    second = _build(workbook_path)
    assert [entry.business_key for entry in second.entries] == [
        entry.business_key for entry in first.entries
    ]


@pytest.mark.req("FR-90")
def test_a_compound_discipline_value_is_split(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path,
        [["A term", "", "", 11, "Chemical or Haematology", "", "Serum", "12345678", "x", 4, ""]],
    )

    dataset = _build(workbook_path)

    values = [v.value for v in dataset.entries[0].properties.discipline]
    assert values == ["Chemical", "Haematology"]


def test_write_dataset_writes_the_json_file(tmp_path: Path) -> None:
    workbook_path = _workbook(
        tmp_path, [["A term", "", "", 11, "Chemical", "", "Serum", "12345678", "A term", 4, ""]]
    )

    dataset = _build(workbook_path)

    report_dir = tmp_path / "out"
    write_dataset(dataset, report_dir)

    payload = _dataset_payload(report_dir)
    assert payload["schema_version"] == 1
    assert payload["baseline_release"]["name"] == "2026-06"
    assert len(payload["entries"]) == 1
