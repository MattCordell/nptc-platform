"""Tests for the ``CellRef`` value object (FR-72)."""

from __future__ import annotations

import pytest

from nptc_transform.cellref import CellRef


def test_str_renders_the_a1_style_reference() -> None:
    assert str(CellRef("Odd!Sheet", "B", 12)) == "Odd!Sheet!B12"


@pytest.mark.req("FR-72")
def test_sort_key_orders_rows_numerically_not_lexicographically() -> None:
    """The principal failure mode this type exists to fix: a plain string
    sort would put ``B10`` before ``B2`` (lexicographic), and this must not."""
    b2 = CellRef("Sheet", "B", 2)
    b10 = CellRef("Sheet", "B", 10)
    assert b2.sort_key() < b10.sort_key()


@pytest.mark.req("FR-72")
def test_sort_key_orders_columns_numerically_not_lexicographically() -> None:
    """The reason ``order=True`` is deliberately unused: field-order (and
    plain string) comparison would put ``AA1`` before ``B1``, because 'A' <
    'B' lexicographically even though column AA (27) comes after column B
    (2)."""
    b1 = CellRef("Sheet", "B", 1)
    aa1 = CellRef("Sheet", "AA", 1)
    assert b1.sort_key() < aa1.sort_key()


def test_equal_refs_are_equal_and_hash_identically() -> None:
    """Frozen + eq is load-bearing for ``checkable_locations`` sets/dicts."""
    a = CellRef("Sheet", "B", 2)
    b = CellRef("Sheet", "B", 2)
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_different_refs_are_not_equal() -> None:
    assert CellRef("Sheet", "B", 2) != CellRef("Sheet", "B", 3)
    assert CellRef("Sheet", "B", 2) != CellRef("Sheet", "C", 2)
    assert CellRef("Sheet", "B", 2) != CellRef("Other", "B", 2)


def test_a_cellref_never_equals_a_plain_string() -> None:
    """Guards Risk 2 from the P0-8 plan: a ``CellRef`` must never compare
    equal to the opaque string it replaced, or a stale ``== "Sheet!B2"``
    comparison left over from before this migration would silently pass
    while asserting nothing."""
    assert CellRef("Sheet", "B", 2) != "Sheet!B2"


@pytest.mark.parametrize(
    ("sheet", "column_letter", "row"),
    [
        ("", "B", 2),
        ("Sheet", "b", 2),
        ("Sheet", "B2", 2),
        ("Sheet", "B", 0),
        ("Sheet", "B", -1),
    ],
)
def test_validation_rejects_an_unresolvable_reference(
    sheet: str, column_letter: str, row: int
) -> None:
    with pytest.raises(ValueError):
        CellRef(sheet, column_letter, row)
