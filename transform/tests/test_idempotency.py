"""Re-running against an already-processed report directory is a no-op (FR-73)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from openpyxl.worksheet.worksheet import Worksheet
from typer.testing import CliRunner

from nptc_transform.cli import app

runner = CliRunner()

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
    """See ``test_determinism.py``'s identical fixture for what it exercises."""
    path = tmp_path / "misspelling_idempotency.xlsx"
    workbook = openpyxl.Workbook()
    sheet: Worksheet = workbook.active  # type: ignore[assignment]
    sheet.title = "Requesting"
    sheet.append(_MISSPELLING_HEADERS)
    sheet.append(["Antenatal screen", "antental", "", "", "", "", "", "10000001"])
    sheet.append(["Bilirubon", "", "", "", "", "", "", "10000002"])
    sheet.append(["Bilirubin", "", "", "", "", "", "", "10000003"])
    sheet.append(["Bilirubin panel", "", "", "", "", "", "", "10000004"])
    sheet.append(["Bilirubin ratio", "", "", "", "", "", "", "10000005"])
    workbook.save(path)
    return path


@pytest.mark.req("FR-73")
def test_rerun_into_the_same_report_dir_is_byte_identical(
    tmp_path: Path, sample_workbook: Path
) -> None:
    report_dir = tmp_path / "report"

    first = runner.invoke(
        app, ["run", "--workbook", str(sample_workbook), "--report-dir", str(report_dir)]
    )
    assert first.exit_code == 0, first.output
    before = {name: (report_dir / name).read_bytes() for name in ("report.json", "report.md")}
    files_before = sorted(p.name for p in report_dir.iterdir())

    second = runner.invoke(
        app, ["run", "--workbook", str(sample_workbook), "--report-dir", str(report_dir)]
    )
    assert second.exit_code == 0, second.output
    after = {name: (report_dir / name).read_bytes() for name in ("report.json", "report.md")}
    files_after = sorted(p.name for p in report_dir.iterdir())

    assert before == after
    assert files_before == files_after


@pytest.mark.req("FR-73")
def test_rerun_replaces_a_stale_report_rather_than_skipping_or_appending(
    tmp_path: Path, sample_workbook: Path
) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text("not a real report", encoding="utf-8")

    result = runner.invoke(
        app, ["run", "--workbook", str(sample_workbook), "--report-dir", str(report_dir)]
    )

    assert result.exit_code == 0, result.output
    assert (report_dir / "report.json").read_text(encoding="utf-8") != "not a real report"
    assert sorted(p.name for p in report_dir.iterdir()) == ["report.json", "report.md"]


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-76")
def test_rerun_with_emit_dataset_into_the_same_report_dir_is_byte_identical(
    tmp_path: Path, sample_workbook: Path
) -> None:
    report_dir = tmp_path / "report"

    args = [
        "run",
        "--workbook",
        str(sample_workbook),
        "--report-dir",
        str(report_dir),
        "--emit-dataset",
        "--release-name",
        "2026-06",
    ]

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    before = (report_dir / "import-dataset.json").read_bytes()

    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    after = (report_dir / "import-dataset.json").read_bytes()

    assert before == after
    assert sorted(p.name for p in report_dir.iterdir()) == [
        "import-dataset.json",
        "report.json",
        "report.md",
    ]


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-76")
def test_rerun_replaces_a_stale_import_dataset_rather_than_skipping_or_appending(
    tmp_path: Path, sample_workbook: Path
) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "import-dataset.json").write_text("not a real dataset", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "--workbook",
            str(sample_workbook),
            "--report-dir",
            str(report_dir),
            "--emit-dataset",
            "--release-name",
            "2026-06",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (report_dir / "import-dataset.json").read_text(encoding="utf-8") != "not a real dataset"


@pytest.mark.req("FR-73")
@pytest.mark.req("FR-79")
def test_rerun_over_the_misspelling_fixture_is_byte_identical(tmp_path: Path) -> None:
    workbook = _misspelling_workbook(tmp_path)
    report_dir = tmp_path / "report"

    first = runner.invoke(
        app, ["run", "--workbook", str(workbook), "--report-dir", str(report_dir)]
    )
    assert first.exit_code == 0, first.output
    before = {name: (report_dir / name).read_bytes() for name in ("report.json", "report.md")}

    second = runner.invoke(
        app, ["run", "--workbook", str(workbook), "--report-dir", str(report_dir)]
    )
    assert second.exit_code == 0, second.output
    after = {name: (report_dir / name).read_bytes() for name in ("report.json", "report.md")}

    assert before == after
