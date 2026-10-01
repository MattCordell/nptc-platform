"""FR-98's label-provenance vocabulary.

Every SNOMED/catalogue label field on the API states, in the payload, which
designation it is and, for an FSN, whether its semantic tag is intact or
stripped. This module defines that vocabulary once; response models
(`nptc.api.routers.catalogue_shared`, `nptc.api.routers.terminology`) attach
it to their own fields.

**`PREFERRED_VARIANT`, not "rcpa_preferred_term", for a `designation` row
with `use == "preferred"`.** ADR-0022 keeps the catalogue's en-AU preferred
term only on `catalogue_entry.preferred_term`, enforced by
`ck_designation_no_en_au_preferred`. A preferred `designation` row is
therefore a preferred term in another language (e.g. `mi-NZ`), which
"preferred variant" names correctly.

**The read path contains no tag-stripping code**, so `SemanticTagState` is
config-driven, not computed: `fsn_provenance` reports
`ApiSettings.fsn_semantic_tag` (FR-66's placeholder) and never inspects the
FSN string. It is the only reader of that setting. `binding_from_row` and
`ConceptLookup` both call it, so no caller can observe two opinions about
whether an FSN's tag is intact.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from nptc.settings import ApiSettings

__all__ = [
    "AU_PREFERRED_TERM_PROVENANCE",
    "PREFERRED_VARIANT_PROVENANCE",
    "SYNONYM_PROVENANCE",
    "DesignationType",
    "LabelProvenance",
    "SemanticTagState",
    "fsn_provenance",
]


class DesignationType(StrEnum):
    """Which designation a label-bearing field holds - the wire values
    named in the FR-98 plan, and nothing else: a client branches on these,
    so a new value is a deliberate addition here, not an incidental rename."""

    FSN = "fsn"
    AU_PREFERRED_TERM = "au_preferred_term"
    SYNONYM = "synonym"
    PREFERRED_VARIANT = "preferred_variant"


class SemanticTagState(StrEnum):
    """Whether an FSN's trailing semantic tag is present on the served
    value. `NOT_APPLICABLE` is for every designation type that is not an
    FSN at all - a preferred term or synonym never carries a semantic tag
    to strip, so there is no "intact" or "stripped" fact to report for one."""

    INTACT = "intact"
    STRIPPED = "stripped"
    NOT_APPLICABLE = "not_applicable"


class LabelProvenance(BaseModel):
    """One field's provenance declaration: which designation it is, and -
    for an FSN - whether the semantic tag is intact or stripped.

    Frozen, matching every other response model in this package
    (`nptc.api.routers.catalogue_shared`): provenance describes a field
    that has already been assembled, never something a caller mutates.
    """

    model_config = ConfigDict(frozen=True)

    designation: DesignationType
    semantic_tag: SemanticTagState


#: `au_preferred_term` never carries a semantic tag - it is a preferred
#: term, not an FSN - so its tag state is fixed, not configuration-driven.
AU_PREFERRED_TERM_PROVENANCE = LabelProvenance(
    designation=DesignationType.AU_PREFERRED_TERM,
    semantic_tag=SemanticTagState.NOT_APPLICABLE,
)

#: A catalogue-authored synonym (`Designation.use == "synonym"`). Not an FSN, so
#: `NOT_APPLICABLE`.
SYNONYM_PROVENANCE = LabelProvenance(
    designation=DesignationType.SYNONYM,
    semantic_tag=SemanticTagState.NOT_APPLICABLE,
)

#: A `designation` row with `use == "preferred"` - a preferred term in a
#: language other than en-AU (ADR-0022). Not an FSN, so `NOT_APPLICABLE`.
PREFERRED_VARIANT_PROVENANCE = LabelProvenance(
    designation=DesignationType.PREFERRED_VARIANT,
    semantic_tag=SemanticTagState.NOT_APPLICABLE,
)


def fsn_provenance(settings: ApiSettings) -> LabelProvenance:
    """The `LabelProvenance` for an `fsn` field, given the running
    process's configuration.

    Reads `settings.fsn_semantic_tag` rather than inspecting the FSN string
    (see the module docstring). `ApiSettings` refuses `"stripped"`, so only
    `"intact"` is reachable today. Reading the setting rather than hardcoding
    `SemanticTagState.INTACT` means FR-66's export configuration needs no
    change here.
    """
    return LabelProvenance(
        designation=DesignationType.FSN,
        semantic_tag=SemanticTagState(settings.fsn_semantic_tag),
    )
