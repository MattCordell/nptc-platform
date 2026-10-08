"""Verifies every code in the reviewed specimen map against SNOMED CT-AU (FR-88, ADR-0044).

Runs with ``--check-terminology``, beside the workbook's own code checks. One ``AND <<123038009``
request per chunk of codes answers it, and a code missing from the answer fails whatever the
reason: absent, inactive or outside the specimen hierarchy. The finding points at the map's own
``Target code`` cell, because the fix is a correction to the map, not to the workbook.

A second request resolves each passing code's AU preferred term, which the import dataset stores as
the specimen's ``display`` (``dataset.py``). The map's own ``Target display`` is the FSN with its
tag, and the workbook wording is not a SNOMED CT term, so neither can stand in for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from nptc_shared.terminology.models import SNOMED_CT_AU, SPECIMEN_ROOT_CODE
from nptc_shared.terminology.sweep import TerminologySweep
from nptc_transform.bands import FindingCode
from nptc_transform.cellref import CellRef
from nptc_transform.findings import Finding
from nptc_transform.specimen_map import SPECIMEN_MAP, SPECIMEN_MAP_FILE, SpecimenMap


@dataclass(frozen=True)
class SpecimenMapRun:
    """What the pass covered, for the report's provenance block (FR-48)."""

    codes_checked: int
    resolved_versions: tuple[str, ...]
    #: ``(code, AU preferred term)`` for every code that passed, sorted by code. Not in the report.
    preferred_terms: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SpecimenMapOutcome:
    findings: tuple[Finding, ...]
    run: SpecimenMapRun


def check_specimen_map(
    sweep: TerminologySweep, specimen_map: SpecimenMap = SPECIMEN_MAP
) -> SpecimenMapOutcome:
    versions: set[str] = set()
    codes = specimen_map.codes
    within_root = frozenset(
        sweep.codes_subsumed_by(
            codes, root=SPECIMEN_ROOT_CODE, edition=SNOMED_CT_AU, versions=versions
        )
    )
    served = sweep.describe(sorted(within_root), edition=SNOMED_CT_AU, versions=versions)
    preferred_terms = {entry.code: entry.display for entry in served if entry.display}
    findings = tuple(
        Finding(
            code=FindingCode.SPECIMEN_MAP_CODE_OUT_OF_SCOPE,
            location=CellRef(SPECIMEN_MAP_FILE, specimen_map.code_column, entry.line),
            message=(
                f"specimen map string '{entry.source}' maps to {entry.code}, which is not an "
                f"active concept under <<{SPECIMEN_ROOT_CODE} in SNOMED CT-AU"
            ),
        )
        for entry in specimen_map.entries
        if entry.code is not None and entry.code not in within_root
    )
    no_term = tuple(
        Finding(
            code=FindingCode.SPECIMEN_MAP_NO_PREFERRED_TERM,
            location=CellRef(SPECIMEN_MAP_FILE, specimen_map.code_column, entry.line),
            message=(
                f"specimen map string '{entry.source}' maps to {entry.code}, but SNOMED CT-AU "
                "served no preferred term for it"
            ),
        )
        for entry in specimen_map.entries
        if entry.code in within_root and entry.code not in preferred_terms
    )
    return SpecimenMapOutcome(
        findings=(*findings, *no_term),
        run=SpecimenMapRun(
            codes_checked=len(codes),
            resolved_versions=tuple(sorted(versions)),
            preferred_terms=tuple(sorted(preferred_terms.items())),
        ),
    )
