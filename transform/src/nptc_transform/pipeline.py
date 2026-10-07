"""Orchestrates a single transform run.

This is where the passes that produce ``Finding`` values meet: the workbook
reader, cell-level defect detection and band classification (FR-71), batch
terminology validation over the ``nptc_shared.terminology`` client and sweep,
designation reconciliation (FR-97, over the sweep's results; see
``designation_check.py``), the FR-79 misspelling heuristics (over the sweep's
results when available; see ``misspelling.py``), the FR-75 semantic-drift check
(``semantic_drift.py``), the specimen map's code check (``specimen_map_check.py``) and
the FR-05 collision check (``collision_check.py``). Report
content grouped by defect class with required actions (FR-72) is ``report_writer.py``'s.
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
from nptc_transform.collision_check import check_collisions
from nptc_transform.designation_check import DesignationRun, check_designations
from nptc_transform.findings import Finding
from nptc_transform.misspelling import MisspellingRun, check_misspellings
from nptc_transform.semantic_drift import DriftRun, check_semantic_drift
from nptc_transform.specimen_map_check import SpecimenMapRun, check_specimen_map
from nptc_transform.terminology_check import (
    DEFAULT_EDITIONS,
    TerminologyRun,
    check_terminology,
)
from nptc_transform.workbook import Sheet, read_workbook


@dataclass(frozen=True)
class SourceRef:
    """Identifies the input workbook without embedding a machine-specific path.

    ``filename`` is the basename only, never the absolute path, which varies per
    machine and run and would break FR-73's byte-identical output. ``sha256`` ties
    a report to the exact bytes that produced it.
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
    #: ``None`` when no terminology sweep ran, which differs from a sweep that
    #: found nothing, hence not an empty record. The report says which happened
    #: (FR-48).
    terminology: TerminologyRun | None = None
    #: ``None`` under the same condition as ``terminology``: designation
    #: reconciliation (FR-97) rides on the same sweep.
    designations: DesignationRun | None = None
    #: Never ``None`` in a pipeline run: the FR-79 heuristics run with or without
    #: a sweep, and only their authority whitelist differs
    #: (``MisspellingRun.authority_source``).
    misspellings: MisspellingRun | None = None
    #: ``None`` under the same condition as ``terminology``: FR-75's
    #: semantic-drift check needs a live ``sweep`` for its own requests
    #: (``describe``, ``codes_without_attribute``, ``codes_with_attribute_value``).
    drift: DriftRun | None = None
    #: ``None`` under the same condition as ``terminology``: the specimen map's
    #: codes are checked against the server (ADR-0044).
    specimen_map: SpecimenMapRun | None = None

    def __post_init__(self) -> None:
        sorted_findings = tuple(sorted(self.findings, key=Finding.sort_key))
        object.__setattr__(self, "findings", sorted_findings)

    @property
    def band_counts(self) -> dict[Band, int]:
        """The number of findings in each band, including bands with zero.

        A property, not a field, so a caller-supplied count cannot disagree with
        the findings it summarises. Every ``Band`` member is present (0 if
        unobserved), so consumers need no defaulting lookup, and iteration follows
        ``Band``'s declaration order, never insertion order (FR-73).
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

    Split from ``run_transform`` so a caller opening a network connection for
    --check-terminology (``cli.py``) can read first and surface
    ``WorkbookReadError`` before building the client. A corrupt workbook is a
    usage error, not a terminology-server failure.
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

    Scans every cell for PRD Appendix A.1-A.3 defects; ``Finding.band`` classifies
    each finding when it is constructed (FR-71).

    ``sweep`` is optional and off by default. Terminology validation is the one
    part of the pipeline that talks to a server, so it is opted into
    (``--check-terminology``), not a hidden network dependency of every run. Every
    test in ``transform/tests`` passes a stub-backed sweep or none, so the suite
    needs no network (NFR-37).
    """
    findings = (*scan_workbook(sheets), *check_collisions(sheets))
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
    drift = check_semantic_drift(
        sheets,
        sweep=sweep,
        bindings=outcome.bindings,
        results=outcome.results,
    )
    specimen_map = check_specimen_map(sweep)
    return RunResult(
        source=source,
        mode=mode,
        findings=(
            *findings,
            *outcome.findings,
            *designations.findings,
            *misspellings.findings,
            *drift.findings,
            *specimen_map.findings,
        ),
        terminology=outcome.run,
        designations=designations.run,
        misspellings=misspellings.run,
        drift=drift.run,
        specimen_map=specimen_map.run,
    )


def run_transform(
    workbook: Path,
    *,
    mode: Mode,
    sweep: TerminologySweep | None = None,
    editions: Sequence[Edition] = DEFAULT_EDITIONS,
) -> RunResult:
    """Runs the transform against ``workbook`` and returns its findings.

    ``read_source`` then ``run_transform_sheets``, as one call for callers with no
    reason to split them (every test here, and any caller that never contacts a
    terminology server).
    """
    source, sheets = read_source(workbook)
    return run_transform_sheets(source, sheets, mode=mode, sweep=sweep, editions=editions)
