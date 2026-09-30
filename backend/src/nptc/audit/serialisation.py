"""Strict normalisation of one value into JSON-safe form, for content about to be written into
`audit_event.before`/`after` (NFR-08, FR-06).

The strict counterpart to `nptc.audit.hashing._normalise`, which must stay total. Every type
handled here normalises identically to `_normalise` for any value `_normalise` already
accepted, so no existing hash moves (`test_audit_hashing.py`'s golden-vector test).

**Raise, do not stringify.** `audit_event` is INSERT/SELECT-only (NFR-09), so a row cannot be
corrected. `_normalise` tolerates an unfamiliar type via `str(value)` because it also runs over
rows read back from Postgres, where raising would make a verifiable chain unverifiable. A diff
about to be written has no such excuse: a silently stringified object is a permanent, possibly
misleading record. ADR-0018 records the split.
"""

from __future__ import annotations

import ipaddress
import math
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Final

from nptc_shared.sctid import SCTID

#: Recursion depth past which `normalise_json_value` raises, a loud failure at the write rather
#: than a stack overflow mid-transaction.
_MAX_DEPTH: Final[int] = 32

#: A JSON-safe value: what `json.dumps` renders without a custom encoder and Postgres `jsonb`
#: stores. A PEP 695 `type` statement evaluates lazily, so the recursive reference needs no
#: string-quoting.
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class UnserialisableAuditValueError(TypeError):
    """Raised when a value has no defined, lossless JSON form for an audit payload: an
    unrecognised type, a NaN or infinite float, a `str` with a NUL byte, a non-`str` mapping
    key, or nesting deeper than `_MAX_DEPTH`. A `TypeError` because the caller passed an
    unsupported shape, a programming error and not a data-quality finding."""


def _normalise_str(value: str) -> str:
    if "\x00" in value:
        raise UnserialisableAuditValueError(
            "audit value contains a NUL byte (U+0000), which Postgres jsonb cannot "
            "store - FR-74's entry-time prohibition is the right place to reject "
            "this, not a silent escape that would make the audit record differ "
            "from what was written"
        )
    # `str(value)`, not `value`: a `str` subclass such as `StrEnum` must become a genuine `str`,
    # which is what a reader of the stored JSONB gets and what `compute_entry_hash`'s dict-key
    # coercion assumes.
    return str(value)


def normalise_json_value(value: object, *, _depth: int = 0) -> JsonValue:
    """Recursively normalises `value` into JSON-safe form, raising
    `UnserialisableAuditValueError` for anything not explicitly recognised.

    Order matters: `bool` before `int` (a subclass), and `str` (including `StrEnum`) before
    other `Enum` members.
    """
    if _depth > _MAX_DEPTH:
        raise UnserialisableAuditValueError(
            f"audit value nested deeper than {_MAX_DEPTH} levels - refusing rather "
            "than risking the recursion limit mid-transaction"
        )

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise UnserialisableAuditValueError(
                f"audit value {value!r} is not valid JSON and jsonb cannot store it"
            )
        return value
    if isinstance(value, str):
        return _normalise_str(value)
    if isinstance(value, Enum):
        # A non-`str` Enum recurses on its `.value`, so an `IntEnum` becomes its int. `StrEnum`
        # was already caught by the `str` branch.
        return normalise_json_value(value.value, _depth=_depth + 1)
    if isinstance(value, Decimal):
        # Never float: it would lose a Decimal's exact scale ("1.50").
        return _normalise_str(str(value))
    if isinstance(value, SCTID):
        return _normalise_str(value.value)
    if isinstance(value, uuid.UUID):
        return _normalise_str(str(value))
    if isinstance(
        value,
        ipaddress.IPv4Address
        | ipaddress.IPv6Address
        | ipaddress.IPv4Network
        | ipaddress.IPv6Network
        | ipaddress.IPv4Interface
        | ipaddress.IPv6Interface,
    ):
        return _normalise_str(str(value))
    if isinstance(value, datetime):
        return _normalise_str(value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    if isinstance(value, date | time):
        return _normalise_str(value.isoformat())
    if isinstance(value, Mapping):
        normalised: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnserialisableAuditValueError(
                    f"audit mapping key {key!r} is not a str - a JSON object key must be textual"
                )
            normalised[key] = normalise_json_value(item, _depth=_depth + 1)
        return normalised
    if isinstance(value, list | tuple):
        return [normalise_json_value(item, _depth=_depth + 1) for item in value]

    raise UnserialisableAuditValueError(
        f"audit value of type {type(value).__name__} has no defined JSON "
        "representation - add explicit handling to normalise_json_value rather "
        "than let it fall through to an implicit str()"
    )
