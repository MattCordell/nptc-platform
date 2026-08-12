"""FR-75/H-03: reports a semantic mismatch between the RCPA preferred term's
own specimen/timing wording and the bound SNOMED concept's modelled
``Has specimen`` value (issue #29, P0-7).

This is a **seeding-only, candidate-generating** concern, the same shape as
``designation_check.py`` (FR-97) and ``misspelling.py`` (FR-79): every finding
here is ``Band.INFORMATIONAL`` - "a candidate for editorial review, not a
confirmed defect" (see ``bands.py``) - because the check is a heuristic over
free text, calibrated against PRD Annex A.9's own worked examples, which show
roughly as many benign rows as genuine ones. A blocking band would be
indefensible at that false-positive rate.

**Unlike FR-97, a hierarchy violation (FR-84) is deliberately not excluded
here.** A term's specimen/timing drift finding is about the term's own
content, and survives whatever rebinding would fix the hierarchy violation -
the two findings describe different remediations of the same cell, and both
should be able to fire together.

**Request shape (ADR-0008).** Every code this pass classifies is resolved
against ``SNOMED_CT_AU`` specifically, never the dual-edition set
``terminology_check.DEFAULT_EDITIONS`` iterates for FR-74's own check: the
workbook's code-binding column is ``Terminology binding (SNOMED CT-AU)``, and
every specimen concept this module compares against was verified live in the
AU edition (see ``specimen_table.py``). One ``describe()`` call resolves the
whole specimen table's own designation sets (for the visibility filter and
for messages); one ``codes_without_attribute`` call and one
``codes_with_attribute_value`` call *per distinct group still asserted by an
unresolved row* classify the delta the visibility filter didn't already
settle - see ``check_semantic_drift``'s own docstring for why this is not
literally "1 + G" the way ADR-0008 first framed it, and why that is a
deliberate, documented deviation rather than a miscount.

**The specimen table is an allowlist, never a finding generator.** A term
asserting a specimen no group in ``specimen_table.SPECIMEN_TABLE`` covers is
silently never inspected for that aspect - this module's principal failure
mode. The mitigation is a coverage *audit*, never an assertion source: the
workbook's own ``Specimen`` column (a free-text field, not controlled
vocabulary - see ADR-0008's rejected alternatives) is checked only for how
many of its distinct values map to no group, rendered as
``DriftRun.specimen_column_values_unmapped``, so a systematically-uncovered
specimen never degrades to a silent zero findings with nothing to show for it.
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
from nptc_transform.findings import Finding
from nptc_transform.specimen_table import SPECIMEN_TABLE, SpecimenGroup, all_specimen_codes
from nptc_transform.terminology_check import CodeBinding
from nptc_transform.workbook import Cell, ColumnRole, Sheet

#: Excluded from the ``Specimen`` column coverage audit (module docstring):
#: FR-75's own plan names these as having separate findings elsewhere, so
#: counting them here as "unmapped" would double-count a gap this module
#: does not own.
_EXCLUDED_SPECIMEN_COLUMN_VALUES = frozenset({"any", "fluids"})

#: A timing assertion in a free-text label: 1-3 digits, optional dash/space,
#: then an hour/day unit word, at a word boundary. ``(?<![\w.])`` before the
#: digits is what keeps this from matching inside "B12", "Vitamin D3" or
#: "1,25 dihydroxy...": in each of those the digit run is preceded by a word
#: character or a decimal point, never by whitespace/punctuation/start-of-
#: string, and the unit alternation's trailing ``\b`` keeps "dihydroxy" from
#: matching the bare "d" branch (no boundary between "d" and the following
#: "i"). Verified against all three by test, not merely asserted.
_TIMING_RE = re.compile(
    r"(?<![\w.])(\d{1,3})\s*-?\s*(h|hr|hrs|hour|hours|d|day|days)\b", re.IGNORECASE
)

_HOUR_UNITS = frozenset({"h", "hr", "hrs", "hour", "hours"})
_DAY_UNITS = frozenset({"d", "day", "days"})


@dataclass(frozen=True)
class DriftRun:
    """What the semantic-drift pass covered, for the report's provenance block."""

    #: Rows with a checkable code, a usable preferred-term label, and a code
    #: resolved in at least one edition - the pass ran either way, whether or
    #: not it found anything.
    rows_examined: int = 0
    #: Rows excluded before classification: no checkable code binding, no
    #: preferred-term cell, a blank/whitespace-only label, or a code absent/
    #: inactive in every edition (already owned by CODE_NOT_FOUND/CODE_INACTIVE).
    rows_excluded: int = 0
    term_specimen_not_modelled_count: int = 0
    term_specimen_differs_count: int = 0
    term_timing_not_modelled_count: int = 0
    #: Specimen-table SCTIDs ``describe()`` resolved nothing for - see
    #: ``TerminologyRun.unresolved_fsn_count``'s identical "the check did not
    #: actually run for that many, not a silent pass" reasoning. That group
    #: still functions on its hand-typed terms alone, just without server
    #: augmentation.
    specimen_table_entries_unresolved: int = 0
    #: Distinct non-empty ``Specimen`` column values that map to no group in
    #: ``specimen_table.SPECIMEN_TABLE`` (excluding ``Any``/``Fluids``) - the
    #: coverage audit for this module's own allowlist blind spot. Purely an
    #: audit counter: never fed back into classification.
    specimen_column_values_unmapped: int = 0
    #: ``$expand``-style requests issued resolving the specimen table's own
    #: designations (``TerminologySweep.describe``) - always at most
    #: ``ceil(len(specimen table) / chunk_size)``, never catalogue-scale.
    describe_requests: int = 0
    #: ``$expand``-style requests issued classifying the delta the visibility
    #: filter didn't already settle (``codes_without_attribute`` plus one
    #: ``codes_with_attribute_value`` per distinct group still asserted by an
    #: unresolved row) - see ``check_semantic_drift``'s docstring for why this
    #: is not literally "describe_requests + classification_requests == 1 + G".
    classification_requests: int = 0
    #: Every fully qualified version URI resolved across this pass's
    #: ``describe``/``codes_without_attribute``/``codes_with_attribute_value``
    #: calls (FR-48) - the same provenance ``SweepResult.resolved_versions``
    #: carries for the terminology pass proper.
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
    """``normalise_for_comparison(text).casefold()`` - a substring heuristic
    over free text, where case is noise, not FR-97's identity comparison
    against a served designation (which deliberately never casefolds, since a
    case difference there is a real editorial signal). Here the label is
    being scanned for a specimen/timing *word*, not compared for equality, so
    folding case out is the right choice for this pass specifically.
    """
    return normalise_for_comparison(text).casefold()


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(term) + r"\b")


