"""FR-97: reconciles each workbook row's published label against the
designation set of the concept its code is bound to (issue #28, P0-6).

This is a **seeding-only** concern (PRD:847): once designations are stored as
served (FR-82), every stored value came from the server by construction, so
"matches nothing" cannot arise again. It arises here because the workbook's
``SNOMED CT Fully Specified Name`` column - despite its header - holds neither
FSNs nor preferred terms consistently (PRD Appendix A.10): it is free text a
human typed over more than a decade, validatable only against the bound
concept's whole designation *set*.

Two independent axes, both handled here:

1. **The four-outcome classification** (FR-97's own table). A label matching
   the tag-stripped FSN is seeded silently (no finding at all); one matching
   another active designation is informational (``LABEL_DESIGNATION_DRIFT``);
   one matching a designation of a *different* bound concept
   (``LABEL_BOUND_TO_OTHER_CONCEPT``) or of no concept at all
   (``LABEL_MATCHES_NO_DESIGNATION``) is a blocking data defect - FR-71's own
   words for "the most dangerous outcome", a plausible label paired with the
   wrong code.
2. **The AU-preferred-term-differs list** (``LABEL_DIFFERS_FROM_PREFERRED_TERM``),
   always informational, reported only for rows the first axis found benign -
   PRD Appendix A.10/A.11's arithmetic is 8 drift-only rows plus 1 defect row,
   not 9 rows in both lists (row 22 is the defect and is excluded from the
   drift list; row 45 is on the drift list despite being axis-1-clean,
   because its label equals the tag-stripped FSN while the AU preferred term
   has since diverged from *both*).

**Request shape.** Classification is local first, against
``SweepResult.designations`` - the designation set the FR-52 batch sweep's
bulk ``$expand`` already fetched, at zero extra request cost. Only a label
matching nothing locally is escalated to one ``CodeSystem/$validate-code``
per unique ``(code, label)`` pair (``TerminologySweep.confirm_labels``) - the
delta, never every row (FR-52's discipline, and ``client.py``'s own
``validate_code`` docstring: "one call per row is legitimate only in the
seeding transform's designation-reconciliation pass, where the delta is the
workload").

**The probe is monotone: it can only make an outcome more benign, never less.**
No local match -> probe. Probe says the display matches -> downgrade to
informational (outcome 2). Probe says it doesn't -> the local verdict stands
and outcome 3/4 discrimination proceeds exactly as it would have without a
probe at all. This is what makes the design safe against a server whose
``$validate-code`` display matching is itself imperfect: it can never turn a
benign label into a false abort, only fail to rescue a genuine defect from
one.

**Outcome 3 is workbook-scoped.** There is no reverse designation search in
the FR-53 client contract, and a server-side term filter would be per-row and
non-deterministic (FR-73) rather than an equality check. Instead this module
indexes every designation value the sweep resolved, across every code bound
anywhere in the workbook, and reports outcome 3 when an unmatched label hits
a *different* bound code's designation. A label belonging to a concept this
workbook does not bind anywhere is therefore reported as outcome 4, not
outcome 3 - both block, so this narrows *why*, never *whether*.

**Dual-edition handling.** A label matching a designation in *any* swept
edition is benign (mirrors FR-71's own "not resolving in either edition" -
resolving in one is the expected shape of an Australian extension code, FR-47).
The AU edition is authoritative for the FSN quoted in a message and for the
probe, since the workbook's own code-binding column is
``Terminology binding (SNOMED CT-AU)`` - falling back to the first edition in
sorted label order that resolved the code. The preferred-term check is
scoped to the AU edition specifically (FR-82), and is skipped entirely for a
code that did not resolve in AU at all.

**What this module does not re-decide.** A code absent or inactive in every
edition never appears in any edition's ``SweepResult.designations`` (it is
only populated from the bulk ``$expand``'s *active* results), so it is
silently excluded here - it is already ``CODE_NOT_FOUND``/``CODE_INACTIVE``,
and reconciling it too would both double-report and waste a probe on a code
the server has already disowned. Likewise a non-text or Verhoeff-invalid code
cell is excluded via ``bindings`` (the same checkable set
``terminology_check.check_terminology`` built) rather than a second,
independently drifting notion of "checkable". A hierarchy violation
(FR-84) is deliberately **not** excluded, unlike FR-99's tag check: a label
defect on that cell survives the rebinding that would fix the hierarchy
violation, so the two findings describe different remediations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto

from nptc_shared.terminology.models import SNOMED_CT_AU, Edition
from nptc_shared.terminology.snomed import strip_semantic_tag
from nptc_shared.terminology.sweep import (
    ConceptDesignations,
    LabelConfirmation,
    SweepResult,
    TerminologySweep,
)
from nptc_shared.text import escape_invisible, find_invisible_characters, normalise_for_comparison
from nptc_transform.bands import FindingCode
from nptc_transform.findings import Finding
from nptc_transform.terminology_check import DEFAULT_EDITIONS, CodeBinding
from nptc_transform.workbook import Cell, ColumnRole, Sheet

#: A server rejection message can enumerate a concept's whole designation set
#: (see ``validate-code-false-display-mismatch.json``) - truncated so one
#: unresolved row does not dominate a report meant to cover thousands.
_MAX_SERVER_MESSAGE_LENGTH = 240


@dataclass(frozen=True)
class DesignationRun:
    """What the reconciliation pass covered, for the report's provenance block.

    Mirrors ``TerminologyRun``'s own reasoning: a pass that silently skipped
    most of the workbook must not read as one that reconciled all of it.
    """

    #: Rows with a checkable code and a usable label that reached
    #: classification - benign or defect, the pass ran either way.
    labels_reconciled: int = 0
    #: Rows with a checkable code that could not be classified at all: no
    #: label, a label reduced to nothing by whitespace, a label still
    #: carrying an invisible character after edge-stripping (already owned
    #: by ``cell_defects.py``), or a code absent/inactive in every edition
    #: (already owned by ``CODE_NOT_FOUND``/``CODE_INACTIVE``).
    labels_not_reconciled: int = 0
    #: ``CodeSystem/$validate-code`` requests actually issued - the only
    #: per-row request in the whole tool, and the number that makes "the
    #: delta is the workload" (``client.py``) auditable rather than asserted.
    label_confirmations: int = 0


@dataclass(frozen=True)
class DesignationOutcome:
    """The reconciliation pass's findings, plus its provenance record."""

    findings: tuple[Finding, ...]
    run: DesignationRun


