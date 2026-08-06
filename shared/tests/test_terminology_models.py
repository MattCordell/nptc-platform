"""Tests for value-type behaviour in nptc_shared.terminology.models that
isn't already exercised incidentally by the contract/ontoserver/stub suites."""

from __future__ import annotations

from nptc_shared.terminology.models import ExpandedConcept, Expansion


def _concept(code: str) -> ExpandedConcept:
    return ExpandedConcept(code=code, system="http://snomed.info/sct")


def test_expansion_with_no_total_is_complete() -> None:
    assert Expansion(concepts=(_concept("122192001"),), total=None).is_complete is True


def test_expansion_is_complete_when_every_concept_was_returned() -> None:
    assert Expansion(concepts=(_concept("122192001"),), total=1).is_complete is True


def test_expansion_is_not_complete_when_total_exceeds_the_returned_page() -> None:
    """A server-side page-size ceiling can cap ``concepts`` below what
    ``total`` promises - a caller must be able to detect that, rather than
    a truncated page silently looking like a genuinely short result."""
    assert Expansion(concepts=(_concept("122192001"),), total=3).is_complete is False


def test_empty_expansion_with_zero_total_is_complete() -> None:
    assert Expansion(concepts=(), total=0).is_complete is True
