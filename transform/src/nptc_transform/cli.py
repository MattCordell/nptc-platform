"""CLI entry point for the P0 seeding transform.

Scaffolding only - proves the ``nptc-transform`` console script installs and runs.
The real subcommands (``run``, with ``--report-only`` per FR-70) land with P0-1.
"""

from __future__ import annotations

import typer

from nptc_transform import __version__

app = typer.Typer(
    name="nptc-transform",
    help="Convert the published SPIA Requesting workbook into an import dataset "
    "or a defect report.",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Convert the published SPIA Requesting workbook into an import dataset or a defect report.

    A no-op callback: its only job is to keep Typer in multi-command mode so
    ``version`` stays an explicit subcommand rather than collapsing into the
    single default command once ``run`` (P0-1) makes this a two-command CLI.
    """


@app.command()
def version() -> None:
    """Print the transform's version and exit."""
    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
