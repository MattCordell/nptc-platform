"""Output must not depend on the clock, absolute paths, or dict/hash ordering (FR-73)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from nptc_transform.pipeline import Finding, Mode, RunResult, SourceRef

ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def test_run_result_sorts_findings_into_canonical_order() -> None:
    findings = (
        Finding(code="B", location="B2", message="second"),
        Finding(code="A", location="A1", message="first"),
        Finding(code="A", location="A1", message="also"),
    )

    result = RunResult(
        source=SourceRef(filename="x.xlsx", sha256="0" * 64),
        mode=Mode.REPORT_ONLY,
        findings=findings,
    )

    assert result.findings == (
        Finding(code="A", location="A1", message="also"),
        Finding(code="A", location="A1", message="first"),
        Finding(code="B", location="B2", message="second"),
    )
