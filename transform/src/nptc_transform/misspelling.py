"""FR-79/H-04: heuristic misspelling detection over the RCPA preferred-term
and synonyms columns (issue #29, P0-7).

**Scope.** Only ``ColumnRole.PREFERRED_TERM`` and ``ColumnRole.SYNONYMS``
cells are read. This pass consumes the FR-52 sweep's *already-resolved*
per-edition ``SweepResult`` mapping - the same shape ``check_designations``
consumes - and issues zero terminology requests of its own; it never holds a
live ``sweep``. Both finding codes are ``Band.INFORMATIONAL`` (``bands.py``):
candidates for editorial review, never auto-corrections, and never blocking.

**Two heuristics, in order of reliability (FR-79's own words).**

1. **Intra-entry near-match.** Within one workbook row's own preferred-term
   and synonym cells (plus, when a completed sweep is available, the served
   designations of the concept that row's code binds to), a token near a
   near-match reference is a probable in-entry misspelling
   (``PROBABLE_MISSPELLING``). This is the heuristic that catches a typo with
   zero cross-row reasoning at all - ``Epinephine`` next to a served
   ``Epinephrine`` needs only the one row.
2. **Cross-entry corpus frequency.** A token seen in only a handful of
   entries that near-matches one seen in many more is a probable spelling
   drift across the corpus (``INCONSISTENT_SPELLING``). Heuristic 1 always
   takes precedence for the same ``(cell, token)`` pair - at most one finding
   per cell/token, across both heuristics combined.

**The authority set is a whitelist only, never a finding generator.** Every
token found in any edition's served designation values or FSN, when a sweep
ran, is authoritative - it can be cited as a *reference*, but its own
``token_key`` can never itself be named a *suspect*, in either heuristic.
This is what keeps a synonym column's real brand names and genuine
abbreviations - not carried by any SNOMED FSN - from being false-flagged
merely for being unusual, while a token that genuinely doesn't match
anything served is judged purely on its own corpus behaviour.

**Without a sweep (``results=None``), both heuristics still run in full** -
nothing is suppressed - but the authority set is empty, so precision is
lower and ``MisspellingRun.authority_source`` records ``WORKBOOK_ONLY``
rather than ``SWEEP`` so the report can say so explicitly (see
``report_writer._render_misspellings``).

**Thresholds are module constants, not configuration** (mirroring FR-79's
own "no NPTC_TX_* here" stance - see ADR-0007): a judgement call, stated
once, not a lever an operator is expected to tune per catalogue.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from nptc_shared.similarity import (
    LONG_TOKEN_LENGTH,
    MAX_EDIT_DISTANCE,
    MIN_TOKEN_LENGTH,
    is_comparable_token,
    near_match_distance,
    token_key,
    tokenise,
)
from nptc_shared.terminology.sweep import ConceptDesignations, SweepResult
from nptc_shared.text import escape_invisible
from nptc_transform.bands import FindingCode
from nptc_transform.findings import Finding
from nptc_transform.workbook import Cell, ColumnRole, Sheet

#: Heuristic 2's own thresholds (FR-79's "in order of reliability", operationalised
#: as: rare enough to be suspect, common enough to be trusted, and the gap between
#: them wide enough that "coincidence" is not the likelier explanation). Judgement
#: calls, not measurements - see ADR-0007's "how to tune these constants".
MAX_RARE_COUNT = 2
MIN_COMMON_COUNT = 3
COMMON_TO_RARE_RATIO = 3

#: Echoed verbatim into ``report.json``'s ``thresholds`` object
#: (``report_writer._misspellings_payload``) so a reader never has to cross-reference
#: this module's source to know what produced a finding.
THRESHOLDS: dict[str, int] = {
    "min_token_length": MIN_TOKEN_LENGTH,
    "max_edit_distance": MAX_EDIT_DISTANCE,
    "long_token_length": LONG_TOKEN_LENGTH,
    "max_rare_count": MAX_RARE_COUNT,
    "min_common_count": MIN_COMMON_COUNT,
    "common_to_rare_ratio": COMMON_TO_RARE_RATIO,
}

_ENTRY_ROLES = (ColumnRole.PREFERRED_TERM, ColumnRole.SYNONYMS)


class AuthoritySource(StrEnum):
    """Where the authority whitelist came from, for the report's provenance
    block - mirrors ``DesignationRun``'s "say what ran, not just what was
    found" reasoning (``designation_check.DesignationRun``)."""

    #: Built from a completed FR-52 sweep's served designations/FSNs.
    SWEEP = "SWEEP"
    #: No sweep was available; the authority set is empty and both
    #: heuristics ran on the workbook's own content alone.
    WORKBOOK_ONLY = "WORKBOOK_ONLY"


