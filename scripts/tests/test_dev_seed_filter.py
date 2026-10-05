"""Offline tests for scripts/dev_seed_filter.py (FR-70, FR-76). No Docker or Postgres.

The fixture tests run the real transform on the committed 50-row excerpt, so a change to the
fixture or the specimen table that reopens a loader refusal fails here rather than at seed time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from nptc.catalogue.seed_dataset import (
    DatasetNotSeedableError,
    read_import_dataset,
)
from nptc_transform.dataset import DATASET_JSON_NAME, build_dataset, write_dataset
from nptc_transform.pipeline import Mode, run_transform
from nptc_transform.workbook import read_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dev_seed_filter as cli

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "transform"
    / "tests"
    / "fixtures"
    / "spia-requesting-sample.xlsx"
)

#: Recorded from a real run: the specimens with no code in the excerpt, and the Adrenal Ab pair
#: (NPTC-000045's preferred term is a synonym on NPTC-000009).
UNCODED_SPECIMEN_KEYS = {f"NPTC-{n:06d}" for n in (19, 27, 35, 36, 37, 38, 41, 49)}
COLLIDING_KEYS = {"NPTC-000045"}


@pytest.fixture(scope="module")
def emitted_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    report_dir = tmp_path_factory.mktemp("transform-report")
    result = run_transform(FIXTURE, mode=Mode.EMIT_DATASET)
    write_dataset(build_dataset(read_workbook(FIXTURE), result, release_name="2026-06"), report_dir)
    return report_dir / DATASET_JSON_NAME


def _entry(
    key: str,
    preferred: str,
    synonyms: tuple[str, ...] = (),
    specimen_code: str | None = "119364003",
) -> dict[str, Any]:
    designations = [
        {"term": preferred, "use": "preferred", "language": "en-AU", "status": "active"}
    ]
    designations += [
        {"term": s, "use": "synonym", "language": "en-AU", "status": "active"} for s in synonyms
    ]
    return {
        "business_key": key,
        "preferred_term": preferred,
        "designations": designations,
        "properties": {"specimen": [{"value": "Serum", "code": specimen_code}]},
    }


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "source": {"filename": "x.xlsx"}, "entries": list(entries)}


@pytest.mark.req("FR-76")
def test_the_unfiltered_excerpt_is_refused_by_the_loader(emitted_dataset: Path) -> None:
    with pytest.raises(DatasetNotSeedableError):
        read_import_dataset(emitted_dataset)


@pytest.mark.req("FR-70")
@pytest.mark.req("FR-76")
def test_the_filtered_excerpt_is_accepted_by_the_loader_reader(
    emitted_dataset: Path, tmp_path: Path
) -> None:
    output = tmp_path / "filtered" / "import-dataset.json"

    code = cli.main(["--input", str(emitted_dataset), "--output", str(output)])

    assert code == cli.EXIT_OK
    dataset = read_import_dataset(output)
    kept = {entry.business_key for entry in dataset.entries}
    assert len(kept) == 50 - len(UNCODED_SPECIMEN_KEYS) - len(COLLIDING_KEYS)
    assert kept.isdisjoint(UNCODED_SPECIMEN_KEYS | COLLIDING_KEYS)


@pytest.mark.req("FR-06")
def test_the_filtered_excerpt_keeps_every_code_a_string(
    emitted_dataset: Path, tmp_path: Path
) -> None:
    output = tmp_path / "import-dataset.json"
    cli.main(["--input", str(emitted_dataset), "--output", str(output)])

    entries = json.loads(output.read_text(encoding="utf-8"))["entries"]

    for entry in entries:
        for binding in entry["code_bindings"]:
            assert isinstance(binding["code"], str)
        for value in entry["properties"]["specimen"]:
            assert value["code"] is None or isinstance(value["code"], str)


def test_the_filtered_excerpt_still_varies(emitted_dataset: Path, tmp_path: Path) -> None:
    output = tmp_path / "import-dataset.json"
    cli.main(["--input", str(emitted_dataset), "--output", str(output)])

    entries = json.loads(output.read_text(encoding="utf-8"))["entries"]

    disciplines = {v["value"] for e in entries for v in e["properties"]["discipline"]}
    assert len(disciplines) == 6
    assert any(len(e["properties"]["discipline"]) > 1 for e in entries)
    assert any(e["properties"]["subgroup"] for e in entries)
    assert any(not e["properties"]["subgroup"] for e in entries)
    assert any(len(e["designations"]) > 1 for e in entries)
    assert any(len(e["designations"]) == 1 for e in entries)


def test_everything_outside_entries_passes_through() -> None:
    document = _document(_entry("NPTC-000001", "Sodium"))

    filtered, dropped = cli.filter_dataset(document)

    assert filtered == document
    assert dropped == []


def test_an_uncoded_specimen_drops_the_entry_and_says_which_value() -> None:
    document = _document(
        _entry("NPTC-000001", "Sodium"), _entry("NPTC-000002", "Potassium", specimen_code=None)
    )

    filtered, dropped = cli.filter_dataset(document)

    assert [e["business_key"] for e in filtered["entries"]] == ["NPTC-000001"]
    assert [d.business_key for d in dropped] == ["NPTC-000002"]
    assert "Serum" in dropped[0].reason


def test_a_preferred_term_matching_an_earlier_synonym_drops_the_later_entry() -> None:
    document = _document(
        _entry("NPTC-000001", "21-Hydroxylase Ab", synonyms=("Adrenal Ab",)),
        _entry("NPTC-000002", "adrenal  AB"),
    )

    filtered, dropped = cli.filter_dataset(document)

    assert [e["business_key"] for e in filtered["entries"]] == ["NPTC-000001"]
    assert [d.business_key for d in dropped] == ["NPTC-000002"]


def test_a_synonym_matching_an_earlier_preferred_term_drops_the_later_entry() -> None:
    document = _document(
        _entry("NPTC-000001", "Adrenal Ab"),
        _entry("NPTC-000002", "21-Hydroxylase Ab", synonyms=("Adrenal Ab",)),
    )

    _, dropped = cli.filter_dataset(document)

    assert [d.business_key for d in dropped] == ["NPTC-000002"]


def test_a_synonym_shared_between_entries_is_only_a_warning_and_stays() -> None:
    document = _document(
        _entry("NPTC-000001", "17-OHP serum", synonyms=("17-OHP",)),
        _entry("NPTC-000002", "17-OHP plasma", synonyms=("17-OHP",)),
    )

    filtered, dropped = cli.filter_dataset(document)

    assert len(filtered["entries"]) == 2
    assert dropped == []


def test_an_entry_missing_a_field_is_a_shape_error() -> None:
    with pytest.raises(cli.DatasetShapeError):
        cli.filter_dataset(_document({"business_key": "NPTC-000001"}))


def test_a_document_without_entries_is_a_shape_error() -> None:
    with pytest.raises(cli.DatasetShapeError):
        cli.filter_dataset({"schema_version": 1})


def test_main_refuses_unreadable_input(tmp_path: Path) -> None:
    code = cli.main(["--input", str(tmp_path / "missing.json"), "--output", str(tmp_path / "o")])

    assert code == cli.EXIT_INPUT_REFUSED


def test_main_refuses_when_no_entry_survives_and_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text(
        json.dumps(_document(_entry("NPTC-000001", "Sodium", specimen_code=None))),
        encoding="utf-8",
    )
    output = tmp_path / "out.json"

    code = cli.main(["--input", str(source), "--output", str(output)])

    assert code == cli.EXIT_NOTHING_LEFT
    assert not output.exists()


def test_exit_codes_are_distinct() -> None:
    codes = [cli.EXIT_OK, cli.EXIT_USAGE_ERROR, cli.EXIT_INPUT_REFUSED, cli.EXIT_NOTHING_LEFT]
    assert len(set(codes)) == len(codes)
