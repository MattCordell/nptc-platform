"""`UserRef` NFR-04 boundary tests (issue #42) - pure unit, no Docker.

Includes a positive control (`test_the_guard_detects_a_deliberately_leaking_payload`)
so the leak check itself can't quietly rot into an always-pass assertion -
the same convention `test_sql_parameterisation.py::test_guard_flags_known_violations`
already sets for its own guard.
"""

from __future__ import annotations

import json
import uuid

import pytest

from nptc.auth.identity import UserRef
from nptc.db.models.user import User


def _leaks_uuid(payload: dict[str, object], leaked: uuid.UUID) -> bool:
    return str(leaked) in json.dumps(payload, default=str)


@pytest.mark.req("NFR-04")
def test_user_ref_has_no_identifier_field() -> None:
    assert "id" not in UserRef.model_fields


@pytest.mark.req("NFR-04")
def test_user_ref_serialisation_contains_no_internal_uuid() -> None:
    user = User(
        id=uuid.uuid4(),
        username="alice",
        display_name="Alice",
        organisation=None,
        status="active",
    )

    ref = UserRef.from_user(user)

    assert not _leaks_uuid(ref.model_dump(), user.id)


def test_the_guard_detects_a_deliberately_leaking_payload() -> None:
    """A `_leaks_uuid` that always returned `False` would let the test above
    pass regardless of what `UserRef` actually serialises - this proves the
    check fires on a payload that genuinely leaks the UUID."""
    leaked_id = uuid.uuid4()
    bad_payload = {"username": "alice", "internal_ref": str(leaked_id)}

    assert _leaks_uuid(bad_payload, leaked_id)