@dataclass(frozen=True)
class MisspellingRun:
    """What the misspelling pass covered, for the report's provenance block."""

    #: Distinct preferred-term/synonym cells read.
    cells_scanned: int = 0
    #: Comparable-eligible token occurrences considered (see
    #: ``is_comparable_token``) - not distinct tokens, every occurrence.
    tokens_considered: int = 0
    probable_misspelling_count: int = 0
    inconsistent_spelling_count: int = 0
    authority_source: AuthoritySource = AuthoritySource.WORKBOOK_ONLY


@dataclass(frozen=True)
class MisspellingOutcome:
    """The misspelling pass's findings, plus its provenance record."""

    findings: tuple[Finding, ...]
    run: MisspellingRun


@dataclass(frozen=True)
class _Entry:
    """One workbook row's preferred-term/synonym cells, plus the code it binds to.

    Grouped by ``(sheet name, row)`` - mirrors ``designation_check._rows_by_role``'s
    own minimal, private row view, adapted to the two roles this pass reads.
    """

    key: tuple[str, int]
    code: str | None
    cells: tuple[Cell, ...]


@dataclass
class _RowData:
    """Mutable accumulator for one ``(sheet, row)`` while grouping - never
    exposed outside ``_group_entries``, which freezes it into an ``_Entry``.
    """

    code: str | None = None
    cells: list[Cell] = field(default_factory=list)


def _group_entries(sheets: Sequence[Sheet]) -> list[_Entry]:
    rows: dict[tuple[str, int], _RowData] = defaultdict(_RowData)
    for sheet in sheets:
        for cell in sheet.cells:
            if cell.role is ColumnRole.CODE:
                rows[(sheet.name, cell.row)].code = cell.text.strip()
            elif cell.role in _ENTRY_ROLES:
                rows[(sheet.name, cell.row)].cells.append(cell)
    entries = []
    for key in sorted(rows):
        data = rows[key]
        if not data.cells:
            continue
        entries.append(_Entry(key=key, code=data.code or None, cells=tuple(data.cells)))
    return entries


def _entry_tokens(entry: _Entry) -> list[tuple[Cell, str, str]]:
    """``(cell, surface, token_key)`` for every comparable-eligible token in
    ``entry``'s own preferred-term/synonym cells."""
    out: list[tuple[Cell, str, str]] = []
    for cell in entry.cells:
        for surface in tokenise(cell.text):
            if is_comparable_token(surface):
                out.append((cell, surface, token_key(surface)))
    return out


def _reference_extras(
    entry: _Entry, designations_by_label: Mapping[str, Mapping[str, ConceptDesignations]]
) -> list[tuple[str, str]]:
    """Heuristic 1's arm (b): ``(surface, token_key)`` from the served
    designation values and FSN of the concept ``entry.code`` binds to, across
    every edition the sweep resolved it in.

    This is what lets a single-row entry (``Epinephine``, no other row
    involved at all) still be caught - the reference material comes from the
    server, not from any other workbook row.
    """
    if entry.code is None:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for entries_by_code in designations_by_label.values():
        concept = entries_by_code.get(entry.code)
        if concept is None:
            continue
        texts = list(concept.values)
        if concept.fully_specified_name is not None:
            texts.append(concept.fully_specified_name)
        for text in texts:
            for surface in tokenise(text):
                if not is_comparable_token(surface):
                    continue
                key = token_key(surface)
                if key not in seen:
                    seen.add(key)
                    out.append((surface, key))
    return out