def _rows_by_role(sheet: Sheet) -> dict[int, dict[ColumnRole, Cell]]:
    """Groups ``sheet.cells`` by row, keeping the code, preferred-term and
    specimen-column cells.

    Mirrors ``designation_check._rows_by_role``'s own minimal, private row
    view rather than generalising it in place: that function is scoped to
    exactly the two roles FR-97 needs, and widening it there risks changing
    behaviour for a pass this PR must not touch.
    """
    rows: dict[int, dict[ColumnRole, Cell]] = defaultdict(dict)
    for cell in sheet.cells:
        if cell.role in (ColumnRole.CODE, ColumnRole.PREFERRED_TERM, ColumnRole.SPECIMEN):
            rows[cell.row][cell.role] = cell
    return rows


def _entries_for_code(
    code: str, designations_by_label: Mapping[str, Mapping[str, ConceptDesignations]]
) -> dict[str, ConceptDesignations]:
    return {
        label: entries[code] for label, entries in designations_by_label.items() if code in entries
    }


def _longest_match(folded_text: str, table: Sequence[SpecimenGroup]) -> SpecimenGroup | None:
    """The group whose hand-typed term is the longest one matching
    ``folded_text`` at a word boundary; ties broken by ``table``'s own
    declaration order (never by iteration order of some derived set, so this
    stays deterministic - FR-73).
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
    """The label's own timing assertion, canonicalised so ``24h``/``24 hr``/
    ``24 hour`` all produce the identical string - see the module-level regex
    for why ``B12``, ``Vitamin D3`` and ``1,25 dihydroxy...`` never match."""
    match = _TIMING_RE.search(folded_label)
    if match is None:
        return None
    number = str(int(match.group(1)))
    unit = match.group(2).lower()
    canonical_unit = "h" if unit in _HOUR_UNITS else "d"
    return f"{number} {canonical_unit}"


def _timing_visible_in(texts: Iterable[str], timing: str) -> bool:
    """True if any of ``texts`` carries the same canonicalised timing
    assertion as ``timing``.

    Runs each text through ``_extract_timing`` rather than word-boundary
    matching the canonical string (``"24 h"``) literally: a served
    designation reading ``"24 hour urine specimen"`` or ``"24h urine"``
    contains ``"24 h"`` as a substring with no word boundary between the "h"
    and the following letter, so a literal match would never fire on wording
    that plainly does carry the timing - the exact false positive this check
    exists to prevent.
    """
    return any(_extract_timing(text) == timing for text in texts)


def _designation_texts(entries: Mapping[str, ConceptDesignations]) -> list[str]:
    """Every folded FSN/display/designation-value string ``entries`` carries,
    across every edition - the search space for both the specimen visibility
    filter and the timing check."""
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


def _specimen_column_unmapped_count(sheets: Sequence[Sheet], table: Sequence[SpecimenGroup]) -> int:
    """FR-75's principal-failure-mode mitigation: how many distinct, non-empty
    ``Specimen`` column values map to no group at all. Purely a coverage
    audit - see the module docstring - never fed back into classification."""
    distinct_values: set[str] = set()
    for sheet in sheets:
        for cell in sheet.cells:
            if cell.role is not ColumnRole.SPECIMEN:
                continue
            folded = _fold(cell.text)
            if not folded or folded in _EXCLUDED_SPECIMEN_COLUMN_VALUES:
                continue
            distinct_values.add(folded)
    return sum(1 for value in distinct_values if _longest_match(value, table) is None)


def check_semantic_drift(
    sheets: Sequence[Sheet],
    *,
    sweep: TerminologySweep,
    bindings: Sequence[CodeBinding],
    results: Mapping[str, SweepResult],
) -> SemanticDriftOutcome:
    """Reconciles every row's RCPA preferred term against its bound concept's
    modelled ``Has specimen`` value and any timing wording (FR-75, issue #29).

    ``bindings`` and ``results`` are ``terminology_check.check_terminology``'s
    own output for the same workbook - reused rather than recomputed (FR-74),
    the same convention ``designation_check.check_designations`` follows.

    **On the "1 + G" request-count question this pass was specced against.**
    ADR-0008's classification step needs two structurally distinct pieces of
    server data that cannot be merged into one call: (1) the specimen table's
    own designation sets (``sweep.describe``, for the visibility filter and
    for messages - always exactly one logical call, however many chunks the
    table's own size requires), and (2) which asserting codes constrain *no*
    ``Has specimen`` value at all (``sweep.codes_without_attribute``, one
    call over every still-unresolved asserting code, however many groups they
    span) - without (2), ``TERM_SPECIMEN_NOT_MODELLED`` and
    ``TERM_SPECIMEN_DIFFERS`` could not be told apart. Only the *third* piece
    - "does code X's value agree with group G specifically"
    (``sweep.codes_with_attribute_value``) - scales with ``G``, the number of
    distinct groups still asserted after the visibility filter. The total is
    therefore ``2 + G``, not ``1 + G``: this module's own request-count test
    asserts exactly that, and this docstring records the deviation rather
    than forcing an artificial merge that would either lose the visibility
    filter's vocabulary or collapse the NOT_MODELLED/DIFFERS distinction.
    """
    checkable_locations = {binding.location for binding in bindings}
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
                # Absent/inactive in every edition - CODE_NOT_FOUND/
                # CODE_INACTIVE already own it (see terminology_check.py).
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

    specimen_column_values_unmapped = _specimen_column_unmapped_count(sheets, SPECIMEN_TABLE)

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

    # Keyed by (code, group.key), not code alone: the same SCTID can be bound
    # by rows asserting different specimen groups (duplicate bindings occur
    # in the workbook), and agreement is a per-(code, group) question - a
    # code that agrees with group A but not group B must not have group B's
    # verdict bleed into group A's row.
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
        # Specimen aspect is either not asserted, or agrees/was suppressed by
        # the visibility filter - only now does timing get its own check.
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
            specimen_column_values_unmapped=specimen_column_values_unmapped,
            describe_requests=describe_requests,
            classification_requests=classification_requests,
            resolved_versions=tuple(sorted(resolved_versions)),
        ),
    )
