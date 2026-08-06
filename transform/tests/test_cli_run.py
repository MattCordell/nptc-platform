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

    # Exactly the report dir and its two files - not "something starting with
    # report", which would pass for a stray sibling too.
    assert _tree(tmp_path) - before == {
        str(Path("report")),
        str(Path("report/report.json")),
        str(Path("report/report.md")),
    }
    # The default --report-dir is relative, so a regression that ignores the
    # option writes into the process CWD, which is outside tmp_path entirely.
    assert not (Path.cwd() / "transform-report").exists()


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
def test_report_only_can_be_stated_explicitly(tmp_path: Path, sample_workbook: Path) -> None:
    report_dir = tmp_path / "report"

    result = runner.invoke(
        app,
        [
            "run",
            "--workbook",
            str(sample_workbook),
            "--report-dir",
            str(report_dir),
            "--report-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (report_dir / "report.json").is_file()


@pytest.mark.req("FR-70")
def test_report_only_and_emit_dataset_together_are_a_usage_error(
    tmp_path: Path, sample_workbook: Path
) -> None:
    """Asking for both modes at once must be refused, not silently resolved."""
    report_dir = tmp_path / "report"

    result = runner.invoke(
        app,
        [
            "run",
            "--workbook",
            str(sample_workbook),
            "--report-dir",
            str(report_dir),
            "--report-only",
            "--emit-dataset",
        ],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
    assert not report_dir.exists()


@pytest.mark.req("FR-70")
def test_report_dir_pointing_at_a_file_is_a_usage_error(
    tmp_path: Path, sample_workbook: Path
) -> None:
    """An unwritable --report-dir must explain itself, not raise a traceback.

    Exit 1 is reserved for P0-3's blocking findings, so a filesystem refusal
    has to land on 2 with a message the operator can act on.
    """
    not_a_dir = tmp_path / "report"
    not_a_dir.write_text("", encoding="utf-8")

    result = runner.invoke(
        app, ["run", "--workbook", str(sample_workbook), "--report-dir", str(not_a_dir)]
    )

    assert result.exit_code == 2, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output


@pytest.mark.req("FR-70")
def test_missing_workbook_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "--workbook", str(tmp_path / "does-not-exist.xlsx")])

    assert result.exit_code == 2


@pytest.mark.req("FR-70")
def test_unreadable_workbook_is_a_usage_error_not_a_traceback(
    tmp_path: Path, corrupt_workbook: Path
) -> None:
    """A file that exists and is readable but isn't a valid workbook (P0-2)
    must be refused the same way a missing file is - exit 2, no traceback."""
    report_dir = tmp_path / "report"

    result = runner.invoke(
        app, ["run", "--workbook", str(corrupt_workbook), "--report-dir", str(report_dir)]
    )

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert not report_dir.exists()
