"""Offline tests for scripts/dev_seed_workbook_filter.py (FR-05, FR-70). No Docker or Postgres.

The fixture test runs the real transform on the committed 50-row excerpt, so a change to the
excerpt or to the collision rule that leaves the dev seed blocked fails here, not in a stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

from nptc_transform.bands import FindingCode
from nptc_transform.collision_check import check_collisions
from nptc_transform.dataset import build_dataset
from nptc_transform.pipeline import Mode, run_transform
from nptc_transform.workbook import Sheet, read_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dev_seed_workbook_filter as cli

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "transform"
    / "tests"
    / "fixtures"
    / "spia-requesting-sample.xlsx"
)

#: Recorded from a real run: NPTC-000045 (row 46) repeats a synonym of NPTC-000009 (row 10).
COLLIDING_ROW = 46
HEADERS = ["RCPA Preferred term", "RCPA Synonyms", "Terminology binding (SNOMED CT-AU)"]
CODES = ("122192001", "122192017", "122192029", "122192038")


def _workbook(tmp_path: Path, terms: list[tuple[str, str | None]]) -> Path:
    path = tmp_path / "terms.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Requesting"
    sheet.append(HEADERS)
    for (preferred, synonyms), code in zip(terms, CODES, strict=False):
        sheet.append([preferred, synonyms, code])
    workbook.save(path)
    return path


def _rows(sheets: tuple[Sheet, ...]) -> set[int]:
    return {cell.row for sheet in sheets for cell in sheet.cells}


def _filter(source: Path, tmp_path: Path) -> tuple[int, Path]:
    output = tmp_path / "filtered" / "out.xlsx"
    return cli.main(["--input", str(source), "--output", str(output)]), output


@pytest.mark.req("FR-05")
@pytest.mark.req("FR-70")
def test_the_filtered_excerpt_is_no_longer_blocked(tmp_path: Path) -> None:
    code, output = _filter(FIXTURE, tmp_path)

    result = run_transform(output, mode=Mode.EMIT_DATASET)

    assert code == cli.EXIT_OK
    assert not result.has_blocking_findings, result.findings
    assert not [f for f in result.findings if f.code == FindingCode.DESIGNATION_COLLISION]


@pytest.mark.req("FR-05")
def test_only_the_later_colliding_row_of_the_excerpt_is_dropped(tmp_path: Path) -> None:
    sheets = read_workbook(FIXTURE)

    dropped = cli.colliding_rows(sheets)

    assert [d.row for d in dropped] == [COLLIDING_ROW]
    original = build_dataset(
        sheets, run_transform(FIXTURE, mode=Mode.EMIT_DATASET), release_name="2026-06"
    )
    _, output = _filter(FIXTURE, tmp_path)
    filtered = build_dataset(
        read_workbook(output), run_transform(output, mode=Mode.EMIT_DATASET), release_name="2026-06"
    )
    assert len(filtered.entries) == len(original.entries) - 1


@pytest.mark.req("FR-05")
def test_every_other_row_keeps_its_row_number(tmp_path: Path) -> None:
    _, output = _filter(FIXTURE, tmp_path)
    original = read_workbook(FIXTURE)
    filtered = read_workbook(output)

    assert _rows(original) - _rows(filtered) == {COLLIDING_ROW}


@pytest.mark.req("FR-05")
def test_the_input_is_never_modified(tmp_path: Path) -> None:
    before = FIXTURE.read_bytes()

    _filter(FIXTURE, tmp_path)

    assert FIXTURE.read_bytes() == before


@pytest.mark.req("FR-05")
def test_an_entry_that_clashed_only_with_a_dropped_one_stays(tmp_path: Path) -> None:
    """B repeats A's synonym as its preferred term. C repeats B's preferred term as a synonym,
    but once B is gone C has nothing to clash with."""
    source = _workbook(
        tmp_path,
        [("Alpha test", "Shared"), ("Shared", None), ("Gamma test", "Shared")],
    )

    dropped = cli.colliding_rows(read_workbook(source))

    assert [d.row for d in dropped] == [3]
    _, output = _filter(source, tmp_path)
    assert check_collisions(read_workbook(output)) == ()
    assert _rows(read_workbook(output)) == {2, 4}


@pytest.mark.req("FR-05")
def test_a_workbook_with_no_collision_is_copied_whole(tmp_path: Path) -> None:
    source = _workbook(tmp_path, [("Glucose", "Sugar"), ("Blood sugar test", "Sugar")])

    code, output = _filter(source, tmp_path)

    assert code == cli.EXIT_OK
    assert cli.colliding_rows(read_workbook(source)) == []
    assert [c.text for s in read_workbook(output) for c in s.cells] == [
        c.text for s in read_workbook(source) for c in s.cells
    ]


def test_unreadable_input_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xlsx"

    code, output = _filter(missing, tmp_path)

    assert code == cli.EXIT_INPUT_REFUSED
    assert not output.exists()


def test_exit_codes_are_distinct() -> None:
    codes = [cli.EXIT_OK, cli.EXIT_USAGE_ERROR, cli.EXIT_INPUT_REFUSED]
    assert len(set(codes)) == len(codes)
