"""Tests for the Unicode text hygiene helpers (PRD Appendix A.1, A.3).

Every invisible character is built via ``chr(0x....)``, never written as a
literal character in this source file - a literal non-breaking space or BOM
in a ``.py`` file is exactly the kind of silent, invisible-on-screen defect
this module exists to catch, and would trip the repo's own trailing-
whitespace pre-commit hook.
"""

from __future__ import annotations

import pytest

from nptc_shared.text import (
    escape_invisible,
    find_invisible_characters,
    has_surrounding_whitespace,
    is_invisible,
    is_normalisable_space,
)

NBSP = chr(0x00A0)  # non-breaking space (Zs)
NNBSP = chr(0x202F)  # narrow no-break space (Zs)
IDEOGRAPHIC_SPACE = chr(0x3000)  # Zs
ZWSP = chr(0x200B)  # zero width space (Cf)
ZWNJ = chr(0x200C)  # zero width non-joiner (Cf)
BOM = chr(0xFEFF)  # Cf
SOFT_HYPHEN = chr(0x00AD)  # Cf
LINE_SEPARATOR = chr(0x2028)  # Zl
PARAGRAPH_SEPARATOR = chr(0x2029)  # Zp


@pytest.mark.parametrize(
    ("ch", "expected"),
    [
        (NBSP, True),
        (NNBSP, True),
        (IDEOGRAPHIC_SPACE, True),
        (ZWSP, True),
        (ZWNJ, True),
        (BOM, True),
        (SOFT_HYPHEN, True),
        ("\t", True),  # Cc, tab
        ("\r", True),  # Cc, carriage return
        ("\n", True),  # Cc, line feed
        (LINE_SEPARATOR, True),
        (PARAGRAPH_SEPARATOR, True),
        (" ", False),  # ASCII space is never a defect
        ("A", False),
        ("7", False),
    ],
)
def test_is_invisible_categorises_each_case(ch: str, expected: bool) -> None:
    """A line break is genuinely a control character and stays flagged here -
    a caller with column-specific knowledge that a break is legitimate
    formatting (e.g. cell_defects.py's free-text roles) is responsible for
    filtering that case out itself, not this shared, universal definition."""
    assert is_invisible(ch) is expected


def test_find_invisible_characters_reports_offset_codepoint_and_name() -> None:
    found = find_invisible_characters(f"Aciclovir level{NBSP}")
    assert len(found) == 1
    assert found[0].offset == 15
    assert found[0].codepoint == "U+00A0"
    assert found[0].name == "NO-BREAK SPACE"


def test_find_invisible_characters_finds_multiple_occurrences_in_order() -> None:
    # Row 16: two consecutive U+00A0.
    found = find_invisible_characters(f"term{NBSP}{NBSP}")
    assert [f.offset for f in found] == [4, 5]
    assert [f.codepoint for f in found] == ["U+00A0", "U+00A0"]

    # Row 38: U+202F followed by U+00A0.
    found = find_invisible_characters(f"term{NNBSP}{NBSP}")
    assert [f.codepoint for f in found] == ["U+202F", "U+00A0"]


def test_find_invisible_characters_on_clean_text_is_empty() -> None:
    assert find_invisible_characters("Aciclovir level") == ()


def test_escape_invisible_replaces_with_codepoint_and_never_reproduces_the_character() -> None:
    escaped = escape_invisible(f"Aciclovir level{NBSP}")
    assert escaped == "Aciclovir level<U+00A0>"
    assert NBSP not in escaped


def test_escape_invisible_is_a_no_op_on_clean_text() -> None:
    assert escape_invisible("Aciclovir level") == "Aciclovir level"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" 1,1,1-Trichloroethane measurement ", True),  # leading and trailing
        (" 3-Methyl,4-hydroxymandelate measurement", True),  # leading only
        ("clean measurement", False),
        ("", False),
    ],
)
def test_has_surrounding_whitespace(text: str, expected: bool) -> None:
    assert has_surrounding_whitespace(text) is expected


def test_has_surrounding_whitespace_is_true_for_a_trailing_non_breaking_space() -> None:
    # str.strip() also strips U+00A0, so this cell triggers both A.1 and A.3.
    assert has_surrounding_whitespace(f"term{NBSP}") is True


@pytest.mark.parametrize(
    ("ch", "expected"),
    [
        (NBSP, True),
        (NNBSP, True),
        (IDEOGRAPHIC_SPACE, True),
        (ZWSP, False),
        (ZWNJ, False),
        (BOM, False),
        (SOFT_HYPHEN, False),
        ("\n", False),
        (LINE_SEPARATOR, False),
        (PARAGRAPH_SEPARATOR, False),
        (" ", False),
        ("A", False),
    ],
)
def test_is_normalisable_space_only_true_for_non_ascii_zs(ch: str, expected: bool) -> None:
    """Only Zs collapses unambiguously to an ordinary space with no loss of
    meaning - every other invisible category has no single correct repair."""
    assert is_normalisable_space(ch) is expected


def test_find_invisible_characters_marks_normalisable_flag_per_character() -> None:
    found = find_invisible_characters(f"term{NBSP}{ZWSP}")
    assert [f.normalisable for f in found] == [True, False]
