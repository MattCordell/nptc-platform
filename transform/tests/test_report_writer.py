"""Unit coverage for the report envelope, independent of the CLI (FR-73)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nptc_transform.pipeline import Finding, Mode, RunResult, SourceRef
from nptc_transform.report_writer import write_report
from nptc_transform.terminology_check import EditionResolution, TerminologyRun


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
    # An unregistered code fails safe to data-defect (bands.band_for).
    assert "| B2 | INVISIBLE_CHAR | data-defect | zero-width space |" in markdown_text


@pytest.mark.req("FR-48")
def test_a_terminology_run_records_the_editions_it_resolved_against(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        terminology=TerminologyRun(
            codes_checked=42,
            codes_not_checked=1,
            editions=(
                EditionResolution(
                    label="au",
                    resolved_versions=(
                        "http://snomed.info/sct/32506021000036107/version/20260531",
                    ),
                ),
            ),
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["terminology"] == {
        "codes_checked": 42,
        "codes_not_checked": 1,
        "editions": [
            {
                "label": "au",
                "resolved_versions": ["http://snomed.info/sct/32506021000036107/version/20260531"],
            }
        ],
    }
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "42 code(s) checked, 1 not checked" in markdown_text
    assert "| au | http://snomed.info/sct/32506021000036107/version/20260531 |" in markdown_text


@pytest.mark.req("FR-48")
def test_a_run_with_no_terminology_pass_says_so_rather_than_omitting_it(tmp_path: Path) -> None:
    """ "Not run" and "run, nothing found" are different facts. A report that
    simply omits the section lets the first read as the second."""
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64), mode=Mode.REPORT_ONLY
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["terminology"] is None
    assert "Terminology validation: `not run`" in (report_dir / "report.md").read_text(
        encoding="utf-8"
    )


def test_workbook_text_cannot_break_the_markdown_table(tmp_path: Path) -> None:
    """A pipe or a line break in a finding is workbook data, not table syntax.

    Unescaped, ``|`` silently truncates the row to the first three columns and a
    ``\\r\\n`` puts a literal CRLF into report.md - breaking the writer's own
    LF-only guarantee. Both are exactly what P0-2's messages will contain.
    """
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        findings=(
            Finding(code="PIPE", location="B2", message="value was 'a|b'"),
            Finding(code="NEWLINE", location="B3", message="line one\r\nline two"),
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    raw = (report_dir / "report.md").read_bytes()
    assert b"\r\n" not in raw

    markdown_text = raw.decode("utf-8")
    assert "| B2 | PIPE | data-defect | value was 'a\\|b' |" in markdown_text
    assert "| B3 | NEWLINE | data-defect | line one<br>line two |" in markdown_text

    # Every row of the findings table still has exactly four columns - the
    # band summary table above it has a different (two-column) shape and is
    # excluded by locating the findings header explicitly.
    lines = markdown_text.splitlines()
    findings_header = lines.index("| Location | Code | Band | Message |")
    rows = [line for line in lines[findings_header + 2 :] if line.startswith("| ")]
    assert rows, "expected at least one findings row"
    for row in rows:
        assert len(row.split(" | ")) == 4, row
