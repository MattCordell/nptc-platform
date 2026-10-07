"""Offline tests for scripts/dev_seed_filter.py (FR-70, FR-76). No Docker or Postgres.

The fixture tests run the real transform on the committed 50-row excerpt. The loader's reader
catches an uncoded specimen offline, so a fixture or table change that reopens that refusal fails
here. It does not check FR-05 collisions or a code held by two entries, so the invariant test below
checks those directly. The dry run in scripts/dev-seed.ps1 is the final arbiter of what the loader
accepts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from nptc.catalogue.seed_dataset import read_import_dataset
from nptc_shared.similarity import collision_key
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

#: Recorded from a real run: the Adrenal Ab pair (NPTC-000045's preferred term is a synonym on
#: NPTC-000009). The excerpt once held eight entries with an uncoded specimen; the reviewed specimen
#: map covers them all now.
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
    code: str | None = None,
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
        "code_bindings": [
            {
                "system": "http://snomed.info/sct",
                "code": code or f"9{key[-6:]}",
                "status": "active",
            }
        ],
        "properties": {"specimen": [{"value": "Serum", "code": specimen_code}]},
    }


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 2, "source": {"filename": "x.xlsx"}, "entries": list(entries)}


@pytest.mark.req("FR-88")
def test_the_unfiltered_excerpt_holds_no_specimen_the_loader_refuses(
    emitted_dataset: Path,
) -> None:
    dataset = read_import_dataset(emitted_dataset)

    specimens = [v for entry in dataset.entries for v in entry.properties.specimen]
    assert specimens
    assert all(v.code is not None for v in specimens)


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
    assert len(kept) == 50 - len(COLLIDING_KEYS)
    assert kept.isdisjoint(COLLIDING_KEYS)


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


def test_the_filtered_excerpt_has_no_collision_and_no_shared_code(
    emitted_dataset: Path, tmp_path: Path
) -> None:
    output = tmp_path / "import-dataset.json"
    cli.main(["--input", str(emitted_dataset), "--output", str(output)])
    entries = json.loads(output.read_text(encoding="utf-8"))["entries"]

    preferred = [collision_key(e["preferred_term"]) for e in entries]
    synonyms = [
        collision_key(d["term"])
        for e in entries
        for d in e["designations"]
        if d["use"] == "synonym"
    ]
    codes = [b["code"] for e in entries for b in e["code_bindings"]]

    assert len(set(preferred)) == len(preferred)
    assert not set(preferred) & set(synonyms)
    assert len(set(codes)) == len(codes)


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


def test_a_code_already_bound_to_an_earlier_entry_drops_the_later_entry() -> None:
    document = _document(
        _entry("NPTC-000001", "Sodium", code="25197003"),
        _entry("NPTC-000002", "Natrium", code="25197003"),
    )

    filtered, dropped = cli.filter_dataset(document)

    assert [e["business_key"] for e in filtered["entries"]] == ["NPTC-000001"]
    assert [d.business_key for d in dropped] == ["NPTC-000002"]
    assert "25197003" in dropped[0].reason
    assert "NPTC-000001" in dropped[0].reason


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
