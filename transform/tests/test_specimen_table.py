"""Tests for the FR-75 specimen table (issue #29, P0-7).

Structural coverage only - the table's own content (which groups exist, which
hand-typed terms each carries) is exercised through ``semantic_drift.py``'s
own tests, not duplicated here.
"""

from __future__ import annotations

import pytest

from nptc_shared.sctid import has_valid_check_digit
from nptc_transform.specimen_map import SPECIMEN_MAP
from nptc_transform.specimen_table import (
    _OUTSIDE_THE_MAP,
    SPECIMEN_TABLE,
    _group,
    _Wording,
    all_specimen_codes,
)


def test_every_specimen_code_is_a_verhoeff_valid_sctid() -> None:
    for group in SPECIMEN_TABLE:
        assert has_valid_check_digit(group.specimen_code), (
            f"{group.key!r}'s specimen_code {group.specimen_code!r} fails the Verhoeff check"
        )


def test_every_group_key_is_unique() -> None:
    keys = [group.key for group in SPECIMEN_TABLE]
    assert len(keys) == len(set(keys))


def test_every_group_has_at_least_one_hand_typed_term() -> None:
    for group in SPECIMEN_TABLE:
        assert group.terms, f"{group.key!r} has no hand-typed surface forms"


def test_all_specimen_codes_is_deduplicated_and_sorted() -> None:
    codes = all_specimen_codes(SPECIMEN_TABLE)
    assert codes == tuple(sorted(codes))
    assert len(codes) == len(set(codes))
    assert set(codes) == {group.specimen_code for group in SPECIMEN_TABLE}


def test_urine_24h_is_its_own_group_distinct_from_plain_urine() -> None:
    """``276833005`` is a descendant of ``122575003`` (verified live), but is
    kept as a distinct group with its own ``timing`` - see the module
    docstring for why folding it into ``urine`` would lose the timing
    assertion a 24-hour-urine term needs checked in addition to the plain
    specimen check."""
    by_key = {group.key: group for group in SPECIMEN_TABLE}
    assert by_key["urine"].timing is None
    assert by_key["urine_24h"].timing == "24 h"
    assert by_key["urine"].specimen_code != by_key["urine_24h"].specimen_code


_DECLARED_KEYS = [
    "urine",
    "csf",
    "faeces",
    "serum",
    "plasma",
    "whole_blood",
    "saliva",
    "pleural_fluid",
    "synovial_fluid",
    "sputum",
    "swab",
    "tissue",
    "bone_marrow",
    "breast_milk",
    "semen",
    "urine_24h",
]


@pytest.mark.req("FR-75")
def test_the_table_keeps_its_groups_in_declaration_order() -> None:
    """Order is the tie-break ``semantic_drift`` uses when two groups match equally."""
    assert [group.key for group in SPECIMEN_TABLE] == _DECLARED_KEYS


@pytest.mark.req("FR-88")
def test_every_group_with_a_map_row_takes_its_code_and_display_from_the_map() -> None:
    for group in SPECIMEN_TABLE:
        if group.key in _OUTSIDE_THE_MAP:
            continue
        assert group.specimen_code in SPECIMEN_MAP.codes, group.key
        assert any(
            entry.code == group.specimen_code and entry.display == group.specimen_display
            for entry in SPECIMEN_MAP.entries
        ), group.key


@pytest.mark.req("FR-88")
def test_a_group_outside_the_map_leaves_that_list_once_the_map_has_a_row_for_it() -> None:
    """Whole blood and breast milk have no row in the reviewed map today. When the map
    gains one, this fails, and the group should draw its code from the map instead."""
    for key in _OUTSIDE_THE_MAP:
        code, _display = _OUTSIDE_THE_MAP[key]
        assert code not in SPECIMEN_MAP.codes, key


@pytest.mark.req("FR-88")
@pytest.mark.parametrize("map_string", ["No such string", "N/A"])
def test_a_group_naming_a_string_the_map_does_not_code_is_refused(map_string: str) -> None:
    with pytest.raises(ValueError, match="does not code"):
        _group(_Wording("invented", ("invented",), map_string))
