"""Strict normalisation of a single value into JSON-safe form, for content
that is about to be written into `audit_event.before`/`after` (issue #37,
NFR-08, FR-06).

This is the **strict** counterpart to `nptc.audit.hashing._normalise`, which
must stay total (see that module's docstring for why). Every type handled
here normalises identically to `hashing._normalise` for any value that
module already accepted - this refactor moves no existing hash, proven by
`test_audit_hashing.py`'s golden-vector digest test.

**Why raise instead of stringify.** `audit_event` is INSERT/SELECT-only
(NFR-09): once a row is written it cannot be corrected. `hashing._normalise`
tolerates an unfamiliar type via `str(value)` because it must also run over
rows read back from Postgres, where raising would turn a verifiable chain
into an unverifiable one. A diff about to be written has no such excuse - an
unexpected object silently stringified into `before`/`after` is a permanent,
possibly-misleading audit record, so this module fails loudly instead.
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

#: Recursion depth beyond which `normalise_json_value` raises rather than
#: risk hitting Python's own recursion limit on a pathological structure -
#: a loud failure at the point of the write, not a stack overflow mid
#: transaction.
_MAX_DEPTH: Final[int] = 32

#: A JSON-safe value: what `json.dumps` can render without a custom encoder,
#: and what Postgres `jsonb` can store. A PEP 695 `type` statement, not a
#: `TypeAlias`-annotated assignment: it evaluates lazily, so the recursive
#: self-reference (`list[JsonValue]`/`dict[str, JsonValue]`) needs no
#: string-quoting.
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class UnserialisableAuditValueError(TypeError):
    """Raised when a value has no defined, lossless JSON representation for
    an audit `before`/`after` payload - an unrecognised type, a NaN/±Inf
    float, a `str` containing a NUL byte, a non-`str` mapping key, or a
    structure deeper than `_MAX_DEPTH`. Deliberately a `TypeError` subclass:
    the caller handed this function a value shape it does not support,
    which is a programming error at the call site, not a data-quality
    finding to report and continue past."""


def _normalise_str(value: str) -> str:
    if "\x00" in value:
        raise UnserialisableAuditValueError(
            "audit value contains a NUL byte (U+0000), which Postgres jsonb cannot "
            "store - FR-74's entry-time prohibition is the right place to reject "
            "this, not a silent escape that would make the audit record differ "
            "from what was written"
        )
    # str(value) rather than returning value unchanged: a str subclass (e.g.
    # StrEnum) must normalise to a genuine str, not merely something that
    # behaves like one - json.dumps would render it identically either way,
    # but a plain str is what a reader of the stored JSONB actually gets
    # back, and what compute_entry_hash's dict-key coercion already assumes.
    return str(value)


def normalise_json_value(value: object, *, _depth: int = 0) -> JsonValue:
    """Recursively normalises `value` into a JSON-safe form, raising
    `UnserialisableAuditValueError` rather than tolerating anything this
    module does not explicitly recognise.

    Order matters: `bool` is checked before `int` (it is a subclass of
    `int` in Python), and `str`/`StrEnum` before other `Enum` members
    (`StrEnum` is itself a `str` subclass).
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
        # A non-str Enum (str-subclass Enums, e.g. StrEnum, are already
        # caught by the `isinstance(value, str)` branch above) recurses on
        # its own `.value`, so e.g. an IntEnum normalises as its int.
        return normalise_json_value(value.value, _depth=_depth + 1)
    if isinstance(value, Decimal):
        # Never float: a Decimal's exact scale (e.g. "1.50") would be
        # silently lost or altered by a float round-trip.
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
