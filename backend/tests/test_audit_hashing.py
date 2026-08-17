"""Offline unit tests for nptc.audit.hashing (issue #36, NFR-10).

No container, no network - pure digest construction. See
test_audit_chain.py/test_audit_tamper_detection.py for the integration
tests that exercise the digest against a real database.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta, timezone

from nptc.audit.hashing import (
    EXCLUDED_DIGEST_COLUMNS,
    GENESIS_HASH,
    canonical_payload,
    compute_entry_hash,
    digest_field_names,
)
from nptc.db.models.audit import AuditEvent

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_SAMPLE_PREV_HASH = "1" * 64


def _sample_fields() -> dict[str, object]:
    return {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "occurred_at": datetime(2026, 8, 17, 12, 30, 0, 123456, tzinfo=UTC),
        "actor_user_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "actor_ip": "203.0.113.7",
        "user_agent": "pytest",
        "correlation_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "action": "user.closed",
        "entity_type": "app_user",
        "entity_id": "22222222-2222-2222-2222-222222222222",
        "before": {"username": "erin"},
        "after": {"username": None},
        "reason": None,
    }


def test_genesis_hash_shape() -> None:
    assert GENESIS_HASH == "0" * 64
    assert _HEX64_RE.match(GENESIS_HASH)


def test_compute_entry_hash_is_lowercase_hex_64() -> None:
    digest = compute_entry_hash(_sample_fields(), _SAMPLE_PREV_HASH)

    assert _HEX64_RE.match(digest)


def test_compute_entry_hash_is_deterministic() -> None:
    fields = _sample_fields()

    first = compute_entry_hash(fields, _SAMPLE_PREV_HASH)
    second = compute_entry_hash(fields, _SAMPLE_PREV_HASH)

    assert first == second


def test_compute_entry_hash_is_stable_across_dict_insertion_order() -> None:
    fields = _sample_fields()
    reordered = dict(reversed(list(fields.items())))

    assert compute_entry_hash(fields, _SAMPLE_PREV_HASH) == compute_entry_hash(
        reordered, _SAMPLE_PREV_HASH
    )


def test_compute_entry_hash_changes_with_prev_hash() -> None:
    fields = _sample_fields()
    other_prev_hash = "2" * 64

    assert compute_entry_hash(fields, _SAMPLE_PREV_HASH) != compute_entry_hash(
        fields, other_prev_hash
    )


def test_a_single_differing_character_changes_the_digest() -> None:
    """Positive control: two payloads differing in one character produce
    different digests."""
    fields = _sample_fields()
    changed = dict(fields)
    changed["reason"] = "x"

    assert compute_entry_hash(fields, _SAMPLE_PREV_HASH) != compute_entry_hash(
        changed, _SAMPLE_PREV_HASH
    )


def test_datetime_normalisation_is_timezone_independent() -> None:
    """The same instant, expressed in two different timezones, must
    produce the same digest - the normalisation step converts to UTC
    before rendering."""
    fields = _sample_fields()
    utc_time = fields["occurred_at"]
    assert isinstance(utc_time, datetime)
    other_tz_time = utc_time.astimezone(timezone(timedelta(hours=10)))

    other_fields = dict(fields)
    other_fields["occurred_at"] = other_tz_time

    assert compute_entry_hash(fields, _SAMPLE_PREV_HASH) == compute_entry_hash(
        other_fields, _SAMPLE_PREV_HASH
    )


def test_canonical_payload_is_deterministic_json_bytes() -> None:
    fields = {"b": 1, "a": 2}
    reordered = {"a": 2, "b": 1}

    assert canonical_payload(fields) == canonical_payload(reordered)
    assert canonical_payload(fields) == b'{"a":2,"b":1}'


def test_digest_covers_every_meaningful_column() -> None:
    """The exclusion set is explicit and tested, not implied: the digest's
    field set is derived from AuditEvent.__table__.columns minus exactly
    {entry_hash, sequence}, so a column added by a later issue is either
    automatically covered or must be deliberately added to
    EXCLUDED_DIGEST_COLUMNS - either way, not silently left out by
    omission from a hand-maintained list."""
    assert frozenset({"entry_hash", "sequence"}) == EXCLUDED_DIGEST_COLUMNS

    expected = {column.name for column in AuditEvent.__table__.columns} - EXCLUDED_DIGEST_COLUMNS
    assert digest_field_names(AuditEvent.__table__) == expected
    # prev_hash is itself digest-covered - only excluded from the `fields`
    # argument to compute_entry_hash because the writer passes it as a
    # separate parameter, not because it is outside the digest.
    assert "prev_hash" in expected
