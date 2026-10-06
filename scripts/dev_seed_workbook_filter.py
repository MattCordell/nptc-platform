#!/usr/bin/env python3
"""Writes a copy of a SPIA workbook without the rows that collide under FR-05, so the 50-row
sample can reach `nptc-transform run --emit-dataset` on a development stack (FR-05, FR-70).

The transform blocks `--emit-dataset` on a `DESIGNATION_COLLISION` finding, and the sample
excerpt carries one. The sample is a real excerpt that nobody edits, and a production workbook is
never filtered: RCPA-QAP resolves a collision at source (PRD 6.3). This tool is the dev seed's
way past it. It works on a temporary copy, never on the input.

A colliding entry is the later of the pair, as the loader meets it. Its cells are blanked, not
deleted, so every other row keeps its row number and the seed record still points at the real
workbook row. After one entry goes, the transform's check runs again, because an entry that clashed
only with the removed one no longer does.

Usage:
  uv run python scripts/dev_seed_workbook_filter.py --input in/sample.xlsx --output out/sample.xlsx

Run by `scripts/dev-seed.ps1`; see docs/operations/deployment.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from nptc_transform.collision_check import check_collisions
from nptc_transform.findings import Finding
from nptc_transform.workbook import Sheet, WorkbookReadError, read_workbook

#: 0 = a copy was written; 2 = usage error; 3 = the input is unreadable.
EXIT_OK = 0
EXIT_USAGE_ERROR = 2
EXIT_INPUT_REFUSED = 3


@dataclasses.dataclass(frozen=True)
class DroppedRow:
    sheet: str
    row: int
    reason: str


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, type=Path, help="The workbook to read.")
    parser.add_argument("--output", required=True, type=Path, help="Where to write the copy.")
    return parser.parse_args(argv)


def _without_row(sheets: tuple[Sheet, ...], finding: Finding) -> tuple[Sheet, ...]:
    location = finding.location
    return tuple(
        dataclasses.replace(sheet, cells=tuple(c for c in sheet.cells if c.row != location.row))
        if sheet.name == location.sheet
        else sheet
        for sheet in sheets
    )


def colliding_rows(sheets: tuple[Sheet, ...]) -> list[DroppedRow]:
    """The rows to drop, in the order they are dropped, until `check_collisions` finds none."""
    dropped: list[DroppedRow] = []
    while True:
        findings = check_collisions(sheets)
        if not findings:
            return dropped
        first = min(findings, key=Finding.sort_key)
        dropped.append(DroppedRow(first.location.sheet, first.location.row, first.message))
        sheets = _without_row(sheets, first)


def write_filtered_copy(source: Path, output: Path, dropped: list[DroppedRow]) -> None:
    workbook = openpyxl.load_workbook(source)
    for item in dropped:
        for cell in workbook[item.sheet][item.row]:
            cell.value = None
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        dropped = colliding_rows(read_workbook(args.input))
        write_filtered_copy(args.input, args.output, dropped)
    except (OSError, WorkbookReadError, InvalidFileException, KeyError) as exc:
        print(f"error: cannot filter {args.input} ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_INPUT_REFUSED

    for item in dropped:
        print(f"dropped {item.sheet!r} row {item.row}: {item.reason}")
    print(f"dropped {len(dropped)} colliding row(s)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
