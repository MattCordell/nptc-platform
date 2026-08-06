"""CLI entry point for the P0 seeding transform.

``run`` (FR-70, FR-73) reads the SPIA workbook and writes a report. Report-only
is the default mode; ``--emit-dataset`` is reserved for the mutating mode that
lands with backlog issue P0-9. ``--check-terminology`` opts into the FR-52
batch validation pass, which is the only part of the tool that opens a network
connection.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer

from nptc_shared.terminology.config import TerminologyConfig
from nptc_shared.terminology.errors import TerminologyConfigError, TerminologyError
from nptc_shared.terminology.ontoserver import OntoserverClient
from nptc_shared.terminology.sweep import TerminologySweep
from nptc_transform import __version__
from nptc_transform.bands import Band
from nptc_transform.pipeline import Mode, RunResult, read_source, run_transform_sheets
from nptc_transform.report_writer import write_report
from nptc_transform.workbook import WorkbookReadError

app = typer.Typer(
    name="nptc-transform",
    help="Convert the published SPIA Requesting workbook into an import dataset "
    "or a defect report.",
    no_args_is_help=True,
)


class ExitCode(IntEnum):
    """Process exit codes this CLI uses."""

    OK = 0
    BLOCKING_FINDINGS = 1  # any finding banded requires-human-decision or data-defect (FR-71)
    USAGE_ERROR = 2
    TERMINOLOGY_UNAVAILABLE = 3  # the sweep could not complete (FR-54)


@app.callback()
def _callback() -> None:
    """Convert the published SPIA Requesting workbook into an import dataset or a defect report."""


@contextmanager
def _terminology_sweep(enabled: bool) -> Iterator[TerminologySweep | None]:
    """A configured sweep, or ``None`` when terminology checking is off.

    The client is built from ``NPTC_TX_*`` (see
    docs/operations/configuration.md) and closed on the way out, whatever the
    run did. Nothing is constructed at all when the flag is off, so a plain
    ``run`` neither reads terminology configuration nor opens a connection.
    """
    if not enabled:
        yield None
        return
    config = TerminologyConfig.from_env()
    with OntoserverClient(config) as client:
        yield TerminologySweep.from_config(client, config)


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
        typer.Option(
            file_okay=False,
            writable=True,
            help="Directory the report files are written into.",
        ),
    ] = Path("transform-report"),
    report_only: Annotated[
        bool,
        typer.Option(
            "--report-only",
            help="Write a report and mutate nothing. The default; accepted explicitly "
            "so the mode can be stated in a script.",
        ),
    ] = False,
    emit_dataset: Annotated[
        bool,
        typer.Option(
            "--emit-dataset",
            help="Emit the import dataset instead of a report. Not implemented yet (P0-9).",
        ),
    ] = False,
    check_terminology: Annotated[
        bool,
        typer.Option(
            "--check-terminology",
            help="Validate every code binding against SNOMED CT-AU and International "
            "(FR-52, FR-74, FR-84). Requires network access to the terminology "
            "server configured by NPTC_TX_BASE_URL.",
        ),
    ] = False,
) -> None:
    """Run the transform against WORKBOOK.

    Report-only by default (FR-70), and --report-only says so explicitly:
    writes report.json and report.md into --report-dir and nothing else. No
    file outside --report-dir is ever touched. --emit-dataset opts into the
    mutating mode, which is not implemented yet. --check-terminology opts into
    the batch validation pass, the only part of the run that uses the network.
    """
    typer.echo(f"nptc-transform {__version__}: starting", err=True)

    if report_only and emit_dataset:
        typer.echo("--report-only and --emit-dataset are mutually exclusive", err=True)
        raise typer.Exit(code=ExitCode.USAGE_ERROR)

    if emit_dataset:
        typer.echo("dataset emission is not implemented yet (backlog P0-9)", err=True)
        raise typer.Exit(code=ExitCode.USAGE_ERROR)

    start = time.monotonic()

    # Read the workbook before opening any network connection: a corrupt or
    # missing workbook is a usage error the operator needs to see, and a
    # malformed NPTC_TX_* value should never pre-empt that clearer message
    # just because client construction happened to run first.
    try:
        source, sheets = read_source(workbook)
    except WorkbookReadError as exc:
        # WorkbookReadError's own message already names the path and reason -
        # echo it as-is rather than wrapping it a second time.
        typer.echo(f"{exc}. Pass --workbook a valid, readable .xlsx file.", err=True)
        raise typer.Exit(code=ExitCode.USAGE_ERROR) from exc

    result: RunResult
    try:
        with _terminology_sweep(check_terminology) as sweep:
            result = run_transform_sheets(source, sheets, mode=Mode.REPORT_ONLY, sweep=sweep)
    except TerminologyConfigError as exc:
        # A subclass of TerminologyError, caught ahead of it: a malformed
        # NPTC_TX_* value is a deployment typo, not a server outage, and
        # belongs in the usage-error exit code an operator or CI would fix
        # rather than retry.
        typer.echo(
            f"invalid terminology configuration: {exc}. Check the NPTC_TX_* environment "
            "variables (see docs/operations/configuration.md).",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USAGE_ERROR) from exc
    except TerminologyError as exc:
        # No report is written at all. A partial report - cell defects
        # complete, terminology findings silently missing - is the FR-54
        # hazard in its most dangerous form: it would look exactly like a run
        # in which every code validated cleanly.
        typer.echo(
            f"terminology validation failed: {exc}. No report was written. "
            "Re-run without --check-terminology to produce the cell-defect report alone.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.TERMINOLOGY_UNAVAILABLE) from exc
    try:
        write_report(result, report_dir)
    except OSError as exc:
        # Anything the filesystem refuses is the operator's to fix, so say which
        # path and why - never a traceback, and never exit 1, which is reserved
        # for "the report itself contains blocking findings".
        typer.echo(
            f"could not write the report into {report_dir}: {exc.strerror or exc}. "
            "Pass --report-dir a writable directory path.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USAGE_ERROR) from exc
    elapsed = time.monotonic() - start
    typer.echo(f"nptc-transform: wrote report to {report_dir} in {elapsed:.2f}s", err=True)

    band_counts = result.band_counts
    summary = ", ".join(f"{band}={band_counts[band]}" for band in Band)
    typer.echo(f"nptc-transform: bands: {summary}", err=True)

    if result.has_blocking_findings:
        typer.echo(
            "nptc-transform: import blocked - the report contains at least one "
            "requires-human-decision or data-defect finding (FR-71)",
            err=True,
        )
        raise typer.Exit(code=ExitCode.BLOCKING_FINDINGS)
    raise typer.Exit(code=ExitCode.OK)


if __name__ == "__main__":  # pragma: no cover
    app()
