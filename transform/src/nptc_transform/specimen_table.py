"""FR-75/H-03: the specimen vocabulary ``semantic_drift.py`` checks preferred terms against.

Each group takes its code and display from the reviewed specimen map (ADR-0044), so a
corrected code cannot leave this table behind. The wording stays here, because the map holds
workbook cell strings and not the phrases a curator writes inside a preferred term: it cannot
supply "stool" or "cerebrospinal fluid". Two groups name specimens the map has no row for, and
keep their own codes.

Every code is subsumed by ``<<123038009 |Specimen|``. ``urine_24h`` is a *descendant* of
``urine`` (122575003 subsumes 276833005). It is its own group, with ``timing`` set, because a
term asserting the 24-hour variant needs its timing assertion checked in addition to the plain
specimen check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from nptc_transform.specimen_map import SPECIMEN_MAP


@dataclass(frozen=True, slots=True)
class SpecimenGroup:
    """One specimen concept, and the hand-typed surface forms an RCPA curator
    plausibly writes for it inside a preferred term.

    ``key`` is stable, quoted in messages and asserted in tests; it is never
    derived from ``specimen_display``, which is documentation only. ``terms`` are
    casefolded surface forms, short and realistic rather than exhaustive: an
    allowlist a curator's vocabulary is checked *against*, not a corpus of every
    phrasing.
    """

    key: str
    #: The SNOMED specimen concept - what makes the group auditable.
    specimen_code: str
    #: Documentation only, NEVER compared against workbook content.
    specimen_display: str
    terms: tuple[str, ...]
    timing: str | None = None


@dataclass(frozen=True, slots=True)
class _Wording:
    """A group's preferred-term wording. ``map_string`` names the map row that supplies the
    code and display; ``None`` means the map has no row, and ``_OUTSIDE_THE_MAP`` does."""

    key: str
    terms: tuple[str, ...]
    map_string: str | None = None
    timing: str | None = None


_OUTSIDE_THE_MAP: Final = {
    "whole_blood": ("258580003", "Whole blood specimen"),
    "breast_milk": ("446676001", "Expressed breast milk specimen"),
}

#: Declaration order is the tie-break ``semantic_drift.py`` uses when a label's longest
#: matching surface form is equally long across two groups.
_WORDING: Final = (
    _Wording("urine", ("urine",), "Urine"),
    _Wording("csf", ("csf", "cerebrospinal fluid"), "CSF"),
    _Wording("faeces", ("faeces", "feces", "stool"), "Faeces"),
    _Wording("serum", ("serum",), "Serum"),
    _Wording("plasma", ("plasma",), "Plasma"),
    _Wording("whole_blood", ("whole blood",)),
    _Wording("saliva", ("saliva",), "Saliva"),
    _Wording("pleural_fluid", ("pleural fluid",), "Pleural fluid"),
    _Wording("synovial_fluid", ("synovial fluid",), "Synovial fluid"),
    _Wording("sputum", ("sputum",), "Sputum"),
    _Wording("swab", ("swab",), "swab"),
    _Wording("tissue", ("tissue",), "Tissue"),
    _Wording("bone_marrow", ("bone marrow",), "Bone marrow"),
    _Wording("breast_milk", ("breast milk", "expressed breast milk")),
    _Wording("semen", ("semen", "seminal fluid", "sperm"), "Semen"),
    _Wording(
        "urine_24h",
        ("24 hour urine", "24-hour urine", "24h urine", "24 hr urine"),
        "24 hr urine",
        timing="24 h",
    ),
)


def _group(wording: _Wording) -> SpecimenGroup:
    if wording.map_string is None:
        code, display = _OUTSIDE_THE_MAP[wording.key]
    else:
        entry = SPECIMEN_MAP.resolve(wording.map_string)
        if entry is None or entry.code is None or entry.display is None:
            raise ValueError(
                f"specimen group {wording.key!r} names {wording.map_string!r}, "
                "which the specimen map does not code"
            )
        code, display = entry.code, entry.display
    return SpecimenGroup(wording.key, code, display, wording.terms, wording.timing)


SPECIMEN_TABLE: tuple[SpecimenGroup, ...] = tuple(_group(wording) for wording in _WORDING)


def all_specimen_codes(table: tuple[SpecimenGroup, ...] = SPECIMEN_TABLE) -> tuple[str, ...]:
    """Every distinct ``specimen_code`` in ``table``, sorted (FR-73).

    The input to ``TerminologySweep.describe``, which resolves every group's
    designation set for the visibility filter and for messages
    (``semantic_drift.py``).
    """
    return tuple(sorted({group.specimen_code for group in table}))
