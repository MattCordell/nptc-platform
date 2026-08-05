"""Report-only is the default mode; --emit-dataset refuses cleanly (FR-70)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nptc_transform import __version__
from nptc_transform.cli import app

runner = CliRunner()


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_cli_version_command_runs() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


@pytest.mark.req("FR-70")
def test_report_only_is_the_default_and_writes_only_the_report_dir(
    tmp_path: Path, sample_workbook: Path
) -> None:
    report_dir = tmp_path / "report"
    before = _tree(tmp_path)

    result = runner.invoke(
        app, ["run", "--workbook", str(sample_workbook), "--report-dir", str(report_dir)]
    )

    assert result.exit_code == 0, result.output
    assert (report_dir / "report.json").is_file()
    assert (report_dir / "report.md").is_file()

    after = _tree(tmp_path)
    changed = after - before
    assert all(str(Path(p)).startswith("report") for p in changed), changed


@pytest.mark.req("FR-70")
def test_emit_dataset_refuses_and_writes_nothing(tmp_path: Path, sample_workbook: Path) -> None:
    report_dir = tmp_path / "report"
    before = _tree(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "--workbook",
            str(sample_workbook),
            "--report-dir",
            str(report_dir),
            "--emit-dataset",
        ],
    )

    assert result.exit_code == 2
    assert "P0-9" in result.output
    assert _tree(tmp_path) == before
    assert not report_dir.exists()


@pytest.mark.req("FR-70")
def test_missing_workbook_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "--workbook", str(tmp_path / "does-not-exist.xlsx")])

    assert result.exit_code == 2