class _LocalVerdict(Enum):
    """What a label's own designation set says, before any server probe."""

    STRIPPED_FSN = auto()
    OTHER_DESIGNATION = auto()
    NO_LOCAL_MATCH = auto()


@dataclass(frozen=True)
class _Candidate:
    """One row's code cell paired with its FSN-column label cell."""

    code: str
    cell: Cell
    label: str
    normalised_label: str
    entries: Mapping[str, ConceptDesignations]


def _rows_by_role(sheet: Sheet) -> dict[int, dict[ColumnRole, Cell]]:
    """Groups ``sheet.cells`` by row, keeping only the code and FSN cells.

    No row abstraction exists on ``Sheet``/``Cell`` themselves, and adding one
    would ripple into ``cell_defects.py`` for no benefit there - this is a
    private, minimal view built fresh for this one pass.
    """
    rows: dict[int, dict[ColumnRole, Cell]] = defaultdict(dict)
    for cell in sheet.cells:
        if cell.role in (ColumnRole.CODE, ColumnRole.FSN):
            rows[cell.row][cell.role] = cell
    return rows


def _entries_for_code(
    code: str, designations_by_label: Mapping[str, Mapping[str, ConceptDesignations]]
) -> dict[str, ConceptDesignations]:
    return {
        label: entries[code] for label, entries in designations_by_label.items() if code in entries
    }