def _corpus_index(entries: Sequence[_Entry]) -> tuple[dict[str, int], dict[str, str]]:
    """Corpus-wide, per ``token_key``: the number of *entries* carrying it at
    least once, and the surface form seen in the most entries (ties broken
    lexicographically, for deterministic display, FR-73) - never
    occurrence/cell counts.

    The representative surface must be the common one, not merely the
    alphabetically-first one: a message citing "the far more common"
    spelling has to actually be the more common spelling, not whichever
    variant happens to sort first (e.g. a single-entry ``ANTENATAL`` sorting
    before a 200-entry ``Antenatal``).
    """
    row_counts: dict[str, int] = defaultdict(int)
    surface_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for entry in entries:
        surfaces_by_key: dict[str, set[str]] = defaultdict(set)
        for _cell, surface, key in _entry_tokens(entry):
            surfaces_by_key[key].add(surface)
        for key, surfaces in surfaces_by_key.items():
            row_counts[key] += 1
            counts = surface_counts[key]
            for surface in surfaces:
                counts[surface] = counts.get(surface, 0) + 1
    surface_by_key = {
        key: min(counts, key=lambda surface: (-counts[surface], surface))
        for key, counts in surface_counts.items()
    }
    return dict(row_counts), surface_by_key


def _authority_set(results: Mapping[str, SweepResult] | None) -> frozenset[str]:
    """Every ``token_key`` of every served designation value or FSN, across
    every edition - the whitelist, per the module docstring. Empty when
    ``results`` is ``None`` (``AuthoritySource.WORKBOOK_ONLY``).
    """
    if results is None:
        return frozenset()
    keys: set[str] = set()
    for result in results.values():
        for entry in result.designations:
            for value in entry.values:
                keys.update(token_key(t) for t in tokenise(value))
            if entry.fully_specified_name is not None:
                keys.update(token_key(t) for t in tokenise(entry.fully_specified_name))
    return frozenset(keys)


def _can_be_suspect(surface: str, cell: Cell | None) -> bool:
    """The suspect-only restriction ``similarity.is_comparable_token``
    deliberately leaves out (see its docstring): an all-uppercase surface
    form is a fine *reference* but can never itself be named a *suspect*,
    and a reference with no cell of its own (heuristic 1's arm (b), served
    designation material) can never be a suspect either - there is nowhere
    to put the finding.
    """
    return cell is not None and surface != surface.upper()


@dataclass(frozen=True)
class _Side:
    """One side of a near-match pair under consideration by ``_decide_suspect``.

    A `(surface, key, role, cell)` quartet, named once: the two-sided
    signature this replaces passed it twice per call, positionally, which is
    an arg-order transposition hazard for no benefit.
    """

    surface: str
    key: str
    role: ColumnRole | None
    cell: Cell | None


def _decide_suspect(
    a: _Side,
    b: _Side,
    authority: frozenset[str],
    row_counts: Mapping[str, int],
) -> tuple[str, str, Cell, str, str] | None:
    """Given a near-match pair, decides which side is the suspect.

    The total order FR-79's plan specifies, checked in sequence, first match
    wins: (1) authority asymmetry, (2) corpus-wide row-count, (3) a
    same-entry synonym-vs-preferred-term tie-break, (4) silence - with no
    evidence of which spelling is correct, FR-72 requires a finding to be
    able to state the required action, and there isn't one.

    Returns ``(suspect_surface, suspect_key, suspect_cell, reference_surface,
    reference_key)``, or ``None`` if no finding should result - including
    when the side the rules point to cannot be a suspect at all
    (``_can_be_suspect``), in which case the pairing contributes nothing
    rather than being redirected onto the other side without evidence.
    """
    a_in_authority = a.key in authority
    b_in_authority = b.key in authority
    if a_in_authority != b_in_authority:
        a_is_suspect = not a_in_authority
    else:
        count_a, count_b = row_counts.get(a.key, 0), row_counts.get(b.key, 0)
        if count_a != count_b:
            a_is_suspect = count_a < count_b
        else:
            roles = {a.role, b.role}
            if a.role != b.role and roles == {ColumnRole.PREFERRED_TERM, ColumnRole.SYNONYMS}:
                a_is_suspect = a.role is ColumnRole.SYNONYMS
            else:
                return None

    if a_is_suspect:
        if not _can_be_suspect(a.surface, a.cell):
            return None
        assert a.cell is not None
        return (a.surface, a.key, a.cell, b.surface, b.key)
    if not _can_be_suspect(b.surface, b.cell):
        return None
    assert b.cell is not None
    return (b.surface, b.key, b.cell, a.surface, a.key)


def _distance_words(distance: int) -> str:
    return {1: "one character", 2: "two characters"}.get(distance, f"{distance} characters")


def _probable_misspelling_finding(
    cell: Cell, surface: str, reference: str, distance: int
) -> Finding:
    return Finding(
        code=FindingCode.PROBABLE_MISSPELLING,
        location=cell.reference,
        message=(
            f"'{escape_invisible(surface)}' differs from this entry's own "
            f"'{escape_invisible(reference)}' by {_distance_words(distance)}; a probable "
            "misspelling, flagged for editorial review; never auto-corrected (FR-79)"
        ),
    )


