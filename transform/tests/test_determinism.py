"""Output must not depend on the clock, absolute paths, or dict/hash ordering (FR-73)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest
from openpyxl.worksheet.worksheet import Worksheet

from nptc_transform.cellref import CellRef
from nptc_transform.pipeline import Finding, Mode, RunResult, SourceRef

ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_MISSPELLING_HEADERS = [
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


def _misspelling_workbook(tmp_path: Path) -> Path:
    """A small fixture exercising both FR-79 heuristics (issue #29, P0-7):
    an intra-entry near-match (``antental``/``Antenatal``) and a
    cross-entry corpus-frequency pair (``Bilirubon``/``Bilirubin``) - see
    ``test_misspelling.py`` for the same construction, in isolation."""
    path = tmp_path / "misspelling_determinism.xlsx"
    workbook = openpyxl.Workbook()
    sheet: Worksheet = workbook.active  # type: ignore[assignment]
    sheet.title = "Requesting"
    sheet.append(_MISSPELLING_HEADERS)
    sheet.append(["Antenatal screen", "antental", "", "", "", "", "", "10000010"])
    sheet.append(["Bilirubon", "", "", "", "", "", "", "10000023"])
    sheet.append(["Bilirubin", "", "", "", "", "", "", "10000034"])
    sheet.append(["Bilirubin panel", "", "", "", "", "", "", "10000047"])
    sheet.append(["Bilirubin ratio", "", "", "", "", "", "", "10000052"])
    workbook.save(path)
    return path


def _run_cli(
    *args: str, env: dict[str, str] | None = None, expected_returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "nptc_transform.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == expected_returncode, result.stderr
    return result


@pytest.mark.req("FR-73")
def test_two_runs_produce_byte_identical_reports(tmp_path: Path, sample_workbook: Path) -> None:
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    _run_cli("run", "--workbook", str(sample_workbook), "--report-dir", str(out1))
    _run_cli("run", "--workbook", str(sample_workbook), "--report-dir", str(out2))

    for name in ("report.json", "report.md"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


@pytest.mark.req("FR-73")
def test_output_is_independent_of_pythonhashseed(tmp_path: Path, sample_workbook: Path) -> None:
    out1 = tmp_path / "seed1"
    out2 = tmp_path / "seed2"

    _run_cli(
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(out1),
        env={**os.environ, "PYTHONHASHSEED": "1"},
    )
    _run_cli(
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(out2),
        env={**os.environ, "PYTHONHASHSEED": "2"},
    )

    for name in ("report.json", "report.md"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


@pytest.mark.req("FR-73")
def test_report_bytes_carry_no_absolute_path_no_timestamp_no_crlf(
    tmp_path: Path, sample_workbook: Path
) -> None:
    report_dir = tmp_path / "report"
    _run_cli("run", "--workbook", str(sample_workbook), "--report-dir", str(report_dir))

    for name in ("report.json", "report.md"):
        raw = (report_dir / name).read_bytes()
        text = raw.decode("utf-8")
        assert str(tmp_path) not in text
        assert not ISO_DATE_RE.search(text)
        assert b"\r\n" not in raw


@pytest.mark.req("FR-73")
def test_banded_output_is_independent_of_pythonhashseed(
    tmp_path: Path, annex_a_workbook: Path
) -> None:
    """The determinism guarantee must hold once findings carry a band, not
    only on a workbook clean enough that band assignment is vacuous - a bare
    ``band_for`` re-call proves nothing, since it's a pure dict lookup."""
    out1 = tmp_path / "seed1"
    out2 = tmp_path / "seed2"

    _run_cli(
        "run",
        "--workbook",
        str(annex_a_workbook),
        "--report-dir",
        str(out1),
        env={**os.environ, "PYTHONHASHSEED": "1"},
        expected_returncode=1,
    )
    _run_cli(
        "run",
        "--workbook",
        str(annex_a_workbook),
        "--report-dir",
        str(out2),
        env={**os.environ, "PYTHONHASHSEED": "2"},
        expected_returncode=1,
    )

    for name in ("report.json", "report.md"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-79")
def test_misspelling_findings_are_independent_of_pythonhashseed(tmp_path: Path) -> None:
    """The determinism guarantee must hold for the FR-79 heuristics too - the
    corpus-wide row-count and tie-break logic (``misspelling.py``) build
    ordinary ``dict``/``set`` structures internally, so this proves that
    internal iteration order never leaks into the report."""
    workbook = _misspelling_workbook(tmp_path)
    out1 = tmp_path / "seed1"
    out2 = tmp_path / "seed2"

    _run_cli(
        "run",
        "--workbook",
        str(workbook),
        "--report-dir",
        str(out1),
        env={**os.environ, "PYTHONHASHSEED": "1"},
    )
    _run_cli(
        "run",
        "--workbook",
        str(workbook),
        "--report-dir",
        str(out2),
        env={**os.environ, "PYTHONHASHSEED": "2"},
    )

    for name in ("report.json", "report.md"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    assert "PROBABLE_MISSPELLING" in (out1 / "report.json").read_text(encoding="utf-8")
    assert "INCONSISTENT_SPELLING" in (out1 / "report.json").read_text(encoding="utf-8")


def test_run_result_sorts_findings_into_canonical_order() -> None:
    findings = (
        Finding(code="B", location=CellRef("Sheet", "B", 2), message="second"),
        Finding(code="A", location=CellRef("Sheet", "A", 1), message="first"),
        Finding(code="A", location=CellRef("Sheet", "A", 1), message="also"),
    )

    result = RunResult(
        source=SourceRef(filename="x.xlsx", sha256="0" * 64),
        mode=Mode.REPORT_ONLY,
        findings=findings,
    )

    assert result.findings == (
        Finding(code="A", location=CellRef("Sheet", "A", 1), message="also"),
        Finding(code="A", location=CellRef("Sheet", "A", 1), message="first"),
        Finding(code="B", location=CellRef("Sheet", "B", 2), message="second"),
    )


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-76")
def test_two_emit_dataset_runs_produce_a_byte_identical_import_dataset(
    tmp_path: Path, sample_workbook: Path
) -> None:
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    _run_cli(
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(out1),
        "--emit-dataset",
        "--release-name",
        "2026-06",
    )
    _run_cli(
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(out2),
        "--emit-dataset",
        "--release-name",
        "2026-06",
    )

    assert (out1 / "import-dataset.json").read_bytes() == (
        out2 / "import-dataset.json"
    ).read_bytes()


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-76")
def test_import_dataset_is_independent_of_pythonhashseed(
    tmp_path: Path, sample_workbook: Path
) -> None:
    out1 = tmp_path / "seed1"
    out2 = tmp_path / "seed2"

    _run_cli(
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(out1),
        "--emit-dataset",
        "--release-name",
        "2026-06",
        env={**os.environ, "PYTHONHASHSEED": "1"},
    )
    _run_cli(
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(out2),
        "--emit-dataset",
        "--release-name",
        "2026-06",
        env={**os.environ, "PYTHONHASHSEED": "2"},
    )

    assert (out1 / "import-dataset.json").read_bytes() == (
        out2 / "import-dataset.json"
    ).read_bytes()


def test_run_result_sorts_columns_numerically_not_lexicographically() -> None:
    """Pins the ``CellRef.sort_key`` behaviour change this PR introduces:
    ``B2`` now sorts before ``B10`` (row, numeric), and ``B1`` before ``AA1``
    (column, numeric - never ``AA1`` before ``B1``, which plain string
    comparison of the column letters alone would give). There are no
    committed golden report fixtures this changes the shape of - both
    ``test_idempotency.py`` and this module compare a live run against a
    live run - so this test is what pins the new, intentionally-better
    ordering going forward.
    """
    findings = (
        Finding(code="X", location=CellRef("Sheet", "B", 10), message="b10"),
        Finding(code="X", location=CellRef("Sheet", "AA", 1), message="aa1"),
        Finding(code="X", location=CellRef("Sheet", "B", 2), message="b2"),
        Finding(code="X", location=CellRef("Sheet", "B", 1), message="b1"),
    )

    result = RunResult(
        source=SourceRef(filename="x.xlsx", sha256="0" * 64),
        mode=Mode.REPORT_ONLY,
        findings=findings,
    )

    assert [finding.message for finding in result.findings] == ["b1", "b2", "b10", "aa1"]
