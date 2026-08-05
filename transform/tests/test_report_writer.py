"""Unit coverage for the report envelope, independent of the CLI (FR-73)."""

from __future__ import annotations

from pathlib import Path

from nptc_transform.pipeline import Finding, Mode, RunResult, SourceRef
from nptc_transform.report_writer import write_report


def test_write_report_renders_findings_in_both_files(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        findings=(Finding(code="INVISIBLE_CHAR", location="B2", message="zero-width space"),),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    json_text = (report_dir / "report.json").read_text(encoding="utf-8")
    assert '"finding_count": 1' in json_text
    assert "INVISIBLE_CHAR" in json_text

    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "| B2 | INVISIBLE_CHAR | zero-width space |" in markdown_text
