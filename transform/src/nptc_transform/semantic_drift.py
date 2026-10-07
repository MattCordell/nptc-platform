"""FR-75/H-03: reports a semantic mismatch between the RCPA preferred term's
specimen/timing wording and the bound SNOMED concept's modelled ``Has specimen``
value.

Seeding-only and candidate-generating, like ``designation_check.py`` (FR-97) and
``misspelling.py`` (FR-79). Every finding is ``Band.INFORMATIONAL``, "a candidate
for editorial review, not a confirmed defect" (``bands.py``), because the check
is a heuristic over free text. PRD Annex A.9's worked examples show roughly as
many benign rows as genuine ones, so a blocking band would be indefensible.

**Unlike FR-97, a hierarchy violation (FR-84) is deliberately not excluded.** A
drift finding is about the term's own content and survives whatever rebinding
would fix the hierarchy violation. The two findings need different remedies for
the same cell, so both may fire.

**Request shape (ADR-0008).** Every code is resolved against ``SNOMED_CT_AU``
only, not the dual-edition ``terminology_check.DEFAULT_EDITIONS``: the
workbook's code-binding column is ``Terminology binding (SNOMED CT-AU)``, and
every specimen concept compared against was verified live in AU
(``specimen_table.py``). One ``describe()`` call resolves the specimen table's
designation sets. One ``codes_without_attribute`` call, plus one
``codes_with_attribute_value`` call per distinct group still asserted by an
unresolved row, classify the delta the visibility filter left. The total is
``2 + G`` (ADR-0008 Decision 6).

**The specimen table is an allowlist, never a finding generator.** A term
asserting a specimen no ``specimen_table.SPECIMEN_TABLE`` group covers is never
inspected for that aspect: this module's principal failure mode. The workbook's
``Specimen`` column is never an assertion source. It is coded through the reviewed
specimen map, and a string the map does not cover blocks the run (ADR-0044).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from nptc_shared.terminology.models import HAS_SPECIMEN_ATTRIBUTE, SNOMED_CT_AU
from nptc_shared.terminology.sweep import ConceptDesignations, SweepResult, TerminologySweep
from nptc_shared.text import escape_invisible, normalise_for_comparison
from nptc_transform.bands import FindingCode
from nptc_transform.cellref import CellRef
from nptc_transform.findings import Finding
from nptc_transform.rows import group_rows
from nptc_transform.specimen_table import SPECIMEN_TABLE, SpecimenGroup, all_specimen_codes
from nptc_transform.terminology_check import CodeBinding
from nptc_transform.workbook import Cell, ColumnRole, Sheet

#: A timing assertion in a free-text label: 1-3 digits, optional dash or space,
#: then an hour or day unit word at a word boundary. ``(?<![\w.])`` keeps it from
#: matching inside "B12", "Vitamin D3" or "1,25 dihydroxy...", where the digits
#: follow a word character or decimal point. The trailing ``\b`` keeps
#: "dihydroxy" from matching the bare "d" unit. A test covers all three.
_TIMING_RE = re.compile(
    r"(?<![\w.])(\d{1,3})\s*-?\s*(h|hr|hrs|hour|hours|d|day|days)\b", re.IGNORECASE
)

_HOUR_UNITS = frozenset({"h", "hr", "hrs", "hour", "hours"})
_DAY_UNITS = frozenset({"d", "day", "days"})


@dataclass(frozen=True)
class DriftRun:
    """What the semantic-drift pass covered, for the report's provenance block."""

    #: Rows with a checkable code, a usable preferred-term label, and a code
    #: resolved in at least one edition: the pass ran, whether or not it found
    #: anything.
    rows_examined: int = 0
    #: Rows excluded before classification: no checkable code binding, no
    #: preferred-term cell, a blank label, or a code absent or inactive in every
    #: edition (owned by CODE_NOT_FOUND/CODE_INACTIVE).
    rows_excluded: int = 0
    term_specimen_not_modelled_count: int = 0
    term_specimen_differs_count: int = 0
    term_timing_not_modelled_count: int = 0
    #: Specimen-table SCTIDs ``describe()`` resolved nothing for: the check did
    #: not run for that many, which is not a silent pass (see
    #: ``TerminologyRun.unresolved_fsn_count``). The group still works on its
    #: hand-typed terms, without server augmentation.
    specimen_table_entries_unresolved: int = 0
    #: ``$expand``-style requests resolving the specimen table's designations
    #: (``TerminologySweep.describe``): at most ``ceil(len(specimen table) /
    #: chunk_size)``, never catalogue-scale.
    describe_requests: int = 0
    #: ``$expand``-style requests classifying the delta the visibility filter
    #: left (``codes_without_attribute`` plus one ``codes_with_attribute_value``
    #: per distinct group still asserted by an unresolved row).
    classification_requests: int = 0
    #: Every fully qualified version URI resolved across this pass's calls
    #: (FR-48), the provenance ``SweepResult.resolved_versions`` carries for the
    #: terminology pass.
    resolved_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticDriftOutcome:
    """The semantic-drift pass's findings, plus its provenance record."""

    findings: tuple[Finding, ...]
    run: DriftRun


