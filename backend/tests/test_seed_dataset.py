"""Reader tests for `import-dataset.json` (FR-76, ADR-0010). No database."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nptc.catalogue.seed_dataset import (
    DatasetInvalidError,
    DatasetNotSeedableError,
    DatasetUnreadableError,
    UnsupportedSchemaVersionError,
    highest_business_key,
    read_import_dataset,
)

MakeDocument = Callable[..., dict[str, Any]]
WriteDataset = Callable[[object], Path]


@pytest.mark.req("FR-76")
def test_a_valid_dataset_is_read(make_dataset_document: MakeDocument, write_dataset: WriteDataset):
    dataset = read_import_dataset(write_dataset(make_dataset_document(3)))

    assert [entry.business_key for entry in dataset.entries] == [
        "NPTC-500000",
        "NPTC-500001",
        "NPTC-500002",
    ]
    assert dataset.baseline_release.name == "2026-06"
    assert dataset.entries[0].code_bindings[0].code == "121348009"


@pytest.mark.req("FR-76")
@pytest.mark.parametrize("version", [1, 2, 0, "3", None, 3.5, True])
def test_a_schema_version_other_than_three_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset, version: object
) -> None:
    document = make_dataset_document()
    document["schema_version"] = version

    with pytest.raises(UnsupportedSchemaVersionError):
        read_import_dataset(write_dataset(document))


def test_a_missing_schema_version_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    del document["schema_version"]

    with pytest.raises(UnsupportedSchemaVersionError):
        read_import_dataset(write_dataset(document))


def test_the_version_is_checked_before_the_shape(write_dataset: WriteDataset) -> None:
    """A later transform's file must say 'unsupported version', not list shape errors."""
    with pytest.raises(UnsupportedSchemaVersionError):
        read_import_dataset(write_dataset({"schema_version": 4, "something": "new"}))


def test_a_missing_file_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(DatasetUnreadableError):
        read_import_dataset(tmp_path / "absent.json")


def test_a_file_that_is_not_json_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "import-dataset.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(DatasetUnreadableError):
        read_import_dataset(path)


def test_a_file_that_is_not_utf8_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "import-dataset.json"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(DatasetUnreadableError):
        read_import_dataset(path)


def test_a_json_array_is_unreadable(write_dataset: WriteDataset) -> None:
    with pytest.raises(DatasetUnreadableError):
        read_import_dataset(write_dataset([1, 2]))


