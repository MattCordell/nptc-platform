"""Tests for the FR-79/H-04 bounded edit-distance primitives.

Every distance-sensitive assertion below was computed by running the code,
not guessed - see the comment on each for the arithmetic.
"""

from __future__ import annotations

import pytest

from nptc_shared.similarity import (
    LONG_TOKEN_LENGTH,
    MAX_EDIT_DISTANCE,
    MIN_TOKEN_LENGTH,
    bounded_edit_distance,
    is_comparable_token,
    near_match_distance,
    token_key,
    tokenise,
)

NBSP = chr(0x00A0)  # non-breaking space


# -- bounded_edit_distance ----------------------------------------------------


@pytest.mark.req("FR-79")
def test_identical_strings_are_distance_zero() -> None:
    assert bounded_edit_distance("urine", "urine", max_distance=2) == 0


@pytest.mark.req("FR-79")
def test_a_single_substitution_is_distance_one() -> None:
    assert bounded_edit_distance("epinephrine", "epinephrina", max_distance=2) == 1


@pytest.mark.req("FR-79")
def test_a_single_insertion_is_distance_one() -> None:
    # "Epinephine" (typo, missing the 'r') -> "Epinephrine".
    assert bounded_edit_distance("epinephine", "epinephrine", max_distance=2) == 1


@pytest.mark.req("FR-79")
def test_a_distance_beyond_the_ceiling_returns_none() -> None:
    assert bounded_edit_distance("urine", "gravel", max_distance=2) is None


@pytest.mark.req("FR-79")
def test_a_length_gap_beyond_the_ceiling_short_circuits_without_computing() -> None:
    assert bounded_edit_distance("ab", "abcde", max_distance=1) is None


@pytest.mark.req("FR-79")
def test_an_adjacent_transposition_costs_two_not_one() -> None:
    """Plain Levenshtein, not Damerau-Levenshtein, by design: swapping two
    adjacent characters is a substitution each way (or a delete-then-insert),
    never a single edit. 'ab' vs 'ba' is the minimal case; embedding it in a
    longer, otherwise-identical word makes the point without it being an
    artefact of the strings being only two characters long."""
    assert bounded_edit_distance("ab", "ba", max_distance=2) == 2
    assert bounded_edit_distance("sodabicarb", "sodabicrab", max_distance=2) == 2


# -- near_match_distance -------------------------------------------------------


@pytest.mark.req("FR-79")
def test_distance_one_is_always_admissible_regardless_of_length() -> None:
    assert near_match_distance("epinephine", "epinephrine") == 1


@pytest.mark.req("FR-79")
def test_urine_and_urate_are_refused_at_distance_two_despite_length_five() -> None:
    """'urine' vs 'urate': u-r match, then i/a and n/t both substitute, e
    matches - distance 2 (verified via bounded_edit_distance directly).
    Both tokens are length 5, below LONG_TOKEN_LENGTH (8), so
    near_match_distance must refuse them even though the raw distance is
    within MAX_EDIT_DISTANCE."""
    assert bounded_edit_distance("urine", "urate", max_distance=2) == 2
    assert near_match_distance("urine", "urate") is None


@pytest.mark.req("FR-79")
def test_distance_two_is_admissible_once_the_shorter_token_reaches_the_long_threshold() -> None:
    short = "a" * (LONG_TOKEN_LENGTH - 1)
    long_ = short + "xx"  # two insertions past the shorter length
    assert len(short) < LONG_TOKEN_LENGTH
    assert near_match_distance(short, long_) is None  # shorter token still too short

    long_short = "a" * LONG_TOKEN_LENGTH
    long_long = long_short + "xx"
    assert near_match_distance(long_short, long_long) == 2


@pytest.mark.req("FR-79")
def test_near_match_distance_ceiling_is_max_edit_distance_by_default() -> None:
    assert MAX_EDIT_DISTANCE == 2


@pytest.mark.req("FR-79")
def test_near_match_distance_honours_a_caller_supplied_ceiling_below_one() -> None:
    """A caller-supplied ``max_distance`` is a real ceiling, not just a hint
    to the second (distance-2) probe - this module is shared with FR-36's
    on-save check, which may want a tighter ceiling than FR-79's own
    default. 'urinery' and 'urinary' are distance 1 apart, which
    ``max_distance=0`` must refuse."""
    assert bounded_edit_distance("urinery", "urinary", max_distance=1) == 1
    assert near_match_distance("urinery", "urinary", max_distance=0) is None
    assert near_match_distance("urinery", "urinary", max_distance=1) == 1


# -- tokenise: delimiter independence (FR-71, Annex A.4) ----------------------


@pytest.mark.req("FR-71")
@pytest.mark.req("FR-79")
def test_tokenise_is_independent_of_the_synonym_delimiter() -> None:
    """FR-71 leaves the RCPA Synonyms column's comma-vs-semicolon delimiter
    question unresolved (PRD Appendix A.4); tokenise sidesteps it entirely by
    treating every non-word character - comma, semicolon, or a bare space -
    as an equally valid separator."""
    comma = tokenise("ADA RBC, ADA red cells")
    semicolon = tokenise("ADA RBC; ADA red cells")
    bare_space = tokenise("ADA RBC ADA red cells")
    assert comma == semicolon == bare_space
    assert comma == ("ADA", "RBC", "ADA", "red", "cells")


@pytest.mark.req("FR-79")
def test_tokenise_splits_a_hyphenated_chemical_name_at_every_non_word_character() -> None:
    assert tokenise("3-methyl,4-hydroxymandelate") == ("3", "methyl", "4", "hydroxymandelate")


@pytest.mark.req("FR-79")
def test_tokenise_treats_an_interior_non_breaking_space_as_a_separator() -> None:
    assert tokenise(f"ADA{NBSP}RBC") == ("ADA", "RBC")


# -- is_comparable_token -------------------------------------------------------


@pytest.mark.req("FR-79")
@pytest.mark.parametrize(
    ("token", "comparable"),
    [
        ("ADA", False),  # too short
        ("AFP", False),  # too short
        ("ADA2", False),  # has a digit
        ("5HIAA", False),  # has a digit
        ("urine", True),
        ("Epinephine", True),
        ("ALPHAFETOPROTEIN", True),  # comparable/reference-eligible; see misspelling.py
        # for the separate, stricter suspect-only case restriction.
    ],
)
def test_is_comparable_token_truth_table(token: str, comparable: bool) -> None:
    assert is_comparable_token(token) is comparable


@pytest.mark.req("FR-79")
def test_min_token_length_constant() -> None:
    assert MIN_TOKEN_LENGTH == 5


# -- token_key ------------------------------------------------------------------


@pytest.mark.req("FR-79")
def test_token_key_is_a_casefolded_comparison_key() -> None:
    assert token_key("ADA") == token_key("ada") == "ada"
