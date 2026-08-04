"""Proves the workspace, the CLI entry point and the test runner are wired up.

Delete or replace once the real transform CLI lands with P0-1.
"""

from typer.testing import CliRunner

from nptc_transform import __version__
from nptc_transform.cli import app

runner = CliRunner()


def test_package_reports_a_version() -> None:
    assert __version__ == "0.0.0"


def test_cli_version_command_runs() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.0.0"