def _inconsistent_spelling_finding(
    cell: Cell, surface: str, reference: str, distance: int, rare_count: int, common_count: int
) -> Finding:
    return Finding(
        code=FindingCode.INCONSISTENT_SPELLING,
        location=cell.reference,
        message=(
            f"'{escape_invisible(surface)}' (used in {rare_count} entries across the "
            f"corpus) differs from the far more common '{escape_invisible(reference)}' "
            f"(used in {common_count} entries) by {_distance_words(distance)}; a probable "
            "inconsistent spelling, flagged for editorial review; never auto-corrected (FR-79)"
        ),
    )


def _heuristic_one(
    entries: Sequence[_Entry],
    designations_by_label: Mapping[str, Mapping[str, ConceptDesignations]],
    authority: frozenset[str],
    row_counts: Mapping[str, int],
) -> dict[tuple[str, str], Finding]:
    """Intra-entry near-match. Returns findings keyed by ``(cell.reference,
    token_key)`` - the shape heuristic 2 needs to know which cells/tokens it
    must not also flag.
    """
    findings: dict[tuple[str, str], Finding] = {}
    for entry in entries:
        own = _entry_tokens(entry)
        extra = _reference_extras(entry, designations_by_label)
        candidates: list[_Side] = [
            _Side(surface, key, cell.role, cell) for cell, surface, key in own
        ]
        candidates.extend(_Side(surface, key, None, None) for surface, key in extra)

        for a_cell, a_surface, a_key in own:
            a_side = _Side(a_surface, a_key, a_cell.role, a_cell)
            best: tuple[int, int, str, str] | None = None
            for b_side in candidates:
                if a_key == b_side.key:
                    continue
                distance = near_match_distance(a_key, b_side.key)
                if distance is None:
                    continue
                outcome = _decide_suspect(a_side, b_side, authority, row_counts)
                if outcome is None:
                    continue
                suspect_surface, suspect_key, suspect_cell, ref_surface, ref_key = outcome
                if (
                    suspect_cell is not a_cell
                    or suspect_key != a_key
                    or suspect_surface != a_surface
                ):
                    # This pairing's suspect is the *other* side - it will be
                    # (or already was) considered on its own turn as "a", if
                    # it has a cell to be flagged against at all.
                    continue
                # Tie-break among multiple qualifying references for the same
                # suspect: minimum distance, then maximum reference row-count,
                # then lexicographically smallest reference surface (never
                # Counter.most_common - FR-73 forbids insertion-order ties).
                candidate_key = (distance, -row_counts.get(ref_key, 0), ref_surface)
                if best is None or candidate_key < (best[0], best[1], best[2]):
                    best = (distance, -row_counts.get(ref_key, 0), ref_surface, ref_key)
            if best is not None:
                distance, _neg_count, ref_surface, _ref_key = best
                findings[(a_cell.reference, a_key)] = _probable_misspelling_finding(
                    a_cell, a_surface, ref_surface, distance
                )
    return findings


