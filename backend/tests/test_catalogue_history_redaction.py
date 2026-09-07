"""`nptc.catalogue.history._changed_field_names` (issue #141, FR-19).

Unit-level, no database: no write path exercised in this issue's own
integration tests (`test_api_public_entry_history.py`) ever touches a
model with a real `__audit_withheld_fields__` entry, so the `REDACTED_KEY`
-unpacking branch would otherwise go untested. This module proves it
directly against a hand-built `before`/`after` payload, matching
`nptc.audit.diffing`'s own `REDACTED_KEY` shape (a list of names under
that key, alongside any auditable field's real value).
"""

from __future__ import annotations

from nptc.audit.diffing import REDACTED_KEY
from nptc.catalogue.history import _changed_field_names


def test_ordinary_changed_fields_are_named_directly() -> None:
    before = {"preferred_term": "Old term"}
    after = {"preferred_term": "New term"}

    assert _changed_field_names(before, after) == ("preferred_term",)


def test_redacted_key_is_unpacked_into_the_names_it_lists() -> None:
    before = {REDACTED_KEY: ["acknowledged_by_user_id"]}
    after = {REDACTED_KEY: ["acknowledged_by_user_id"]}

    assert _changed_field_names(before, after) == ("acknowledged_by_user_id",)


def test_ordinary_and_redacted_fields_combine_in_one_event() -> None:
    before = {"reason": "old reason", REDACTED_KEY: ["acknowledged_by_user_id"]}
    after = {"reason": "new reason", REDACTED_KEY: ["acknowledged_by_user_id"]}

    assert _changed_field_names(before, after) == ("acknowledged_by_user_id", "reason")


def test_none_before_is_a_created_event() -> None:
    assert _changed_field_names(None, {"preferred_term": "New term"}) == ("preferred_term",)


def test_none_after_is_a_deleted_event() -> None:
    assert _changed_field_names({"preferred_term": "Old term"}, None) == ("preferred_term",)


def test_both_none_yields_no_changed_fields() -> None:
    assert _changed_field_names(None, None) == ()
