"""FR-75/H-03: the RCPA-workbook specimen vocabulary ``semantic_drift.py`` checks
published labels against.

Transform-scoped, not ``shared/``, like the other seeding-only concerns
(``terminology_check.py``). The table is RCPA workbook vocabulary (how a curator
types "specimen" in a free-text cell), not a SNOMED CT client concern, and the
backend has no use for it.

Every ``specimen_code`` was verified against SNOMED CT-AU: each is subsumed by
``<<123038009 |Specimen|`` and is the FSN-bearing "X specimen (specimen)" concept
for its group, unless its group says otherwise. ``urine_24h`` is a *descendant* of
``urine`` (122575003 subsumes 276833005). It is its own group, with ``timing``
set, because a term asserting the 24-hour variant needs its timing assertion
checked in addition to the plain specimen check.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpecimenGroup:
    """One specimen concept, and the hand-typed surface forms an RCPA curator
    plausibly writes for it in a free-text cell.

    ``key`` is stable, quoted in messages and asserted in tests; it is never
    derived from ``specimen_display``, which is documentation only. ``terms`` are
    casefolded surface forms, short and realistic rather than exhaustive: an
    allowlist a curator's vocabulary is checked *against*, not a corpus of every
    phrasing. ``semantic_drift.py``'s coverage audit handles a term not covered
    here.
    """

    key: str
    #: The SNOMED specimen concept - what makes the group auditable.
    specimen_code: str
    #: Documentation only, NEVER compared against workbook content.
    specimen_display: str
    terms: tuple[str, ...]
    timing: str | None = None


#: The groups verified against SNOMED CT-AU (see the module docstring).
#: Declaration order is the tie-break ``semantic_drift.py`` uses when a label's
#: longest matching surface form is equally long across two groups.
SPECIMEN_TABLE: tuple[SpecimenGroup, ...] = (
    SpecimenGroup(
        key="urine",
        specimen_code="122575003",
        specimen_display="Urine specimen",
        terms=("urine",),
    ),
    SpecimenGroup(
        key="csf",
        specimen_code="258450006",
        specimen_display="Cerebrospinal fluid specimen",
        terms=("csf", "cerebrospinal fluid"),
    ),
    SpecimenGroup(
        key="faeces",
        specimen_code="119339001",
        specimen_display="Stool specimen",
        terms=("faeces", "feces", "stool"),
    ),
    SpecimenGroup(
        key="serum",
        specimen_code="119364003",
        specimen_display="Serum specimen",
        terms=("serum",),
    ),
    SpecimenGroup(
        key="plasma",
        specimen_code="119361006",
        specimen_display="Plasma specimen",
        terms=("plasma",),
    ),
    SpecimenGroup(
        key="whole_blood",
        specimen_code="258580003",
        specimen_display="Whole blood specimen",
        terms=("whole blood",),
    ),
    SpecimenGroup(
        key="saliva",
        specimen_code="119342007",
        specimen_display="Saliva specimen",
        terms=("saliva",),
    ),
    SpecimenGroup(
        key="pleural_fluid",
        specimen_code="418564007",
        specimen_display="Pleural fluid specimen",
        terms=("pleural fluid",),
    ),
    SpecimenGroup(
        key="synovial_fluid",
        specimen_code="119332005",
        specimen_display="Synovial fluid specimen",
        terms=("synovial fluid",),
    ),
    SpecimenGroup(
        key="sputum",
        specimen_code="119334006",
        specimen_display="Sputum specimen",
        terms=("sputum",),
    ),
    SpecimenGroup(
        key="swab",
        specimen_code="257261003",
        specimen_display="Swab (specimen)",
        terms=("swab",),
    ),
    SpecimenGroup(
        key="tissue",
        specimen_code="119376003",
        specimen_display="Tissue specimen",
        terms=("tissue",),
    ),
    SpecimenGroup(
        key="bone_marrow",
        specimen_code="119359002",
        specimen_display="Bone marrow specimen",
        terms=("bone marrow",),
    ),
    SpecimenGroup(
        key="breast_milk",
        specimen_code="446676001",
        specimen_display="Expressed breast milk specimen",
        terms=("breast milk", "expressed breast milk"),
    ),
    SpecimenGroup(
        key="semen",
        specimen_code="119347001",
        specimen_display="Seminal fluid specimen",
        terms=("semen", "seminal fluid", "sperm"),
    ),
    SpecimenGroup(
        key="urine_24h",
        specimen_code="276833005",
        specimen_display="24 hour urine specimen",
        terms=("24 hour urine", "24-hour urine", "24h urine", "24 hr urine"),
        timing="24 h",
    ),
)


def all_specimen_codes(table: tuple[SpecimenGroup, ...] = SPECIMEN_TABLE) -> tuple[str, ...]:
    """Every distinct ``specimen_code`` in ``table``, sorted (FR-73).

    The input to ``TerminologySweep.describe``, which resolves every group's
    designation set for the visibility filter and for messages
    (``semantic_drift.py``).
    """
    return tuple(sorted({group.specimen_code for group in table}))