@pytest.mark.req("FR-06")
def test_a_numeric_code_is_refused_not_converted(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["code_bindings"][0]["code"] = 121348009

    with pytest.raises(DatasetInvalidError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert any("code_bindings" in problem for problem in exc_info.value.problems)


def test_a_missing_key_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    del document["entries"][0]["preferred_term"]

    with pytest.raises(DatasetInvalidError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert any("preferred_term" in problem for problem in exc_info.value.problems)


def test_an_unknown_key_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["surprise"] = "x"

    with pytest.raises(DatasetInvalidError):
        read_import_dataset(write_dataset(document))


def test_an_empty_entry_list_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"] = []

    with pytest.raises(DatasetInvalidError):
        read_import_dataset(write_dataset(document))


def test_an_unknown_entry_status_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["status"] = "published"

    with pytest.raises(DatasetInvalidError):
        read_import_dataset(write_dataset(document))


def test_an_invalid_problem_message_never_echoes_the_value(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["code_bindings"][0]["code"] = 987654321

    with pytest.raises(DatasetInvalidError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "987654321" not in str(exc_info.value)


@pytest.mark.req("FR-76")
def test_an_uncoded_specimen_refuses_the_whole_dataset(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(2)
    document["entries"][1]["properties"]["specimen"] = [
        {"value": "Amniotic fluid", "code": None, "display": None}
    ]

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    (problem,) = exc_info.value.problems
    assert "NPTC-500001" in problem
    assert "Amniotic fluid" in problem
    assert "row 3" in problem


def test_every_problem_is_reported_in_one_refusal(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(3)
    for entry in document["entries"][:2]:
        entry["properties"]["specimen"] = [{"value": "Blood", "code": None, "display": None}]
    document["entries"][2]["code_bindings"][0]["fsn"] = None

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert len(exc_info.value.problems) == 3


def test_a_binding_without_an_fsn_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["code_bindings"][0]["fsn"] = "   "

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "no FSN" in exc_info.value.problems[0]


def test_a_version_1_file_is_refused_with_a_message_that_names_the_version(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["schema_version"] = 1
    document["entries"][0]["specimen_unconstrained"] = False

    with pytest.raises(UnsupportedSchemaVersionError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "schema_version is 1" in str(exc_info.value)


def test_a_version_2_file_is_refused_with_a_message_that_names_the_version(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    """Version 2 carried no specimen display, so it would seed a specimen labelled with the
    workbook's wording."""
    document = make_dataset_document()
    document["schema_version"] = 2

    with pytest.raises(UnsupportedSchemaVersionError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "schema_version is 2" in str(exc_info.value)


@pytest.mark.req("FR-88")
def test_a_property_value_without_a_display_key_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    del document["entries"][0]["properties"]["specimen"][0]["display"]

    with pytest.raises(DatasetInvalidError):
        read_import_dataset(write_dataset(document))


@pytest.mark.req("FR-89")
def test_the_specimen_root_beside_a_named_specimen_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["properties"]["specimen"] = [
        {"value": "Any", "code": "123038009", "display": None},
        {"value": "Serum", "code": "119364003", "display": None},
    ]

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "FR-89" in exc_info.value.problems[0]
    assert "NPTC-500000" in exc_info.value.problems[0]


@pytest.mark.req("FR-89")
def test_the_specimen_root_alone_is_accepted(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["properties"]["specimen"] = [
        {"value": "Any", "code": "123038009", "display": None}
    ]

    entry = read_import_dataset(write_dataset(document)).entries[0]

    assert [value.code for value in entry.properties.specimen] == ["123038009"]


@pytest.mark.req("FR-89")
def test_the_root_under_two_displays_is_not_a_root_beside_another_specimen(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["properties"]["specimen"] = [
        {"value": "Any", "code": "123038009", "display": None},
        {"value": "Specimen", "code": "123038009", "display": None},
    ]

    entry = read_import_dataset(write_dataset(document)).entries[0]

    assert {value.code for value in entry.properties.specimen} == {"123038009"}


def test_a_dataset_that_still_carries_the_retired_flag_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["specimen_unconstrained"] = False

    with pytest.raises(DatasetInvalidError):
        read_import_dataset(write_dataset(document))


def test_a_duplicate_business_key_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document(2)
    document["entries"][1]["business_key"] = document["entries"][0]["business_key"]

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "more than once" in exc_info.value.problems[0]


@pytest.mark.parametrize("key", ["NPTC-12", "nptc-000001", "000001", "NPTC-00000A"])
def test_a_malformed_business_key_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset, key: str
) -> None:
    document = make_dataset_document()
    document["entries"][0]["business_key"] = key

    with pytest.raises(DatasetNotSeedableError):
        read_import_dataset(write_dataset(document))


def test_an_entry_needs_exactly_one_code_binding(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["code_bindings"] = []

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "exactly one code binding" in exc_info.value.problems[0]


def test_the_preferred_designation_must_match_the_preferred_term(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["designations"][0]["term"] = "Something else"

    with pytest.raises(DatasetNotSeedableError) as exc_info:
        read_import_dataset(write_dataset(document))

    assert "differs from preferred_term" in exc_info.value.problems[0]


def test_a_coded_discipline_value_is_refused(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    document["entries"][0]["properties"]["discipline"][0]["code"] = "chemical_pathology"

    with pytest.raises(DatasetNotSeedableError):
        read_import_dataset(write_dataset(document))


@pytest.mark.req("FR-03")
def test_the_highest_key_is_compared_numerically(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    """`NPTC-1000000` sorts before `NPTC-999999` as text but is the greater key (FR-03 lets the
    key widen past six digits)."""
    document = make_dataset_document(3)
    document["entries"][0]["business_key"] = "NPTC-999999"
    document["entries"][1]["business_key"] = "NPTC-1000000"
    document["entries"][2]["business_key"] = "NPTC-000005"

    dataset = read_import_dataset(write_dataset(document))

    assert highest_business_key(dataset) == "NPTC-1000000"


def test_reading_leaves_the_document_untouched(
    make_dataset_document: MakeDocument, write_dataset: WriteDataset
) -> None:
    document = make_dataset_document()
    before = copy.deepcopy(document)

    read_import_dataset(write_dataset(document))

    assert document == before
