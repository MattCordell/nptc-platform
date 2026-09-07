"""Offline unit tests for `nptc.api.labels` (issue #144, FR-98).

No container, no network - pure enum/model/helper plumbing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nptc.api.labels import (
    AU_PREFERRED_TERM_PROVENANCE,
    PREFERRED_VARIANT_PROVENANCE,
    SYNONYM_PROVENANCE,
    DesignationType,
    LabelProvenance,
    SemanticTagState,
    fsn_provenance,
)
from nptc.settings import ApiSettings


@pytest.mark.req("FR-98")
def test_designation_type_values_are_the_wire_strings() -> None:
    """These are load-bearing wire values, not just Python names - a
    rename here changes the JSON every consumer sees."""
    assert DesignationType.FSN == "fsn"
    assert DesignationType.AU_PREFERRED_TERM == "au_preferred_term"
    assert DesignationType.SYNONYM == "synonym"
    assert DesignationType.PREFERRED_VARIANT == "preferred_variant"


@pytest.mark.req("FR-98")
def test_semantic_tag_state_values_are_the_wire_strings() -> None:
    assert SemanticTagState.INTACT == "intact"
    assert SemanticTagState.STRIPPED == "stripped"
    assert SemanticTagState.NOT_APPLICABLE == "not_applicable"


@pytest.mark.req("FR-98")
def test_label_provenance_is_frozen() -> None:
    provenance = LabelProvenance(
        designation=DesignationType.FSN, semantic_tag=SemanticTagState.INTACT
    )
    with pytest.raises(ValidationError):
        provenance.semantic_tag = SemanticTagState.STRIPPED  # type: ignore[misc]


@pytest.mark.req("FR-98")
def test_fixed_provenance_constants_are_never_applicable_for_a_semantic_tag() -> None:
    """None of these three designation types is ever an FSN, so none of
    them has a "the tag is intact/stripped" fact to report."""
    for constant, expected_designation in (
        (AU_PREFERRED_TERM_PROVENANCE, DesignationType.AU_PREFERRED_TERM),
        (SYNONYM_PROVENANCE, DesignationType.SYNONYM),
        (PREFERRED_VARIANT_PROVENANCE, DesignationType.PREFERRED_VARIANT),
    ):
        assert constant.designation == expected_designation
        assert constant.semantic_tag == SemanticTagState.NOT_APPLICABLE


@pytest.mark.req("FR-98")
def test_fsn_provenance_reads_the_configured_semantic_tag_state() -> None:
    """The read path never inspects the FSN string itself - only the
    setting decides what `semantic_tag` reports."""
    provenance = fsn_provenance(ApiSettings())

    assert provenance.designation == DesignationType.FSN
    assert provenance.semantic_tag == SemanticTagState.INTACT
