"""Unit coverage for the report envelope, independent of the CLI (FR-73)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nptc_transform.cellref import CellRef
from nptc_transform.designation_check import DesignationRun
from nptc_transform.misspelling import THRESHOLDS, AuthoritySource, MisspellingRun
from nptc_transform.pipeline import Finding, Mode, RunResult, SourceRef
from nptc_transform.report_writer import SCHEMA_VERSION, write_report
from nptc_transform.semantic_drift import DriftRun
from nptc_transform.terminology_check import EditionResolution, TerminologyRun


def test_write_report_renders_findings_in_both_files(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        findings=(Finding(code="INVISIBLE_CHAR", location=CellRef("Sheet", "B", 2), message="zero-width space"),),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    json_text = (report_dir / "report.json").read_text(encoding="utf-8")
    assert '"finding_count": 1' in json_text
    assert "INVISIBLE_CHAR" in json_text

    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    # An unregistered code fails safe to data-defect (bands.band_for).
    assert "| Sheet!B2 | INVISIBLE_CHAR | data-defect | zero-width space |" in markdown_text


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
        "unresolved_fsn_count": 0,
    }
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "42 code(s) checked, 1 not checked" in markdown_text
    assert "| au | http://snomed.info/sct/32506021000036107/version/20260531 |" in markdown_text
    assert "no identifiable FSN designation" not in markdown_text


@pytest.mark.req("FR-99")
def test_a_nonzero_unresolved_fsn_count_is_rendered_not_silent(tmp_path: Path) -> None:
    """A concept the bulk expansion returned with no identifiable FSN means
    the FR-99 check could not run for it at all - that must be visible in
    both files, not just carried on an unread field."""
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        terminology=TerminologyRun(
            codes_checked=5,
            codes_not_checked=0,
            editions=(EditionResolution(label="au", resolved_versions=()),),
            unresolved_fsn_count=3,
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["terminology"]["unresolved_fsn_count"] == 3
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "3 concept(s) had no identifiable FSN designation" in markdown_text


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


@pytest.mark.req("FR-97")
def test_a_designation_run_records_its_provenance_counters(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        designations=DesignationRun(
            labels_reconciled=48, labels_not_reconciled=2, label_confirmations=1
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["designations"] == {
        "labels_reconciled": 48,
        "labels_not_reconciled": 2,
        "label_confirmations": 1,
    }
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "48 label(s) reconciled, 2 not reconciled, 1 confirmed against the server" in (
        markdown_text
    )


@pytest.mark.req("FR-97")
def test_a_run_with_no_designation_pass_says_so_rather_than_omitting_it(tmp_path: Path) -> None:
    """ "Not run" and "run, nothing found" are different facts here too - a
    clean workbook produces zero ``LABEL_*`` findings just as often as a
    reconciliation pass that never ran."""
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64), mode=Mode.REPORT_ONLY
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["designations"] is None
    assert "Designation reconciliation: `not run`" in (report_dir / "report.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.req("FR-79")
def test_a_misspelling_run_records_its_thresholds_verbatim(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        misspellings=MisspellingRun(
            cells_scanned=10,
            tokens_considered=20,
            probable_misspelling_count=1,
            inconsistent_spelling_count=2,
            authority_source=AuthoritySource.SWEEP,
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION == 6
    assert payload["misspellings"]["thresholds"] == THRESHOLDS
    assert payload["misspellings"]["authority_source"] == "SWEEP"
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "1 probable misspelling(s), 2 inconsistent spelling(s)" in markdown_text
    assert "authority whitelist is empty" not in markdown_text


@pytest.mark.req("FR-79")
def test_a_workbook_only_misspelling_run_states_the_precision_caveat_explicitly(
    tmp_path: Path,
) -> None:
    """A sweep-backed run and a workbook-only run must not read the same in
    the report - the reliability difference is exactly what a reader needs
    to know before acting on a finding."""
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        misspellings=MisspellingRun(authority_source=AuthoritySource.WORKBOOK_ONLY),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["misspellings"]["authority_source"] == "WORKBOOK_ONLY"
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "authority whitelist is empty" in markdown_text


@pytest.mark.req("FR-79")
def test_a_run_with_no_misspelling_pass_says_so_rather_than_omitting_it(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64), mode=Mode.REPORT_ONLY
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["misspellings"] is None
    assert "Misspelling detection: `not run`" in (report_dir / "report.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.req("FR-75")
def test_a_drift_run_records_its_provenance_counters(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        drift=DriftRun(
            rows_examined=4,
            rows_excluded=1,
            term_specimen_not_modelled_count=2,
            term_specimen_differs_count=1,
            term_timing_not_modelled_count=1,
            specimen_table_entries_unresolved=1,
            specimen_column_values_unmapped=1,
            describe_requests=1,
            classification_requests=3,
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION == 6
    assert payload["drift"] == {
        "rows_examined": 4,
        "rows_excluded": 1,
        "term_specimen_not_modelled_count": 2,
        "term_specimen_differs_count": 1,
        "term_timing_not_modelled_count": 1,
        "specimen_table_entries_unresolved": 1,
        "specimen_column_values_unmapped": 1,
        "describe_requests": 1,
        "classification_requests": 3,
        "resolved_versions": [],
    }
    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "4 row(s) examined, 1 not examined" in markdown_text
    assert "1 specimen-table concept(s) could not be resolved" in markdown_text
    assert "1 distinct `Specimen` column value(s) map to no group" in markdown_text


@pytest.mark.req("FR-75")
def test_a_drift_run_with_zero_unresolved_counters_omits_their_lines(tmp_path: Path) -> None:
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64),
        mode=Mode.REPORT_ONLY,
        drift=DriftRun(rows_examined=4, rows_excluded=0),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    markdown_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "could not be resolved" not in markdown_text
    assert "map to no group" not in markdown_text


@pytest.mark.req("FR-75")
def test_a_run_with_no_drift_pass_says_so_rather_than_omitting_it(tmp_path: Path) -> None:
    """ "Not run" and "run, nothing found" are different facts here too - a
    clean workbook produces zero ``TERM_*`` findings just as often as a
    semantic-drift pass that never ran."""
    result = RunResult(
        source=SourceRef(filename="sample.xlsx", sha256="a" * 64), mode=Mode.REPORT_ONLY
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["drift"] is None
    assert "Semantic drift review: `not run`" in (report_dir / "report.md").read_text(
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
            Finding(code="PIPE", location=CellRef("Sheet", "B", 2), message="value was 'a|b'"),
            Finding(code="NEWLINE", location=CellRef("Sheet", "B", 3), message="line one\r\nline two"),
        ),
    )
    report_dir = tmp_path / "report"

    write_report(result, report_dir)

    raw = (report_dir / "report.md").read_bytes()
    assert b"\r\n" not in raw

    markdown_text = raw.decode("utf-8")
    assert "| Sheet!B2 | PIPE | data-defect | value was 'a\\|b' |" in markdown_text
    assert "| Sheet!B3 | NEWLINE | data-defect | line one<br>line two |" in markdown_text

    # Every row of the findings table still has exactly four columns - the
    # band summary table above it has a different (two-column) shape and is
    # excluded by locating the findings header explicitly.
    lines = markdown_text.splitlines()
    findings_header = lines.index("| Location | Code | Band | Message |")
    rows = [line for line in lines[findings_header + 2 :] if line.startswith("| ")]
    assert rows, "expected at least one findings row"
    for row in rows:
        assert len(row.split(" | ")) == 4, row
