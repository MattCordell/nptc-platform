"""Tests for the FR-75 specimen table (issue #29, P0-7).

Structural coverage only - the table's own content (which groups exist, which
hand-typed terms each carries) is exercised through ``semantic_drift.py``'s
own tests, not duplicated here.
"""

from __future__ import annotations

from nptc_shared.sctid import has_valid_check_digit
from nptc_transform.specimen_table import SPECIMEN_TABLE, all_specimen_codes


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
