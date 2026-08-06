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
from dataclasses import dataclass

from nptc_shared.sctid import has_valid_check_digit
from nptc_shared.terminology.models import SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL, Edition
from nptc_shared.terminology.sweep import SweepResult, TerminologySweep
from nptc_shared.text import escape_invisible
from nptc_transform.bands import FindingCode
from nptc_transform.findings import Finding
from nptc_transform.workbook import ColumnRole, Sheet

#: FR-74/FR-47: every code is validated against both editions, latest release
#: of each (FR-49 - no version pinned, the server reports what it resolved).
DEFAULT_EDITIONS: tuple[Edition, ...] = (SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL)


@dataclass(frozen=True)
class CodeBinding:
    """One code cell: the code it holds, and the cell it came from."""

    code: str
    location: str


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
    """

    codes_checked: int
    codes_not_checked: int
    editions: tuple[EditionResolution, ...]


@dataclass(frozen=True)
class TerminologyOutcome:
    """The pass's findings, plus the provenance record of the run itself."""

    findings: tuple[Finding, ...]
    run: TerminologyRun


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
        CodeBinding(code=code, location=cell.reference)
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

    bindings = collect_code_bindings(sheets)
    findings: list[Finding] = []

    checkable: list[CodeBinding] = []
    for binding in bindings:
        if has_valid_check_digit(binding.code):
            checkable.append(binding)
            continue
        findings.append(
            Finding(
                code=FindingCode.CODE_NOT_WELL_FORMED,
                location=binding.location,
                message=(
                    f"code '{escape_invisible(binding.code)}' is not a well-formed SCTID "
                    "(6-18 digits with a valid Verhoeff check digit, FR-06); it was not "
                    "submitted for terminology validation"
                ),
            )
        )

    codes = tuple(sorted({binding.code for binding in checkable}))
    results = {edition.label: sweep.run(codes, edition=edition) for edition in editions}
    findings.extend(_findings_for(checkable, results))

    return TerminologyOutcome(
        findings=tuple(findings),
        run=TerminologyRun(
            codes_checked=len(codes),
            codes_not_checked=len({binding.code for binding in bindings}) - len(codes),
            editions=tuple(
                EditionResolution(label=label, resolved_versions=result.resolved_versions)
                for label, result in sorted(results.items())
            ),
        ),
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
                        f"semantic tag is '({tag.tag})', not '(procedure)': "
                        f"'{tag.fully_specified_name}' (FR-99, warning only)"
                    ),
                )
            )
            break
    return tuple(findings)
