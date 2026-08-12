"""Orchestrates a single transform run.

This is the seam every later P0 issue plugs into: the workbook reader (P0-2),
cell-level defect detection and band classification (P0-3, FR-71), batch
terminology validation (P0-5, over the ``nptc_shared.terminology`` client and
sweep landed with P0-4/P0-5), designation reconciliation (P0-6, FR-97,
over the same sweep's results - see ``designation_check.py``) and the FR-79
misspelling heuristics (P0-7, over the sweep's results when available - see
``misspelling.py``) now produce and classify ``Finding`` values here; report
content grouped by defect class (P0-8) still plugs in later.
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
from nptc_transform.designation_check import DesignationRun, check_designations
from nptc_transform.findings import Finding
from nptc_transform.misspelling import MisspellingRun, check_misspellings
from nptc_transform.terminology_check import (
    DEFAULT_EDITIONS,
    TerminologyRun,
    check_terminology,
)
from nptc_transform.workbook import Sheet, read_workbook


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
    #: ``None`` under the same condition as ``terminology`` - designation
    #: reconciliation (FR-97) rides on the same sweep and never runs without it.
    designations: DesignationRun | None = None
    #: Never ``None`` - unlike ``terminology``/``designations``, the FR-79
    #: misspelling heuristics run whether or not a sweep is available; only
    #: the authority whitelist they use differs (``MisspellingRun.authority_source``).
    misspellings: MisspellingRun | None = None

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


def read_source(workbook: Path) -> tuple[SourceRef, tuple[Sheet, ...]]:
    """Reads and hashes ``workbook`` once (FR-70, FR-73).

    Split out from ``run_transform`` so a caller that also opens a network
    connection for --check-terminology (``cli.py``) can do this first and
    surface ``WorkbookReadError`` before that connection is ever built - a
    corrupt workbook is a usage error the operator needs to see, not a
    terminology-server failure, and it should not pay for client setup it
    never needed either way.
    """
    source = SourceRef(filename=workbook.name, sha256=_hash_file(workbook))
    sheets = read_workbook(workbook)
    return source, sheets


def run_transform_sheets(
    source: SourceRef,
    sheets: tuple[Sheet, ...],
    *,
    mode: Mode,
    sweep: TerminologySweep | None = None,
    editions: Sequence[Edition] = DEFAULT_EDITIONS,
) -> RunResult:
    """The rest of ``run_transform``, given an already-read workbook.

    Scans every cell for PRD Appendix A.1-A.3 defects (P0-2); each finding is
    classified into its band by ``Finding.band`` as soon as it's constructed
    (P0-3, FR-71).

    ``sweep`` is optional and defaults to off. Terminology validation is the
    one part of this pipeline that talks to a server, so it is opted into
    explicitly (``--check-terminology``) rather than being a hidden network
    dependency of every run - and every test in ``transform/tests`` passes a
    stub-backed sweep or none at all, so the suite never needs the network
    (NFR-37).
    """
    findings = scan_workbook(sheets)
    if sweep is None:
        misspellings = check_misspellings(sheets)
        return RunResult(
            source=source,
            mode=mode,
            findings=(*findings, *misspellings.findings),
            misspellings=misspellings.run,
        )
    outcome = check_terminology(sheets, sweep=sweep, editions=editions)
    designations = check_designations(
        sheets,
        sweep=sweep,
        bindings=outcome.bindings,
        results=outcome.results,
        editions=editions,
    )
    misspellings = check_misspellings(sheets, results=outcome.results)
    return RunResult(
        source=source,
        mode=mode,
        findings=(*findings, *outcome.findings, *designations.findings, *misspellings.findings),
        terminology=outcome.run,
        designations=designations.run,
        misspellings=misspellings.run,
    )


def run_transform(
    workbook: Path,
    *,
    mode: Mode,
    sweep: TerminologySweep | None = None,
    editions: Sequence[Edition] = DEFAULT_EDITIONS,
) -> RunResult:
    """Runs the transform against ``workbook`` and returns its findings.

    ``read_source`` then ``run_transform_sheets`` - kept as one call for
    every caller that has no reason to split the two (every test in this
    tree, and any future caller that never talks to a terminology server).
    """
    source, sheets = read_source(workbook)
    return run_transform_sheets(source, sheets, mode=mode, sweep=sweep, editions=editions)
