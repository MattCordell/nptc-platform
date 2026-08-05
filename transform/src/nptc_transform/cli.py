"""CLI entry point for the P0 seeding transform.

``run`` (FR-70, FR-73) reads the SPIA workbook and writes a report. Report-only
is the default mode; ``--emit-dataset`` is reserved for the mutating mode that
lands with backlog issue P0-9.
"""

from __future__ import annotations

import time
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer

from nptc_transform import __version__
from nptc_transform.pipeline import Mode, run_transform
from nptc_transform.report_writer import write_report

app = typer.Typer(
    name="nptc-transform",
    help="Convert the published SPIA Requesting workbook into an import dataset "
    "or a defect report.",
    no_args_is_help=True,
)


class ExitCode(IntEnum):
    """Process exit codes this CLI uses."""

    OK = 0
    BLOCKING_FINDINGS = 1  # reserved for P0-3's band classification; unreachable today
    USAGE_ERROR = 2


@app.callback()
def _callback() -> None:
    """Convert the published SPIA Requesting workbook into an import dataset or a defect report."""


@app.command()
def version() -> None:
    """Print the transform's version and exit."""
    typer.echo(__version__)


@app.command()
def run(
    workbook: Annotated[
        Path,
        typer.Option(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to the published SPIA Requesting workbook (.xlsx).",
        ),
    ],
    report_dir: Annotated[
        Path,
        typer.Option(help="Directory the report files are written into."),
    ] = Path("transform-report"),
    emit_dataset: Annotated[
        bool,
        typer.Option(
            "--emit-dataset",
            help="Emit the import dataset instead of a report. Not implemented yet (P0-9).",
        ),
    ] = False,
) -> None:
    """Run the transform against WORKBOOK.

    Report-only by default (FR-70): writes report.json and report.md into
    --report-dir and nothing else. No file outside --report-dir is ever
    touched. --emit-dataset opts into the mutating mode, which is not
    implemented yet.
    """
    typer.echo(f"nptc-transform {__version__}: starting", err=True)

    if emit_dataset:
        typer.echo("dataset emission is not implemented yet (backlog P0-9)", err=True)
        raise typer.Exit(code=ExitCode.USAGE_ERROR)

    start = time.monotonic()
    result = run_transform(workbook, mode=Mode.REPORT_ONLY)
    write_report(result, report_dir)
    elapsed = time.monotonic() - start
    typer.echo(f"nptc-transform: wrote report to {report_dir} in {elapsed:.2f}s", err=True)
    raise typer.Exit(code=ExitCode.OK)


if __name__ == "__main__":  # pragma: no cover
    app()
