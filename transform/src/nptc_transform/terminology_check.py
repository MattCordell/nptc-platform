"""Turns the workbook's code bindings into terminology findings (FR-74).

The batch validation *engine* is not here - it is
``nptc_shared.terminology.sweep``, shared with the backend, because FR-74
forbids the migration path having its own implementation of anything the
backend also validates. What is here is the translation either side of it:
workbook cells in, ``Finding`` values with cell references out (FR-72).

Three things this module owns, none of which the engine can decide:

- **Which codes are even askable.** A cell whose text is not a well-formed
  SCTID never reaches the server; ``ecl_set_of`` would refuse it, and a code
  that fails the Verhoeff check digit is already a defect in its own right
  (FR-06, and FR-71's data-defect band names it first). It is reported
  against its cell and excluded from the sweep.
- **How two editions combine.** FR-74 validates against both, and FR-71 makes
  "codes not resolving in **either** edition" the defect - so a code present
  in SNOMED CT-AU and absent from International is the expected shape of
  Australian extension content (FR-47 case 1), not a finding.
- **Where a finding points.** The engine works in codes; RCPA-QAP works in
  cells. A code bound by three rows produces three findings, each citing its
  own cell.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from nptc_shared.sctid import has_valid_check_digit
from nptc_shared.terminology.models import SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL, Edition
from nptc_shared.terminology.sweep import SweepResult, TerminologySweep
from nptc_shared.text import escape_invisible
from nptc_transform.bands import FindingCode
from nptc_transform.cellref import CellRef
from nptc_transform.findings import Finding
from nptc_transform.workbook import CellType, ColumnRole, Sheet

#: FR-74/FR-47: every code is validated against both editions, latest release
#: of each (FR-49 - no version pinned, the server reports what it resolved).
DEFAULT_EDITIONS: tuple[Edition, ...] = (SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL)


@dataclass(frozen=True)
class CodeBinding:
    """One code cell: the code it holds, the cell it came from, and the
    cell's original storage type - carried through so ``check_terminology``
    can tell a genuine code apart from a non-text cell's rendered value
    (a date's ISO string, a corrupted number's ``repr(float)``) without a
    second, divergent notion of "what counts as a code cell" from
    ``cell_defects.py``'s.
    """

    code: str
    location: CellRef
    cell_type: CellType


@dataclass(frozen=True)
class EditionResolution:
    """Which release of one edition a run actually resolved against (FR-48)."""

    label: str
    resolved_versions: tuple[str, ...]


@dataclass(frozen=True)
class TerminologyRun:
    """What the terminology pass covered, for the report's provenance block.

    ``codes_not_checked`` is not decoration: a run that skipped a third of
    the catalogue because those cells were malformed must not read as a run
    that validated all of it (the same reason ``UNRECOGNISED_LAYOUT`` reports
    its unscanned row count).

    The two counts intentionally use different units, so they are not meant
    to be added together: ``codes_checked`` is the number of *distinct codes*
    the sweep actually queried (a code bound by three checkable cells still
    counts once - the engine works in codes, FR-72's own docstring), while
    ``codes_not_checked`` is the number of *bindings* excluded before the
    sweep ran (one per skipped cell, even if its code string also reached the
    server via a different, checkable cell) - counting bindings rather than
    distinct codes is what keeps a binding that was genuinely skipped from
    being silently absorbed by another cell that happened to share its code
    string (issue #130).
    """

    codes_checked: int
    codes_not_checked: int
    editions: tuple[EditionResolution, ...]
    #: Concepts resolved during this run for which no edition's sweep could
    #: identify an FSN designation, summed across editions - see
    #: ``SweepResult.unresolved_fsn_count``. Zero on a conformant server;
    #: nonzero here is the operator-visible signal that FR-99's check did not
    #: actually run for that many concepts, not a silent pass.
    unresolved_fsn_count: int = 0


@dataclass(frozen=True)
class TerminologyOutcome:
    """The pass's findings, plus the provenance record of the run itself.

    ``bindings`` and ``results`` exist for ``designation_check.py`` (FR-97,
    issue #28): the checkable code bindings and each edition's ``SweepResult``
    this pass already produced, so the reconciliation pass can reuse them - a
    code's cell type and Verhoeff validity, and its designation set, must not
    be decided twice by two independently drifting notions of "checkable".
    Reconciling only codes present in ``results`` is also what keeps a code
    already reported ``CODE_NOT_FOUND``/``CODE_INACTIVE`` here from being
    reconciled at all: an absent or inactive code was never resolved in any
    edition's ``SweepResult.designations``, so it never reaches the
    designation check's index.
    """

    findings: tuple[Finding, ...]
    run: TerminologyRun
    bindings: tuple[CodeBinding, ...] = ()
    results: dict[str, SweepResult] = field(default_factory=dict)


def collect_code_bindings(sheets: Sequence[Sheet]) -> tuple[CodeBinding, ...]:
    """Every non-empty code cell in the workbook, in sheet/row order.

    The cell's text is stripped before use, so an Appendix A.1/A.3 defect on
    a code cell (a trailing U+00A0 is in the sample data) is reported once, by
    the cell scanner that owns it, instead of a second time here as a code
    that does not resolve. ``str.strip`` removes the non-breaking space along
    with ordinary whitespace, which is exactly the auto-correctable repair
    FR-71 specifies for that defect.
    """
    bindings = [
        CodeBinding(code=code, location=cell.reference, cell_type=cell.cell_type)
        for sheet in sheets
        for cell in sheet.cells
        if cell.role is ColumnRole.CODE and (code := cell.text.strip())
    ]
    return tuple(bindings)


def check_terminology(
    sheets: Sequence[Sheet],
    *,
    sweep: TerminologySweep,
    editions: Sequence[Edition] = DEFAULT_EDITIONS,
) -> TerminologyOutcome:
    """Validates every code binding in ``sheets`` against every edition.

    One sweep per edition; each sweep is ``ceil(N / chunk_size)`` expansion
    requests plus one hierarchy request plus one lookup per unresolved code
    (FR-52). Never one request per code per edition, which is the whole point
    of the requirement.

    Raises whatever the terminology client raises. A failed sweep must not be
    reported as a clean workbook, and this module has no way to mark a report
    partial - FR-54's incomplete-run machinery belongs to the P3 backend
    sweep, so here the run simply fails.
    """
    if not editions:
        # Not a degenerate no-op: with no edition to be absent from, "missing
        # from every edition asked" is vacuously true and every code in the
        # workbook would be reported as not found.
        raise ValueError("check_terminology requires at least one edition")
    edition_labels = [edition.label for edition in editions]
    if len(set(edition_labels)) != len(edition_labels):
        # Two editions sharing a label (e.g. a pinned and an unpinned AU pair
        # for FR-49's reproduce-a-historical-run case) would silently
        # collapse in the `results` dict below: both sweeps run, but one
        # result is discarded, understating what was actually validated.
        raise ValueError(
            f"check_terminology requires distinct edition labels, got {edition_labels}"
        )

    bindings = collect_code_bindings(sheets)
    findings: list[Finding] = []

    # cell_defects.scan_workbook already owns well-formedness for every code
    # cell (CODE_NOT_WELL_FORMED for a malformed text cell, CODE_CELL_NOT_TEXT
    # or CODE_CELL_INVALID_TYPE for a non-text one) unconditionally, not only
    # under --check-terminology. Reporting it again here would duplicate the
    # same finding for the same cell; this loop only needs to decide which
    # bindings are safe to submit for terminology validation.
    checkable: list[CodeBinding] = [
        binding
        for binding in bindings
        if binding.cell_type is CellType.TEXT and has_valid_check_digit(binding.code)
    ]

    codes = tuple(sorted({binding.code for binding in checkable}))
    results = {edition.label: sweep.run(codes, edition=edition) for edition in editions}
    findings.extend(_findings_for(checkable, results))

    return TerminologyOutcome(
        findings=tuple(findings),
        run=TerminologyRun(
            codes_checked=len(codes),
            codes_not_checked=len(bindings) - len(checkable),
            editions=tuple(
                EditionResolution(label=label, resolved_versions=result.resolved_versions)
                for label, result in sorted(results.items())
            ),
            unresolved_fsn_count=sum(result.unresolved_fsn_count for result in results.values()),
        ),
        bindings=tuple(checkable),
        results=results,
    )


def _labels(labels: Sequence[str]) -> str:
    return ", ".join(labels)


def _findings_for(
    bindings: Sequence[CodeBinding], results: dict[str, SweepResult]
) -> tuple[Finding, ...]:
    """Maps each code's cross-edition outcome onto the cells that bind it."""
    absent = {label: set(result.absent) for label, result in results.items()}
    inactive = {label: set(result.inactive) for label, result in results.items()}
    violations = {label: set(result.hierarchy_violations) for label, result in results.items()}
    tags = {
        label: {tag.code: tag for tag in result.unexpected_semantic_tags}
        for label, result in results.items()
    }
    labels = sorted(results)

    findings: list[Finding] = []
    for binding in bindings:
        code = binding.code
        missing_from = [label for label in labels if code in absent[label]]
        if len(missing_from) == len(labels):
            # FR-71's data-defect band: "codes not resolving in either
            # edition". Resolving in one is not a defect - it is what an
            # Australian extension code looks like (FR-47).
            findings.append(
                Finding(
                    code=FindingCode.CODE_NOT_FOUND,
                    location=binding.location,
                    message=(
                        f"code '{code}' does not resolve in any validated edition "
                        f"({_labels(labels)})"
                    ),
                )
            )
            continue

        resolved_in = [label for label in labels if code not in absent[label]]
        inactive_in = [label for label in resolved_in if code in inactive[label]]
        if inactive_in and len(inactive_in) == len(resolved_in):
            # Inactive in every edition that has it at all. Inactive in one
            # and active in another is FR-47's *forecast* - the International
            # edition inactivating a concept the AU edition still carries -
            # and FR-47 is explicit that a forecast is not a current error.
            # Raising it here would abort a seeding run over a change that
            # has not happened yet; the finding it deserves is the P3
            # validation sweep's, not this pass's.
            findings.append(
                Finding(
                    code=FindingCode.CODE_INACTIVE,
                    location=binding.location,
                    message=(
                        f"code '{code}' is inactive in {_labels(inactive_in)}; "
                        "an inactive concept must not be published as a binding"
                    ),
                )
            )

        violated_in = [label for label in labels if code in violations[label]]
        if violated_in:
            findings.append(
                Finding(
                    code=FindingCode.OUT_OF_SCOPE_HIERARCHY,
                    location=binding.location,
                    message=(
                        f"code '{code}' is not subsumed by <<71388002 |Procedure "
                        f"(procedure)| in {_labels(violated_in)} (FR-84)"
                    ),
                )
            )
            continue

        # FR-99, reported once per cell even where both editions serve the
        # same tag: the first edition that has one settles the message, so a
        # dual-edition run does not produce two identical warnings.
        for label in labels:
            tag = tags[label].get(code)
            if tag is None:
                continue
            findings.append(
                Finding(
                    code=FindingCode.UNEXPECTED_SEMANTIC_TAG,
                    location=binding.location,
                    message=(
                        f"code '{code}' is a procedure by subsumption but its {label} "
                        f"semantic tag is '({escape_invisible(tag.tag)})', not '(procedure)': "
                        f"'{escape_invisible(tag.fully_specified_name)}' (FR-99, warning only)"
                    ),
                )
            )
            break
    return tuple(findings)
