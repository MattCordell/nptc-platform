"""FR-05: reports the designation collisions the baseline loader refuses.

The loader (``nptc.catalogue.seed_import``) writes entries in ``(sheet, row)`` order and
refuses an entry whose term collides at error severity with one already written. This
pass finds the same pairs from the workbook alone, so an operator learns of them at
transform time (ADR-0010, PRD 6.3). ``nptc_shared.similarity.collision_key`` is the
comparison, shared with the backend so the two sides cannot disagree on what "equal"
means (FR-74).

Error severity, as the backend applies it:

- a preferred term equals an earlier entry's preferred term or synonym;
- a synonym equals an earlier entry's preferred term.

A synonym shared by two entries is warning severity and is not reported. A term that
repeats inside one entry is not a collision.

The pass reads ``rows.seedable_rows``, the rows ``dataset.build_dataset`` seeds, and the
strings ``dataset.py`` builds from them (``apply_corrections``, ``split_synonyms``), so
what it compares is what the loader would receive. It reports every later entry against
every earlier one, not only the first pair the loader stops at, so one run lists the
whole set. Resolving an earlier entry's term can clear a later finding that names it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nptc_shared.similarity import collision_key
from nptc_shared.text import escape_invisible
from nptc_transform.bands import FindingCode
from nptc_transform.cell_defects import split_synonyms
from nptc_transform.corrections import apply_corrections
from nptc_transform.findings import Finding
from nptc_transform.rows import SourceRow, seedable_rows
from nptc_transform.workbook import ColumnRole, Sheet

_PREFERRED = "preferred term"
_SYNONYM = "synonym"


@dataclass(frozen=True)
class _Holder:
    """An earlier entry's designation, as a collision names it."""

    sheet: str
    row: int
    kind: str
    term: str

    def describe(self) -> str:
        return f"the {self.kind} '{escape_invisible(self.term)}' on {self.sheet!r} row {self.row}"


def _join(holders: Sequence[_Holder]) -> str:
    return " and ".join(holder.describe() for holder in holders)


def _finding(
    source_row: SourceRow, role: ColumnRole, kind: str, term: str, holders: list[_Holder]
) -> Finding:
    return Finding(
        code=FindingCode.DESIGNATION_COLLISION,
        location=source_row.cells[role].reference,
        message=(
            f"the {kind} '{escape_invisible(term)}' on {source_row.sheet!r} row {source_row.row} "
            f"collides with {_join(holders)}; the seed loader refuses it (FR-05)"
        ),
    )


def check_collisions(sheets: Sequence[Sheet]) -> tuple[Finding, ...]:
    """One finding per (entry, colliding term) where the entry meets an earlier one.

    The finding sits on the later entry's cell and names every earlier holder.
    """
    preferred_holders: dict[str, list[_Holder]] = {}
    synonym_holders: dict[str, list[_Holder]] = {}
    findings: list[Finding] = []

    for source_row in seedable_rows(sheets):
        cells = source_row.cells
        preferred = apply_corrections(cells[ColumnRole.PREFERRED_TERM].text)
        preferred_key = collision_key(preferred)
        reported = {preferred_key}

        earlier = preferred_holders.get(preferred_key, []) + synonym_holders.get(preferred_key, [])
        if earlier:
            findings.append(
                _finding(source_row, ColumnRole.PREFERRED_TERM, _PREFERRED, preferred, earlier)
            )

        synonyms_cell = cells.get(ColumnRole.SYNONYMS)
        synonyms = split_synonyms(apply_corrections(synonyms_cell.text)) if synonyms_cell else ()
        own_synonym_keys: dict[str, str] = {}
        for synonym in synonyms:
            key = collision_key(synonym)
            own_synonym_keys.setdefault(key, synonym)
            if key in reported:
                continue
            reported.add(key)
            earlier = preferred_holders.get(key, [])
            if earlier:
                findings.append(
                    _finding(source_row, ColumnRole.SYNONYMS, _SYNONYM, synonym, earlier)
                )

        preferred_holders.setdefault(preferred_key, []).append(
            _Holder(source_row.sheet, source_row.row, _PREFERRED, preferred)
        )
        for key, synonym in own_synonym_keys.items():
            synonym_holders.setdefault(key, []).append(
                _Holder(source_row.sheet, source_row.row, _SYNONYM, synonym)
            )

    return tuple(findings)