def _heuristic_two_candidates(
    row_counts: Mapping[str, int], surface_by_key: Mapping[str, str], authority: frozenset[str]
) -> dict[str, tuple[int, str, str]]:
    """For every rare ``token_key`` qualifying under the thresholds, the best
    common reference to cite: ``rare_key -> (distance, common_surface, common_key)``.

    Candidate keys are bucketed by length, and restricted up front to keys
    that pass the *count-only* half of the common-reference threshold
    (``MIN_COMMON_COUNT`` - the ``COMMON_TO_RARE_RATIO`` half also depends on
    the rare key's own count, so it cannot be applied until inside the
    per-rare-key loop). This is a modest, honestly-scoped saving, not the
    fix for this heuristic's cost centre: ``bounded_edit_distance`` already
    rejects a length-incompatible pair in O(1) on its own first line, so
    almost every call this bucketing removes was one that would have
    returned ``None`` immediately anyway, never reaching the DP. Measured
    against the unbucketed scan (see PR review on issue #29), the call
    count drops sharply but the count of calls that actually reach the DP
    - the real cost - does not; restricting the bucket to count-eligible
    keys, as done here, buys a further ~1.2x by shrinking the candidate
    lists themselves, but this is still not a structural fix.

    The heuristic's actual cost centre is the DP over the surviving,
    length-compatible rare/common pairs, and that stays open: closing it
    properly means an actual index (e.g. a SymSpell-style
    deletion-neighbourhood lookup) rather than any bucketing scheme -
    out of scope here unless real catalogue measurements show this
    heuristic is a practical bottleneck on an actual SPIA-sized workbook.
    """
    keys = sorted(row_counts)
    keys_by_length: dict[int, list[str]] = defaultdict(list)
    for key in keys:
        if row_counts[key] >= MIN_COMMON_COUNT:
            keys_by_length[len(key)].append(key)

    result: dict[str, tuple[int, str, str]] = {}
    for rare_key in keys:
        if rare_key in authority:
            continue
        rare_count = row_counts[rare_key]
        if rare_count > MAX_RARE_COUNT:
            continue
        best: tuple[int, int, str, str] | None = None
        rare_length = len(rare_key)
        candidate_lengths = range(
            rare_length - MAX_EDIT_DISTANCE, rare_length + MAX_EDIT_DISTANCE + 1
        )
        candidate_keys = (
            common_key
            for length in candidate_lengths
            for common_key in keys_by_length.get(length, ())
        )
        for common_key in candidate_keys:
            if common_key == rare_key:
                continue
            common_count = row_counts[common_key]
            if common_count < MIN_COMMON_COUNT or common_count < COMMON_TO_RARE_RATIO * rare_count:
                continue
            distance = near_match_distance(rare_key, common_key)
            if distance is None:
                continue
            candidate = (distance, -common_count, surface_by_key[common_key], common_key)
            if best is None or candidate < best:
                best = candidate
        if best is not None:
            distance, _neg_count, common_surface, common_key = best
            result[rare_key] = (distance, common_surface, common_key)
    return result


def check_misspellings(
    sheets: Sequence[Sheet],
    *,
    results: Mapping[str, SweepResult] | None = None,
) -> MisspellingOutcome:
    """Runs both FR-79 heuristics over ``sheets``' preferred-term/synonym cells.

    ``results`` is the FR-52 sweep's already-resolved per-edition mapping
    (``check_terminology``'s own ``outcome.results``) - never a live
    ``sweep``, since this pass issues zero terminology requests of its own.
    When ``results`` is ``None`` both heuristics still run in full, over the
    workbook's own content alone; only the authority whitelist is empty.
    """
    entries = _group_entries(sheets)
    designations_by_label: dict[str, dict[str, ConceptDesignations]] = (
        {
            label: {entry.code: entry for entry in result.designations}
            for label, result in results.items()
        }
        if results is not None
        else {}
    )
    authority = _authority_set(results)
    row_counts, surface_by_key = _corpus_index(entries)

    h1_findings = _heuristic_one(entries, designations_by_label, authority, row_counts)
    h2_candidates = _heuristic_two_candidates(row_counts, surface_by_key, authority)

    h2_findings: dict[tuple[str, str], Finding] = {}
    cells_scanned: set[str] = set()
    tokens_considered = 0
    for entry in entries:
        for cell in entry.cells:
            cells_scanned.add(cell.reference)
        for cell, surface, key in _entry_tokens(entry):
            tokens_considered += 1
            if key not in h2_candidates:
                continue
            if not _can_be_suspect(surface, cell):
                continue  # ADR-0007 Decision 5 applies to both heuristics, not just h1.
            if (cell.reference, key) in h1_findings:
                continue  # heuristic 1 takes precedence for this cell/token.
            location_key = (cell.reference, key)
            if location_key in h2_findings:
                continue
            distance, common_surface, common_key = h2_candidates[key]
            h2_findings[location_key] = _inconsistent_spelling_finding(
                cell,
                surface,
                common_surface,
                distance,
                row_counts[key],
                row_counts[common_key],
            )

    findings = tuple(sorted((*h1_findings.values(), *h2_findings.values()), key=Finding.sort_key))
    return MisspellingOutcome(
        findings=findings,
        run=MisspellingRun(
            cells_scanned=len(cells_scanned),
            tokens_considered=tokens_considered,
            probable_misspelling_count=len(h1_findings),
            inconsistent_spelling_count=len(h2_findings),
            authority_source=AuthoritySource.SWEEP
            if results is not None
            else AuthoritySource.WORKBOOK_ONLY,
        ),
    )
