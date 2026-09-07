"""FR-98's label-provenance vocabulary (issue #144).

Every SNOMED/catalogue label field on the API states, in the payload
itself, which designation it is and - for an FSN - whether its semantic
tag is intact or stripped. This module is the one place that vocabulary is
defined; response models (`nptc.api.routers.catalogue_shared`,
`nptc.api.routers.terminology`) attach it to their own fields rather than
each restating what a "designation type" or a "semantic tag state" is.

**`PREFERRED_VARIANT`, not "rcpa_preferred_term", for a `designation` row
with `use == "preferred"`.** `docs/adr/0022-designation-storage.md` makes
the catalogue's own en-AU preferred term live only on
`catalogue_entry.preferred_term` - never on a `designation` row, enforced
by `ck_designation_no_en_au_preferred`. So a `designation` row that is
`preferred` is necessarily a preferred term in some language other than
en-AU (e.g. `use='preferred', language='mi-NZ'`), not the catalogue's own
preferred term - "preferred variant" names that correctly, where
"RCPA preferred term" would not.

**The read path contains no tag-stripping code**, so `SemanticTagState` is
config-driven rather than computed: `fsn_provenance` reads
`ApiSettings.fsn_semantic_tag` (FR-66's placeholder until that
configuration surface exists) and reports whatever it says, never
inspecting the FSN string itself. This is deliberately the only place that
reads the setting - `binding_from_row`/`ConceptLookup`'s assembly both call
`fsn_provenance`, so a caller can't observe two different opinions about
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

#: A catalogue-authored synonym (`Designation.use == "synonym"`) - never an
#: FSN, so `NOT_APPLICABLE` for the same reason `AU_PREFERRED_TERM_PROVENANCE`
#: is fixed rather than read from configuration.
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

    Reads `settings.fsn_semantic_tag` rather than inspecting the FSN
    string: the whole point of FR-98 on this read path is that nothing
    here re-derives a fact about the label from the label itself - see
    this module's own docstring. `ApiSettings` already refuses
    `fsn_semantic_tag="stripped"` at construction time (there being no
    stripper on the read path to make that value true), so the only value
    ever reachable here is `"intact"` - this still reads the setting
    rather than hardcoding `SemanticTagState.INTACT`, so a future FR-66
    configuration surface that legitimately varies this needs no change
    here.
    """
    return LabelProvenance(
        designation=DesignationType.FSN,
        semantic_tag=SemanticTagState(settings.fsn_semantic_tag),
    )
