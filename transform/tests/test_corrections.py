"""FR-71's three auto-correctable repairs, as functions (``corrections.py``,
issue #31, P0-9)."""

from __future__ import annotations

from nptc_transform.corrections import (
    apply_corrections,
    correct_code_cell,
    correct_invisible_characters,
    correct_surrounding_whitespace,
)

NBSP = chr(0x00A0)
NNBSP = chr(0x202F)


def test_correct_invisible_characters_collapses_nbsp_wherever_it_occurs() -> None:
    assert correct_invisible_characters(f"a{NBSP}b{NBSP}") == "a b "


def test_correct_invisible_characters_collapses_multiple_kinds() -> None:
    assert correct_invisible_characters(f"a{NNBSP}{NBSP}b") == "a  b"


def test_correct_invisible_characters_leaves_ordinary_text_untouched() -> None:
    assert correct_invisible_characters("clean text") == "clean text"


def test_correct_surrounding_whitespace_strips_both_edges() -> None:
    assert correct_surrounding_whitespace("  padded  ") == "padded"


def test_correct_surrounding_whitespace_is_a_no_op_on_clean_text() -> None:
    assert correct_surrounding_whitespace("clean") == "clean"


def test_correct_code_cell_returns_the_text_unchanged() -> None:
    """``Cell.text`` already renders a NUMBER-typed cell's digits exactly
    (FR-06) - this function names the repair, it does not transform the text."""
    assert correct_code_cell("1393151000168101") == "1393151000168101"


def test_apply_corrections_collapses_an_edge_nbsp_then_strips_it() -> None:
    """Order matters: collapsing to an ordinary space before stripping is
    what lets the strip remove an edge non-breaking space at all."""
    assert apply_corrections(f"{NBSP}Aciclovir level{NBSP}") == "Aciclovir level"


def test_apply_corrections_preserves_an_interior_nbsp_as_an_ordinary_space() -> None:
    assert apply_corrections(f"Term{NBSP}with{NBSP}space") == "Term with space"