def _index_designation_values(
    designations_by_label: Mapping[str, Mapping[str, ConceptDesignations]],
) -> dict[str, set[str]]:
    """Every designation value resolved anywhere in this run, to the set of
    codes it belongs to - the workbook-scoped material for outcome 3.

    Includes each concept's tag-stripped FSN alongside its raw designation
    values, for the same reason ``_local_verdict`` checks both for a code's
    *own* entries: the workbook column never carries a tag (PRD Appendix
    A.8), so the realistic transcription error is a label matching the
    tag-stripped FSN of the *wrong* concept, not its tagged form.
    """
    index: dict[str, set[str]] = defaultdict(set)
    for entries in designations_by_label.values():
        for entry in entries.values():
            for value in entry.values:
                index[normalise_for_comparison(value)].add(entry.code)
            if entry.fully_specified_name is not None:
                index[normalise_for_comparison(strip_semantic_tag(entry.fully_specified_name))].add(
                    entry.code
                )
    return index


def _local_verdict(
    normalised_label: str, entries: Mapping[str, ConceptDesignations]
) -> _LocalVerdict:
    """Classifies ``normalised_label`` against ``entries`` alone, no request.

    A union across every edition the code resolved in - the same "not
    resolving in *either* edition" logic FR-71 applies to code status
    (``terminology_check._findings_for``), so a label valid in one edition is
    never a defect merely because a different edition's designation set
    doesn't happen to carry it too.

    A label matching the FSN **with its tag intact** is treated the same as
    matching the tag-stripped form - both are "this is genuinely the
    concept's own FSN", never ``OTHER_DESIGNATION``. The workbook column
    never carries a tag in practice (PRD Appendix A.8), but a label that
    happens to is unambiguously the concept's own designation, not a
    different, merely-active one - and ``_drift_finding``'s message asserts
    "is not the FSN of this code", which would be false for exactly this
    case if it fell through to that branch.
    """
    tag_stripped_fsns: set[str] = set()
    full_fsns: set[str] = set()
    all_values: set[str] = set()
    for entry in entries.values():
        if entry.fully_specified_name is not None:
            tag_stripped_fsns.add(
                normalise_for_comparison(strip_semantic_tag(entry.fully_specified_name))
            )
            full_fsns.add(normalise_for_comparison(entry.fully_specified_name))
        all_values.update(normalise_for_comparison(value) for value in entry.values)
    if normalised_label in tag_stripped_fsns or normalised_label in full_fsns:
        return _LocalVerdict.STRIPPED_FSN
    if normalised_label in all_values:
        return _LocalVerdict.OTHER_DESIGNATION
    return _LocalVerdict.NO_LOCAL_MATCH


def _preferred_entry(entries: Mapping[str, ConceptDesignations]) -> tuple[str, ConceptDesignations]:
    """The (edition label, entry) to quote in a message and to probe.

    AU is authoritative when it resolved the code - the workbook's own
    code-binding column is ``Terminology binding (SNOMED CT-AU)`` - otherwise
    the first edition in sorted label order that did.
    """
    if SNOMED_CT_AU.label in entries:
        return SNOMED_CT_AU.label, entries[SNOMED_CT_AU.label]
    label = min(entries)
    return label, entries[label]


def _join(codes: Iterable[str]) -> str:
    return ", ".join(codes)


def _truncate(message: str) -> str:
    if len(message) <= _MAX_SERVER_MESSAGE_LENGTH:
        return message
    return message[:_MAX_SERVER_MESSAGE_LENGTH] + "…"


def _fsn_clause(entry: ConceptDesignations) -> str:
    if entry.fully_specified_name is None:
        return ""
    return f" (FSN '{escape_invisible(entry.fully_specified_name)}')"


def _drift_finding(candidate: _Candidate, *, server_confirmed: bool) -> Finding:
    _, entry = _preferred_entry(candidate.entries)
    basis = "the terminology server confirmed it" if server_confirmed else "it"
    served_fsn = (
        f"the served FSN '{escape_invisible(entry.fully_specified_name)}'"
        if entry.fully_specified_name is not None
        else "the served FSN"
    )
    return Finding(
        code=FindingCode.LABEL_DESIGNATION_DRIFT,
        location=candidate.cell.reference,
        message=(
            f"published label '{escape_invisible(candidate.label)}' is not the FSN of "
            f"'{candidate.code}' but {basis} matches an active designation on it; "
            f"{served_fsn} will be seeded (FR-97, informational)"
        ),
    )


