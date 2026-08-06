"""FR-71: every finding is classified into exactly one of the three defect
bands (plus the non-defect ``INFORMATIONAL`` outcome FR-97/FR-75 need - see
``bands.py`` and ADR-0004), and the band determines whether the import
blocks."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nptc_transform.bands import BAND_BY_CODE, Band, FindingCode, band_for, blocks_import
from nptc_transform.cell_defects import scan_workbook
from nptc_transform.cli import app
from nptc_transform.findings import Finding
from nptc_transform.pipeline import Mode, run_transform
from nptc_transform.workbook import read_workbook

runner = CliRunner()


@pytest.mark.req("FR-71")
def test_registry_classifies_every_finding_code() -> None:
    """The one guarantee this module exists to provide: no ``FindingCode`` a
    detector can emit is left unclassified. Enforced at import time in
    ``bands.py`` too - this test keeps that invariant visible and would fail
    loudly if that check were ever weakened or removed."""
    assert set(FindingCode) == set(BAND_BY_CODE)


@pytest.mark.req("FR-71")
def test_unrecognised_code_fails_safe_to_data_defect() -> None:
    """A code this registry doesn't recognise must not read as clean - the
    principal failure mode of ``band_for`` is a detector emitting a code
    nobody registered, and the safe fallback is the band that blocks import."""
    assert band_for("SOME_FUTURE_CODE_NOBODY_REGISTERED") is Band.DATA_DEFECT
    assert Finding(code="UNKNOWN", location="Z1", message="x").band is Band.DATA_DEFECT


@pytest.mark.req("FR-71")
@pytest.mark.parametrize(
    ("band", "blocking"),
    [
        (Band.AUTO_CORRECTABLE, False),
        (Band.REQUIRES_HUMAN_DECISION, True),
        (Band.DATA_DEFECT, True),
        (Band.INFORMATIONAL, False),
    ],
)
def test_blocks_import_matches_fr71s_behaviour_table(band: Band, blocking: bool) -> None:
    assert blocks_import(band) is blocking


@pytest.mark.req("FR-71")
def test_every_finding_from_the_annex_a_fixture_has_exactly_one_band(
    annex_a_workbook: Path,
) -> None:
    """Every fixture row is assigned exactly one band (issue #25's acceptance
    criteria) - checked here at finding granularity, which is what FR-71
    itself classifies; a row's own effective status is the roll-up tested in
    ``test_a_row_with_mixed_bands_is_blocked_by_its_worst_finding`` below."""
    sheets = read_workbook(annex_a_workbook)
    findings = scan_workbook(sheets)
    assert findings, "fixture must produce at least one finding to be worth testing"
    for finding in findings:
        assert isinstance(finding.band, Band)


@pytest.mark.req("FR-71")
def test_representative_case_exists_for_every_band(
    annex_a_workbook: Path, no_spia_columns_workbook: Path
) -> None:
    """Issue #25: a fixture row for a representative case in each band."""
    sheets = read_workbook(annex_a_workbook) + read_workbook(no_spia_columns_workbook)
    bands_observed = {finding.band for finding in scan_workbook(sheets)}
    assert bands_observed == set(Band)


@pytest.mark.req("FR-71")
def test_a_row_with_mixed_bands_is_blocked_by_its_worst_finding(annex_a_workbook: Path) -> None:
    """Issue #25 speaks of a row's band; FR-71 classifies findings, and a row
    can carry findings in more than one band (H8: a 16-digit code stored as
    a number is both ``CODE_CELL_NOT_TEXT``, auto-correctable, and
    ``NUMERIC_PRECISION_RISK``, a data defect). The row's effective status is
    the worst of its findings: blocked if any of them blocks, however many of
    the others are merely auto-correctable."""
    sheets = read_workbook(annex_a_workbook)
    row_findings = [f for f in scan_workbook(sheets) if f.location == "Requesting!H8"]
    bands = {f.band for f in row_findings}
    assert Band.AUTO_CORRECTABLE in bands
    assert Band.DATA_DEFECT in bands
    assert any(blocks_import(f.band) for f in row_findings)


@pytest.mark.req("FR-71")
def test_run_result_band_counts_cover_every_band_and_sum_to_finding_count(
    annex_a_workbook: Path,
) -> None:
    result = run_transform(annex_a_workbook, mode=Mode.REPORT_ONLY)
    assert set(result.band_counts) == set(Band)
    assert sum(result.band_counts.values()) == len(result.findings)
    assert result.has_blocking_findings is True


@pytest.mark.req("FR-71")
def test_run_result_with_no_blocking_findings_does_not_block(
    auto_correctable_only_workbook: Path,
) -> None:
    result = run_transform(auto_correctable_only_workbook, mode=Mode.REPORT_ONLY)
    assert result.findings
    assert all(f.band is Band.AUTO_CORRECTABLE for f in result.findings)
    assert result.has_blocking_findings is False


@pytest.mark.req("FR-71")
def test_cli_exits_ok_when_no_finding_blocks(
    tmp_path: Path, auto_correctable_only_workbook: Path
) -> None:
    result = runner.invoke(
        app,
        ["run", "--workbook", str(auto_correctable_only_workbook), "--report-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "auto-correctable=" in result.output


@pytest.mark.req("FR-71")
def test_cli_exits_blocking_when_a_data_defect_is_present(
    tmp_path: Path, annex_a_workbook: Path
) -> None:
    result = runner.invoke(
        app, ["run", "--workbook", str(annex_a_workbook), "--report-dir", str(tmp_path)]
    )
    assert result.exit_code == 1, result.output
    assert "import blocked" in result.output


@pytest.mark.req("FR-71")
def test_cli_exits_blocking_on_genuine_layout_drift(
    tmp_path: Path, unrecognised_layout_workbook: Path
) -> None:
    result = runner.invoke(
        app, ["run", "--workbook", str(unrecognised_layout_workbook), "--report-dir", str(tmp_path)]
    )
    assert result.exit_code == 1, result.output


@pytest.mark.req("FR-71")
def test_cli_does_not_block_on_a_sheet_that_is_not_spia_data(
    tmp_path: Path, no_spia_columns_workbook: Path
) -> None:
    """The informational band exists precisely so a non-SPIA sheet (FR-63's
    ``Rev History``) does not abort the import it never belonged to."""
    result = runner.invoke(
        app, ["run", "--workbook", str(no_spia_columns_workbook), "--report-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
