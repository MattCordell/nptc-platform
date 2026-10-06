"""The transform and the seed loader agree on which workbooks FR-05 refuses (FR-05).

The transform's `check_collisions` reports a collision from the workbook alone. The loader
finds it through `create_entry` and `add_synonyms`. They share `collision_key` but apply the
pair rule separately, so this test feeds each case through both and fails if one refuses what
the other accepts. A refusal for any other reason fails too: the loader must raise
`DesignationCollisionError` specifically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from sqlalchemy.orm import Session

from nptc.catalogue.seed_dataset import read_import_dataset
from nptc.catalogue.seed_import import SeedEntryError, seed_baseline
from nptc_transform.collision_check import check_collisions
from nptc_transform.dataset import DATASET_JSON_NAME, build_dataset, write_dataset
from nptc_transform.pipeline import Mode, run_transform
from nptc_transform.workbook import read_workbook

pytestmark = pytest.mark.usefixtures("pristine_catalogue")

#: Verhoeff-valid and distinct, because the database allows one active entry per code.
CODES = ("122192001", "122192017", "122192029", "122192038")

#: A key range far above anything another test mints; the loader advances the business-key
#: sequence past the highest key it writes, and a sequence is not transactional.
FIRST_KEY_NUMBER = 700_000

NBSP = chr(0xA0)

Terms = list[tuple[str, str | None]]


def _workbook(tmp_path: Path, terms: Terms) -> Path:
    path = tmp_path / "parity.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Requesting"
    sheet.append(
        [
            "RCPA Preferred term",
            "RCPA Synonyms",
            "Terminology binding (SNOMED CT-AU)",
            "SNOMED CT Fully Specified Name",
        ]
    )
    for (preferred, synonyms), code in zip(terms, CODES, strict=False):
        sheet.append([preferred, synonyms, code, f"{preferred} measurement"])
    workbook.save(path)
    return path


def _dataset_file(workbook: Path, report_dir: Path) -> Path:
    """The dataset the transform would write, with business keys moved out of the way."""
    result = run_transform(workbook, mode=Mode.EMIT_DATASET)
    write_dataset(
        build_dataset(read_workbook(workbook), result, release_name="2026-06"), report_dir
    )
    path = report_dir / DATASET_JSON_NAME
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for index, entry in enumerate(document["entries"]):
        entry["business_key"] = f"NPTC-{FIRST_KEY_NUMBER + index:06d}"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _loader_refuses_for_collision(session: Session, dataset_file: Path) -> bool:
    dataset = read_import_dataset(dataset_file)
    try:
        seed_baseline(session, dataset)
    except SeedEntryError as error:
        session.rollback()
        assert error.cause_type == "DesignationCollisionError", error
        return True
    return False


CASES: dict[str, tuple[Terms, bool]] = {
    "preferred equals an earlier synonym": (
        [("Adrenal antibody", "Adrenal Ab"), ("Adrenal Ab", None)],
        True,
    ),
    "synonym equals an earlier preferred": (
        [("Cortisol", None), ("Serum cortisol", "Cortisol;Hydrocortisone")],
        True,
    ),
    "two equal preferred terms": ([("Ferritin", None), ("Ferritin", None)], True),
    "punctuation and case differ": ([("17-OHP", None), ("17 ohp", None)], True),
    "a non-breaking space differs": ([("Anti DNA", None), (f"Anti{NBSP}DNA", None)], True),
    "a later entry holds an earlier synonym as preferred": (
        [("A test", "Shared"), ("Another test", "Other"), ("Shared", None)],
        True,
    ),
    "a synonym shared by two entries": (
        [("Glucose", "Sugar"), ("Blood sugar test", "Sugar")],
        False,
    ),
    "a synonym equals its own preferred term": ([("Glucose", "Glucose;Sugar")], False),
    "a repeated synonym in one entry": ([("Glucose", "Sugar;sugar")], False),
    "a token boundary keeps terms apart": ([("AntiDNA", None), ("Anti-DNA", None)], False),
}


@pytest.mark.req("FR-05")
@pytest.mark.integration
@pytest.mark.parametrize(("terms", "collides"), CASES.values(), ids=CASES.keys())
def test_the_transform_and_the_loader_agree_on_a_collision(
    app_session: Session, tmp_path: Path, terms: Terms, collides: bool
) -> None:
    workbook = _workbook(tmp_path, terms)
    reported = bool(check_collisions(read_workbook(workbook)))
    dataset_file = _dataset_file(workbook, tmp_path / "report")

    refused = _loader_refuses_for_collision(app_session, dataset_file)

    assert reported is collides
    assert refused is collides