def _other_concept_finding(candidate: _Candidate, other_codes: Sequence[str]) -> Finding:
    _, entry = _preferred_entry(candidate.entries)
    return Finding(
        code=FindingCode.LABEL_BOUND_TO_OTHER_CONCEPT,
        location=candidate.cell.reference,
        message=(
            f"published label '{escape_invisible(candidate.label)}' matches a designation of "
            f"{_join(other_codes)}, not of the bound code '{candidate.code}'{_fsn_clause(entry)}; "
            "one of the code and the label is a transcription error (FR-97)"
        ),
    )


def _no_match_finding(candidate: _Candidate, confirmation: LabelConfirmation | None) -> Finding:
    _, entry = _preferred_entry(candidate.entries)
    server_clause = ""
    if confirmation is not None and confirmation.message:
        server_clause = (
            f"; the server reported: {escape_invisible(_truncate(confirmation.message))}"
        )
    return Finding(
        code=FindingCode.LABEL_MATCHES_NO_DESIGNATION,
        location=candidate.cell.reference,
        message=(
            f"published label '{escape_invisible(candidate.label)}' matches no designation of "
            f"'{candidate.code}'{_fsn_clause(entry)} in {_join(sorted(candidate.entries))}"
            f"{server_clause} (FR-97)"
        ),
    )


def _preferred_term_finding(candidate: _Candidate) -> Finding | None:
    """FR-97's separate, always-informational list: the current AU preferred
    term differs from the published label.

    Scoped to the AU edition specifically (FR-82 is the AU langrefset) and
    skipped entirely for a code that did not resolve in AU at all - the
    workbook's own code-binding column names AU as the bound edition, so a
    non-AU ``display`` here is not the preferred term FR-82 means.
    """
    entry = candidate.entries.get(SNOMED_CT_AU.label)
    if entry is None or entry.display is None:
        return None
    if normalise_for_comparison(entry.display) == candidate.normalised_label:
        return None
    return Finding(
        code=FindingCode.LABEL_DIFFERS_FROM_PREFERRED_TERM,
        location=candidate.cell.reference,
        message=(
            f"the {SNOMED_CT_AU.label} preferred term for '{candidate.code}' is "
            f"'{escape_invisible(entry.display)}', not the published label "
            f"'{escape_invisible(candidate.label)}' (FR-97, informational)"
        ),
    )


def _axis_one(
    candidate: _Candidate,
    confirmation: LabelConfirmation | None,
    value_index: Mapping[str, set[str]],
) -> tuple[Finding | None, bool]:
    """Classifies one candidate's label against the four outcomes.

    Returns ``(finding, benign)``: ``finding`` is ``None`` for outcome 1 (seed
    silently), and ``benign`` gates whether the preferred-term check runs at
    all - row 22 (a defect) must never also appear on the informational
    preferred-term list (PRD Appendix A.11's 8-not-9 arithmetic).
    """
    verdict = _local_verdict(candidate.normalised_label, candidate.entries)
    if verdict is _LocalVerdict.STRIPPED_FSN:
        return None, True
    if verdict is _LocalVerdict.OTHER_DESIGNATION:
        return _drift_finding(candidate, server_confirmed=False), True
    # NO_LOCAL_MATCH: the probe can only downgrade this, never escalate it.
    if confirmation is not None and confirmation.matched:
        return _drift_finding(candidate, server_confirmed=True), True
    other_codes = sorted(value_index.get(candidate.normalised_label, set()) - {candidate.code})
    if other_codes:
        return _other_concept_finding(candidate, other_codes), False
    return _no_match_finding(candidate, confirmation), False


