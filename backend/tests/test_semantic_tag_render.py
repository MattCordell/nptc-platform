"""`nptc.exports.semantic_tag.render_display_term` tests (issue #48, FR-83,
NFR-38 tests 11 and 12).
"""

from __future__ import annotations

import pytest

from nptc.exports.semantic_tag import (
    EmptyDisplayTermError,
    NotAServedFSNError,
    render_display_term,
    trim_specimen_suffix,
)


@pytest.mark.req("FR-83")
def test_391483001_regression_fixture() -> None:
    """PRD SS6.4/NFR-38 test 11: the inner parenthesised phrase is
    retained - only the *final* group is a semantic tag."""
    assert (
        render_display_term("Microscopy (acid fast bacilli) (procedure)")
        == "Microscopy (acid fast bacilli)"
    )


@pytest.mark.req("FR-83")
def test_a_second_application_would_silently_over_strip() -> None:
    """The exact hazard PRD SS6.4 names: `once` still ends with its own
    parenthesised phrase (`"(acid fast bacilli)"`), which is
    indistinguishable from a semantic tag once stored - applying
    `render_display_term` to it a second time would not raise, it would
    silently produce `"Microscopy"`. This is why FR-83 makes the
    guarantee structural (exactly one call site, over a column that only
    ever holds an as-served FSN - see `test_catalogue_bindings.py`'s AST
    guard) rather than something this function could ever detect from its
    input alone."""
    once = render_display_term("Microscopy (acid fast bacilli) (procedure)")
    assert once == "Microscopy (acid fast bacilli)"

    twice = render_display_term(once)
    assert twice == "Microscopy"


@pytest.mark.req("FR-83")
def test_a_value_with_no_trailing_group_raises() -> None:
    """PRD SS6.4/NFR-38 test 12: a value that is not a served FSN must
    fail loudly at export, never publish silently."""
    with pytest.raises(NotAServedFSNError):
        render_display_term("Full blood count")


@pytest.mark.req("FR-83")
def test_result_is_never_empty() -> None:
    assert render_display_term("Procedure (procedure)") == "Procedure"


@pytest.mark.req("FR-83")
def test_a_value_that_is_only_its_own_tag_raises() -> None:
    """PRD SS6.4/NFR-38 test 12's second half: stripping `"(procedure)"`
    would leave nothing at all - the second defensive assertion, exercised
    on its actual failure path rather than only the happy path above."""
    with pytest.raises(EmptyDisplayTermError):
        render_display_term("(procedure)")


@pytest.mark.req("FR-83")
@pytest.mark.parametrize(
    ("term", "trimmed"),
    [
        ("Serum specimen", "Serum"),
        ("Urine specimen", "Urine"),
        ("serum SPECIMEN", "serum"),
        ("Cerebrospinal fluid specimen", "Cerebrospinal fluid"),
        ("Serum", "Serum"),
        ("Specimen from skin", "Specimen from skin"),
        ("Specimen", "Specimen"),
        ("specimen", "specimen"),
        ("Nonspecimen", "Nonspecimen"),
        ("309051001", "309051001"),
    ],
)
def test_a_trailing_specimen_word_is_trimmed_and_nothing_else(term: str, trimmed: str) -> None:
    """The bare word is the root concept's own term; trimming it would leave an empty
    label, so it stays. A word that only ends in the letters is not a trailing word."""
    assert trim_specimen_suffix(term) == trimmed
