"""Re-running against an already-processed report directory is a no-op (FR-73)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nptc_transform.cli import app

runner = CliRunner()


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