def check_designations(
    sheets: Sequence[Sheet],
    *,
    sweep: TerminologySweep,
    bindings: Sequence[CodeBinding],
    results: Mapping[str, SweepResult],
    editions: Sequence[Edition] = DEFAULT_EDITIONS,
) -> DesignationOutcome:
    """Reconciles every row's published label against its bound concept.

    ``bindings`` and ``results`` are ``terminology_check.check_terminology``'s
    own output for the same workbook and editions - reused rather than
    recomputed, so "which code cells are checkable" and "what did each
    edition resolve" are decided exactly once (FR-74).
    """
    checkable_locations = {binding.location for binding in bindings}
    designations_by_label: dict[str, dict[str, ConceptDesignations]] = {
        label: {entry.code: entry for entry in result.designations}
        for label, result in results.items()
    }
    value_index = _index_designation_values(designations_by_label)
    editions_by_label = {edition.label: edition for edition in editions}

    candidates: list[_Candidate] = []
    labels_not_reconciled = 0
    for sheet in sheets:
        for cells in _rows_by_role(sheet).values():
            code_cell = cells.get(ColumnRole.CODE)
            if code_cell is None or code_cell.reference not in checkable_locations:
                continue
            fsn_cell = cells.get(ColumnRole.FSN)
            if fsn_cell is None:
                labels_not_reconciled += 1
                continue
            normalised_label = normalise_for_comparison(fsn_cell.text)
            if not normalised_label or find_invisible_characters(normalised_label):
                # Blank/whitespace-only (WHITESPACE_ONLY_CELL, blocking), or a
                # genuinely ambiguous invisible character with no
                # deterministic repair (INVISIBLE_CHARACTER_AMBIGUOUS,
                # blocking) - never a merely auto-correctable one, since
                # normalise_for_comparison already collapses those (a
                # non-breaking space, wherever it occurs) before this check
                # runs. Skipping on a code that survives *that* would exempt
                # a row from FR-97 through a non-blocking path, silently
                # dropping the very transcription-error check H-07 exists for.
                labels_not_reconciled += 1
                continue
            code = code_cell.text.strip()
            entries = _entries_for_code(code, designations_by_label)
            if not entries:
                # Absent or inactive in every edition - CODE_NOT_FOUND/
                # CODE_INACTIVE already own it; reconciling it too would
                # double-report and waste a probe on a disowned code.
                labels_not_reconciled += 1
                continue
            candidates.append(
                _Candidate(
                    code=code,
                    cell=fsn_cell,
                    label=fsn_cell.text,
                    normalised_label=normalised_label,
                    entries=entries,
                )
            )

    probes_by_edition: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for candidate in candidates:
        if (
            _local_verdict(candidate.normalised_label, candidate.entries)
            is _LocalVerdict.NO_LOCAL_MATCH
        ):
            edition_label, _entry = _preferred_entry(candidate.entries)
            probes_by_edition[edition_label].append((candidate.code, candidate.label.strip()))

    confirmations: dict[tuple[str, str], LabelConfirmation] = {}
    label_confirmations = 0
    for edition_label, probes in probes_by_edition.items():
        edition = editions_by_label.get(edition_label)
        if edition is None:
            continue
        issued = sweep.confirm_labels(probes, edition=edition)
        label_confirmations += len(issued)
        for issued_confirmation in issued:
            confirmations[(issued_confirmation.code, issued_confirmation.display)] = (
                issued_confirmation
            )

    findings: list[Finding] = []
    for candidate in candidates:
        confirmation = confirmations.get((candidate.code, candidate.label.strip()))
        finding, benign = _axis_one(candidate, confirmation, value_index)
        if finding is not None:
            findings.append(finding)
        if benign:
            pt_finding = _preferred_term_finding(candidate)
            if pt_finding is not None:
                findings.append(pt_finding)

    return DesignationOutcome(
        findings=tuple(findings),
        run=DesignationRun(
            labels_reconciled=len(candidates),
            labels_not_reconciled=labels_not_reconciled,
            label_confirmations=label_confirmations,
        ),
    )