@dataclass(frozen=True)
class _Candidate:
    code: str
    cell: Cell
    label: str
    folded_label: str
    entries: Mapping[str, ConceptDesignations]
    asserted_group: SpecimenGroup | None
    timing: str | None


def _fold(text: str) -> str:
    """``normalise_for_comparison(text).casefold()``. This pass scans a label for
    a specimen or timing *word* rather than comparing for equality, so case is
    noise. FR-97's comparison against a served designation never casefolds,
    because a case difference there is an editorial signal.
    """
    return normalise_for_comparison(text).casefold()


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(term) + r"\b")


def _rows_by_role(sheet: Sheet) -> dict[int, dict[ColumnRole, Cell]]:
    """Groups ``sheet.cells`` by row, keeping the code, preferred-term and
    specimen-column cells (see ``designation_check._rows_by_role``)."""
    rows: dict[int, dict[ColumnRole, Cell]] = defaultdict(dict)
    for source_row in group_rows((sheet,)):
        for role in (ColumnRole.CODE, ColumnRole.PREFERRED_TERM, ColumnRole.SPECIMEN):
            cell = source_row.cells.get(role)
            if cell is not None:
                rows[source_row.row][role] = cell
    return rows


def _entries_for_code(
    code: str, designations_by_label: Mapping[str, Mapping[str, ConceptDesignations]]
) -> dict[str, ConceptDesignations]:
    return {
        label: entries[code] for label, entries in designations_by_label.items() if code in entries
    }


def _longest_match(folded_text: str, table: Sequence[SpecimenGroup]) -> SpecimenGroup | None:
    """The group whose hand-typed term is the longest one matching ``folded_text``
    at a word boundary. Ties go to ``table``'s declaration order, never a derived
    set's iteration order (FR-73).
    """
    best: tuple[int, SpecimenGroup] | None = None
    for group in table:
        for term in group.terms:
            if len(term) <= (best[0] if best else -1):
                continue
            if _word_boundary_pattern(term).search(folded_text) is not None:
                best = (len(term), group)
    return best[1] if best is not None else None


def _any_term_matches(folded_text: str, terms: Iterable[str]) -> bool:
    return any(_word_boundary_pattern(term).search(folded_text) is not None for term in terms)


def _extract_timing(folded_label: str) -> str | None:
    """The label's timing assertion, canonicalised so ``24h``/``24 hr``/``24 hour``
    all give the same string (see ``_TIMING_RE`` for what never matches)."""
    match = _TIMING_RE.search(folded_label)
    if match is None:
        return None
    number = str(int(match.group(1)))
    unit = match.group(2).lower()
    canonical_unit = "h" if unit in _HOUR_UNITS else "d"
    return f"{number} {canonical_unit}"


def _timing_visible_in(texts: Iterable[str], timing: str) -> bool:
    """True if any of ``texts`` carries the same canonicalised timing as ``timing``.

    Runs each text through ``_extract_timing`` instead of word-boundary matching
    the canonical string (``"24 h"``). A served ``"24 hour urine specimen"`` or
    ``"24h urine"`` has no word boundary after the "h", so a literal match would
    miss wording that does carry the timing: the false positive this check
    exists to prevent.
    """
    return any(_extract_timing(text) == timing for text in texts)


def _designation_texts(entries: Mapping[str, ConceptDesignations]) -> list[str]:
    """Every folded FSN, display and designation value in ``entries``, across every
    edition: the search space for the specimen visibility filter and the timing
    check."""
    texts: list[str] = []
    for entry in entries.values():
        if entry.fully_specified_name is not None:
            texts.append(_fold(entry.fully_specified_name))
        if entry.display is not None:
            texts.append(_fold(entry.display))
        texts.extend(_fold(value) for value in entry.values)
    return texts


