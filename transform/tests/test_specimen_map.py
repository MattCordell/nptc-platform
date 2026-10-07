"""Tests for the reviewed specimen map and its loader (FR-88, FR-06; ADR-0044)."""

from __future__ import annotations

import pytest

from nptc_shared.sctid import has_valid_check_digit
from nptc_shared.terminology.models import SPECIMEN_ROOT_CODE
from nptc_transform.specimen_map import (
    SPECIMEN_MAP,
    SpecimenMapError,
    normalise_specimen_key,
    parse_specimen_map,
)

_NBSP = chr(0x00A0)

_HEADER = (
    "Source code\tSource display\tTarget code\tTarget display\tRelationship type code\t"
    "Relationship type display\tNo map flag\tStatus"
)


def _row(
    source: str,
    code: str = "122575003",
    display: str = "Urine specimen (specimen)",
    relationship: str = "TARGET_EQUIVALENT",
    no_map: str = "false",
    status: str = "MAPPED",
) -> str:
    return f"hash\t{source}\t{code}\t{display}\t{relationship}\tnote\t{no_map}\t{status}"


def _tsv(*rows: str) -> str:
    return "\n".join((_HEADER, *rows)) + "\n"


def _problems(text: str) -> tuple[str, ...]:
    with pytest.raises(SpecimenMapError) as error:
        parse_specimen_map(text)
    return error.value.problems


@pytest.mark.req("FR-88")
def test_the_packaged_map_carries_the_reviewed_rows() -> None:
    assert len(SPECIMEN_MAP.entries) == 87
    no_map = [entry.source for entry in SPECIMEN_MAP.entries if entry.code is None]
    assert sorted(no_map) == ["Breath", "Culture", "N/A"]


@pytest.mark.req("FR-06")
def test_every_packaged_code_is_a_valid_sctid_held_as_a_string() -> None:
    assert SPECIMEN_MAP.codes
    for code in SPECIMEN_MAP.codes:
        assert isinstance(code, str)
        assert has_valid_check_digit(code), code


@pytest.mark.req("FR-06")
def test_a_sixteen_digit_extension_code_survives_unchanged() -> None:
    entry = SPECIMEN_MAP.resolve("Fetal scalp")
    assert entry is not None
    assert entry.code == "1308031000168102"


@pytest.mark.req("FR-88")
@pytest.mark.parametrize("text", ["Urine", "  urine  ", "URINE", f"24{_NBSP}hr urine"])
def test_a_string_resolves_after_trimming_and_ignoring_case(text: str) -> None:
    assert SPECIMEN_MAP.resolve(text) is not None


@pytest.mark.req("FR-88")
@pytest.mark.parametrize("text", ["Urine sample", "Ur", "serum plasma", "", "Whole blood"])
def test_a_string_the_map_does_not_cover_resolves_to_nothing(text: str) -> None:
    assert SPECIMEN_MAP.resolve(text) is None


@pytest.mark.req("FR-89")
def test_any_is_the_only_string_that_resolves_to_the_specimen_root() -> None:
    entry = SPECIMEN_MAP.resolve("Any")
    assert entry is not None
    assert entry.code == SPECIMEN_ROOT_CODE
    others = [e.source for e in SPECIMEN_MAP.entries if e.code == SPECIMEN_ROOT_CODE]
    assert others == ["Any"]


@pytest.mark.req("FR-89")
def test_breath_has_no_equivalent_until_a_specimen_concept_exists() -> None:
    entry = SPECIMEN_MAP.resolve("Breath")
    assert entry is not None
    assert entry.code is None


@pytest.mark.req("FR-89")
@pytest.mark.parametrize("source", ["Breath", "Anything else"])
def test_a_row_other_than_any_that_targets_the_specimen_root_is_refused(source: str) -> None:
    problems = _problems(
        _tsv(_row(source, SPECIMEN_ROOT_CODE, "Specimen (specimen)", "TARGET_BROADER"))
    )
    assert len(problems) == 1
    assert source in problems[0]
    assert "line 2" in problems[0]
    assert SPECIMEN_ROOT_CODE in problems[0]


@pytest.mark.req("FR-89")
@pytest.mark.parametrize("source", ["Any", "  any ", "ANY"])
def test_any_may_target_the_specimen_root(source: str) -> None:
    entry = parse_specimen_map(
        _tsv(_row(source, SPECIMEN_ROOT_CODE, "Specimen (specimen)", "TARGET_EQUIVALENT"))
    ).entries[0]
    assert entry.code == SPECIMEN_ROOT_CODE


