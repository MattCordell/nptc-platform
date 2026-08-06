"""Orchestrates a single transform run.

This is the seam every later P0 issue plugs into: the workbook reader (P0-2),
cell-level defect detection and band classification (P0-3, FR-71) and batch
terminology validation (P0-5, over the ``nptc_shared.terminology`` client and
sweep landed with P0-4/P0-5) now produce and classify ``Finding`` values here;
designation reconciliation (P0-6) and the misspelling/semantic-drift
heuristics (P0-7) still plug in later.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from nptc_shared.terminology.models import Edition
from nptc_shared.terminology.sweep import TerminologySweep
from nptc_transform.bands import Band, blocks_import
from nptc_transform.cell_defects import scan_workbook
from nptc_transform.findings import Finding
from nptc_transform.terminology_check import (
    DEFAULT_EDITIONS,
    TerminologyRun,
    check_terminology,
)
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
    #: ``None`` when no terminology sweep ran - which is not the same as a
    #: sweep that found nothing, and is why this is not simply an empty
    #: record. The report says which of the two happened (FR-48).
    terminology: TerminologyRun | None = None

    def __post_init__(self) -> None:
        sorted_findings = tuple(sorted(self.findings, key=Finding.sort_key))
        object.__setattr__(self, "findings", sorted_findings)

    @property
    def band_counts(self) -> dict[Band, int]:
        """The number of findings in each band, including bands with zero.

        A property, not a stored field: it is entirely derived from
        ``findings``, and a dataclass field here would let a caller-supplied
        count disagree with the findings it claims to summarise. Every
        ``Band`` member is always present (as 0 if unobserved) so a report
        consumer never needs a defaulting lookup, and iteration order is
        always ``Band``'s declaration order, never set/dict insertion order
        (FR-73).
        """
        counts = dict.fromkeys(Band, 0)
        for finding in self.findings:
            counts[finding.band] += 1
        return counts

    @property
    def has_blocking_findings(self) -> bool:
        """True if any finding's band aborts the import (FR-71)."""
        return any(blocks_import(finding.band) for finding in self.findings)


def _hash_file(workbook: Path) -> str:
    return hashlib.sha256(workbook.read_bytes()).hexdigest()


def run_transform(
    workbook: Path,
    *,
    mode: Mode,
    sweep: TerminologySweep | None = None,
    editions: Sequence[Edition] = DEFAULT_EDITIONS,
) -> RunResult:
    """Runs the transform against ``workbook`` and returns its findings.

    Reads the workbook and scans every cell for PRD Appendix A.1-A.3 defects
    (P0-2); each finding is classified into its band by ``Finding.band`` as
    soon as it's constructed (P0-3, FR-71).

    ``sweep`` is optional and defaults to off. Terminology validation is the
    one part of this pipeline that talks to a server, so it is opted into
    explicitly (``--check-terminology``) rather than being a hidden network
    dependency of every run - and every test in ``transform/tests`` passes a
    stub-backed sweep or none at all, so the suite never needs the network
    (NFR-37).
    """
    source = SourceRef(filename=workbook.name, sha256=_hash_file(workbook))
    sheets = read_workbook(workbook)
    findings = scan_workbook(sheets)
    if sweep is None:
        return RunResult(source=source, mode=mode, findings=findings)
    outcome = check_terminology(sheets, sweep=sweep, editions=editions)
    return RunResult(
        source=source,
        mode=mode,
        findings=(*findings, *outcome.findings),
        terminology=outcome.run,
    )