def _specimen_not_modelled_finding(candidate: _Candidate, group: SpecimenGroup) -> Finding:
    timing_clause = ""
    if candidate.timing is not None:
        timing_clause = f" and a timing assertion of '{candidate.timing}'"
    return Finding(
        code=FindingCode.TERM_SPECIMEN_NOT_MODELLED,
        location=candidate.cell.reference,
        message=(
            f"published label '{escape_invisible(candidate.label)}' asserts specimen "
            f"'{group.key}' ('{group.specimen_display}', {group.specimen_code}){timing_clause}, "
            f"but '{candidate.code}' constrains no {HAS_SPECIMEN_ATTRIBUTE} |Has specimen| value "
            "at all; a candidate for editorial review, not a confirmed defect (FR-75)"
        ),
    )


def _specimen_differs_finding(candidate: _Candidate, group: SpecimenGroup) -> Finding:
    return Finding(
        code=FindingCode.TERM_SPECIMEN_DIFFERS,
        location=candidate.cell.reference,
        message=(
            f"published label '{escape_invisible(candidate.label)}' asserts specimen "
            f"'{group.key}' ('{group.specimen_display}', {group.specimen_code}), but "
            f"'{candidate.code}' constrains a {HAS_SPECIMEN_ATTRIBUTE} |Has specimen| value "
            f"that is not subsumed by it; a candidate for editorial review, not a confirmed "
            "defect (FR-75)"
        ),
    )


def _timing_not_modelled_finding(candidate: _Candidate) -> Finding:
    return Finding(
        code=FindingCode.TERM_TIMING_NOT_MODELLED,
        location=candidate.cell.reference,
        message=(
            f"published label '{escape_invisible(candidate.label)}' asserts a timing of "
            f"'{candidate.timing}', but neither '{candidate.code}' nor its asserted specimen "
            "concept's own served designations carry that timing; a candidate for editorial "
            "review, not a confirmed defect (FR-75)"
        ),
    )