@pytest.mark.req("FR-88")
def test_a_no_map_row_resolves_to_an_entry_with_no_code() -> None:
    for text in ("N/A", "Culture"):
        entry = SPECIMEN_MAP.resolve(text)
        assert entry is not None
        assert entry.code is None


@pytest.mark.req("FR-88")
def test_two_strings_may_share_a_code() -> None:
    fluid, fluids = SPECIMEN_MAP.resolve("Fluid"), SPECIMEN_MAP.resolve("Fluids")
    assert fluid is not None and fluids is not None
    assert fluid.code == fluids.code


def test_codes_are_distinct_and_sorted() -> None:
    assert SPECIMEN_MAP.codes == tuple(sorted(set(SPECIMEN_MAP.codes)))


def test_the_key_ignores_case_and_every_kind_of_space() -> None:
    assert normalise_specimen_key(f" Serum{_NBSP} ") == normalise_specimen_key("serum")


def test_a_leading_byte_order_mark_is_tolerated() -> None:
    parsed = parse_specimen_map(chr(0xFEFF) + _tsv(_row("Urine")))
    assert [entry.source for entry in parsed.entries] == ["Urine"]


@pytest.mark.req("FR-88")
def test_a_code_failing_the_check_digit_is_refused() -> None:
    problems = _problems(_tsv(_row("Urine", code="122575004")))
    assert any("line 2" in p and "122575004" in p and "not a valid SCTID" in p for p in problems)


@pytest.mark.req("FR-06")
def test_a_code_that_is_not_digits_is_refused() -> None:
    assert _problems(_tsv(_row("Urine", code="1.22575003E8")))


@pytest.mark.req("FR-88")
def test_two_rows_differing_only_by_case_and_spacing_are_refused() -> None:
    problems = _problems(_tsv(_row("Urine"), _row(" URINE ", code="119364003")))
    assert any("line 3" in p and "repeats the string on line 2" in p for p in problems)


def test_a_repeat_with_the_same_code_is_also_refused() -> None:
    assert _problems(_tsv(_row("Urine"), _row("urine")))


def test_a_no_map_row_that_carries_a_target_is_refused() -> None:
    assert _problems(_tsv(_row("N/A", no_map="true")))


def test_a_mapped_row_without_a_target_is_refused() -> None:
    assert _problems(_tsv(_row("Urine", code="", display="", relationship="")))


def test_an_unknown_relationship_is_refused() -> None:
    assert any(
        "TARGET_NARROWER" in p
        for p in _problems(_tsv(_row("Urine", relationship="TARGET_NARROWER")))
    )


def test_a_row_that_is_not_reviewed_is_refused() -> None:
    assert _problems(_tsv(_row("Urine", status="DRAFT")))


def test_a_bad_flag_is_refused() -> None:
    assert _problems(_tsv(_row("Urine", no_map="maybe")))


def test_a_blank_source_string_is_refused() -> None:
    assert _problems(_tsv(_row("  ")))


def test_a_short_row_is_refused() -> None:
    assert any("one cell per column" in p for p in _problems(_tsv("hash\tUrine\t122575003")))


def test_a_long_row_is_refused() -> None:
    assert any("one cell per column" in p for p in _problems(_tsv(_row("Urine") + "\textra")))


def test_a_missing_column_is_refused() -> None:
    assert _problems("Source display\tTarget code\nUrine\t122575003\n")


def test_an_empty_map_is_refused() -> None:
    assert _problems(_HEADER + "\n") == ("the map has no rows",)


def test_every_defect_is_reported_together() -> None:
    problems = _problems(
        _tsv(_row("Urine", code="1"), _row("Serum", status="DRAFT"), _row("urine"))
    )
    assert len(problems) >= 3


@pytest.mark.req("FR-88")
def test_a_file_saved_by_a_spreadsheet_with_a_bom_and_crlf_line_endings_parses() -> None:
    text = chr(0xFEFF) + _tsv(_row("Urine"), _row("Serum", code="119364003")).replace("\n", "\r\n")

    parsed = parse_specimen_map(text)

    assert [entry.source for entry in parsed.entries] == ["Urine", "Serum"]
    assert [entry.line for entry in parsed.entries] == [2, 3]
