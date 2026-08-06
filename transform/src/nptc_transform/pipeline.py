"""Orchestrates a single transform run.

This is the seam every later P0 issue plugs into: the workbook reader (P0-2)
and cell-level defect detection now produce ``Finding`` values here; band
classification (P0-3), terminology validation (P0-4/P0-5), designation
reconciliation (P0-6) and the misspelling/semantic-drift heuristics (P0-7)
still plug in later. Nothing in this module classifies a finding into a
severity band yet - it only defines the shapes and the run/report contract
that FR-70 and FR-73 depend on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from nptc_transform.cell_defects import scan_workbook
from nptc_transform.findings import Finding
from nptc_transform.workbook import read_workbook


@dataclass(frozen=True)
class SourceRef:
    """Identifies the input workbook without embedding a machine-specific path.

    ``filename`` is the basename only - never the absolute path, which varies
    per machine and per test run and would break FR-73's byte-identical-output
    guarantee. ``sha256`` lets a report be tied back to the exact bytes that
    produced it without embedding the path.
    """

    filename: str
    sha256: str


class Mode(StrEnum):
    """The transform's two run modes."""

    REPORT_ONLY = "report-only"
    EMIT_DATASET = "emit-dataset"


@dataclass(frozen=True)
class RunResult:
    """The outcome of a single transform run, ready for the report writer."""

    source: SourceRef
    mode: Mode
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        sorted_findings = tuple(sorted(self.findings, key=Finding.sort_key))
        object.__setattr__(self, "findings", sorted_findings)


def _hash_file(workbook: Path) -> str:
    return hashlib.sha256(workbook.read_bytes()).hexdigest()


def run_transform(workbook: Path, *, mode: Mode) -> RunResult:
    """Runs the transform against ``workbook`` and returns its findings.

    Reads the workbook and scans every cell for PRD Appendix A.1-A.3 defects
    (P0-2). Severity band classification (P0-3) still plugs in later.
    """
    source = SourceRef(filename=workbook.name, sha256=_hash_file(workbook))
    sheets = read_workbook(workbook)
    findings = scan_workbook(sheets)
    return RunResult(source=source, mode=mode, findings=findings)