def check_semantic_drift(
    sheets: Sequence[Sheet],
    *,
    sweep: TerminologySweep,
    bindings: Sequence[CodeBinding],
    results: Mapping[str, SweepResult],
) -> SemanticDriftOutcome:
    """Reconciles every row's RCPA preferred term against its bound concept's
    modelled ``Has specimen`` value and any timing wording (FR-75).

    ``bindings`` and ``results`` are ``terminology_check.check_terminology``'s
    output for the same workbook, reused rather than recomputed (FR-74), as in
    ``designation_check.check_designations``.

    Request count is ``2 + G`` (ADR-0008 Decision 6): ``describe`` for the
    specimen table, one ``codes_without_attribute`` over every unresolved
    asserting code to separate ``TERM_SPECIMEN_NOT_MODELLED`` from
    ``TERM_SPECIMEN_DIFFERS``, and one ``codes_with_attribute_value`` per
    distinct group still asserted. The request-count test asserts it.
    """
    checkable_locations: set[CellRef] = {binding.location for binding in bindings}
    designations_by_label: dict[str, dict[str, ConceptDesignations]] = {
        label: {entry.code: entry for entry in result.designations}
        for label, result in results.items()
    }

    candidates: list[_Candidate] = []
    rows_excluded = 0
    for sheet in sheets:
        for cells in _rows_by_role(sheet).values():
            code_cell = cells.get(ColumnRole.CODE)
            if code_cell is None or code_cell.reference not in checkable_locations:
                rows_excluded += 1
                continue
            label_cell = cells.get(ColumnRole.PREFERRED_TERM)
            if label_cell is None:
                rows_excluded += 1
                continue
            folded_label = _fold(label_cell.text)
            if not folded_label:
                rows_excluded += 1
                continue
            code = code_cell.text.strip()
            entries = _entries_for_code(code, designations_by_label)
            if not entries:
                # Absent or inactive in every edition: CODE_NOT_FOUND/
                # CODE_INACTIVE own it (see terminology_check.py).
                rows_excluded += 1
                continue
            asserted_group = _longest_match(folded_label, SPECIMEN_TABLE)
            timing = _extract_timing(folded_label)
            candidates.append(
                _Candidate(
                    code=code,
                    cell=label_cell,
                    label=label_cell.text,
                    folded_label=folded_label,
                    entries=entries,
                    asserted_group=asserted_group,
                    timing=timing,
                )
            )

    resolved_versions: set[str] = set()

    # -- vocabulary: one describe() call over the whole specimen table -----
    table_codes = all_specimen_codes(SPECIMEN_TABLE)
    described = sweep.describe(table_codes, edition=SNOMED_CT_AU, versions=resolved_versions)
    described_by_code = {entry.code: entry for entry in described}
    specimen_table_entries_unresolved = len(table_codes) - len(described_by_code)
    describe_requests = math.ceil(len(table_codes) / sweep.chunk_size) if table_codes else 0

    group_effective_terms: dict[str, frozenset[str]] = {}
    for group in SPECIMEN_TABLE:
        served = described_by_code.get(group.specimen_code)
        served_terms: set[str] = set()
        if served is not None:
            if served.fully_specified_name is not None:
                served_terms.add(_fold(served.fully_specified_name))
            if served.display is not None:
                served_terms.add(_fold(served.display))
            served_terms.update(_fold(value) for value in served.values)
        group_effective_terms[group.key] = frozenset(group.terms) | served_terms

    # -- visibility filter: which asserted rows still need classification --
    asserting_codes: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        asserted = candidate.asserted_group
        if asserted is None:
            continue
        texts = _designation_texts(candidate.entries)
        is_visible = any(
            _any_term_matches(text, group_effective_terms[asserted.key]) for text in texts
        )
        if not is_visible:
            asserting_codes[asserted.key].add(candidate.code)

    groups_by_key = {group.key: group for group in SPECIMEN_TABLE}

    # Keyed by (code, group.key), not code alone: rows asserting different
    # specimen groups can bind the same SCTID, and agreement is per (code, group).
    # A code agreeing with group A but not B must not carry B's verdict into A's row.
    not_modelled: set[tuple[str, str]] = set()
    differs: set[tuple[str, str]] = set()
    classification_requests = 0
    all_unresolved_codes = sorted({code for codes in asserting_codes.values() for code in codes})
    if all_unresolved_codes:
        without_result = frozenset(
            sweep.codes_without_attribute(
                all_unresolved_codes,
                attribute=HAS_SPECIMEN_ATTRIBUTE,
                edition=SNOMED_CT_AU,
                versions=resolved_versions,
            )
        )
        classification_requests += 1
        for key, codes in asserting_codes.items():
            not_modelled.update((code, key) for code in codes if code in without_result)
        for key, codes in sorted(asserting_codes.items()):
            group = groups_by_key[key]
            agrees = frozenset(
                sweep.codes_with_attribute_value(
                    sorted(codes),
                    attribute=HAS_SPECIMEN_ATTRIBUTE,
                    root=group.specimen_code,
                    edition=SNOMED_CT_AU,
                    versions=resolved_versions,
                )
            )
            classification_requests += 1
            for code in codes:
                if (code, key) in not_modelled:
                    continue
                if code not in agrees:
                    differs.add((code, key))

    findings: list[Finding] = []
    term_specimen_not_modelled_count = 0
    term_specimen_differs_count = 0
    term_timing_not_modelled_count = 0
    for candidate in candidates:
        asserted = candidate.asserted_group
        if asserted is not None and (candidate.code, asserted.key) in not_modelled:
            findings.append(_specimen_not_modelled_finding(candidate, asserted))
            term_specimen_not_modelled_count += 1
            continue
        if asserted is not None and (candidate.code, asserted.key) in differs:
            findings.append(_specimen_differs_finding(candidate, asserted))
            term_specimen_differs_count += 1
            continue
        # The specimen aspect is not asserted, agrees, or was suppressed by the
        # visibility filter; only now does timing get its own check.
        if candidate.timing is None:
            continue
        own_texts = _designation_texts(candidate.entries)
        timing_visible = _timing_visible_in(own_texts, candidate.timing)
        if not timing_visible and asserted is not None:
            described_group = described_by_code.get(asserted.specimen_code)
            if described_group is not None:
                group_texts = []
                if described_group.fully_specified_name is not None:
                    group_texts.append(_fold(described_group.fully_specified_name))
                if described_group.display is not None:
                    group_texts.append(_fold(described_group.display))
                group_texts.extend(_fold(value) for value in described_group.values)
                timing_visible = _timing_visible_in(group_texts, candidate.timing)
        if not timing_visible:
            findings.append(_timing_not_modelled_finding(candidate))
            term_timing_not_modelled_count += 1

    return SemanticDriftOutcome(
        findings=tuple(findings),
        run=DriftRun(
            rows_examined=len(candidates),
            rows_excluded=rows_excluded,
            term_specimen_not_modelled_count=term_specimen_not_modelled_count,
            term_specimen_differs_count=term_specimen_differs_count,
            term_timing_not_modelled_count=term_timing_not_modelled_count,
            specimen_table_entries_unresolved=specimen_table_entries_unresolved,
            describe_requests=describe_requests,
            classification_requests=classification_requests,
            resolved_versions=tuple(sorted(resolved_versions)),
        ),
    )
