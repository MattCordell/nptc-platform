"""The SHA-256 digest construction behind the audit_event hash chain (NFR-10).

Each row's ``entry_hash`` is a digest over its own content plus its
predecessor's ``entry_hash`` (``prev_hash``). An out-of-band ``UPDATE``,
``DELETE`` or re-order is therefore detectable, though a holder of table-owner
credentials cannot be prevented from making one. Design:
``docs/architecture/data-model.md``; rejected alternatives: ADR-0017.

**Canonical form.** A single JSON object,
``json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)``
encoded as UTF-8, the idiom ``transform/src/nptc_transform/report_writer.py``
uses. A JSON object is self-delimiting, so ``("ab", "c")`` cannot collide with
``("a", "bc")``.

**Field coverage.** Every ``audit_event`` column except
``EXCLUDED_DIGEST_COLUMNS``, and that includes ``prev_hash``.
``compute_entry_hash`` takes ``prev_hash`` as an argument only because the
caller reads it from the chain's tail before assembling the rest of the row.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Mapping
from typing import Final

from sqlalchemy.sql.selectable import FromClause

from nptc.audit.serialisation import UnserialisableAuditValueError, normalise_json_value

#: The first row's `prev_hash` - there is no predecessor to point to.
GENESIS_HASH: Final[str] = "0" * 64

#: Folded into every digest so a verifier can tell a row hashed under an old
#: scheme apart from a tampered one.
HASH_SCHEME: Final[int] = 1

#: `entry_hash` is the digest itself. `sequence` is unknowable before the
#: `INSERT`; the `prev_hash` links keep ordering tamper-evident without it
#: (ADR-0017).
EXCLUDED_DIGEST_COLUMNS: Final[frozenset[str]] = frozenset({"entry_hash", "sequence"})


def digest_field_names(table: FromClause) -> frozenset[str]:
    """The column names the digest covers for `table`.

    Derived from the table, never hand-maintained, so a new column is covered
    unless someone deliberately excludes it
    (`test_audit_hashing.py::test_digest_covers_every_meaningful_column`).
    """
    return frozenset(column.name for column in table.columns) - EXCLUDED_DIGEST_COLUMNS


def _normalise(value: object) -> object:
    """Normalise `value` into something `json.dumps` renders deterministically.

    Leaf typing is delegated to `nptc.audit.serialisation.normalise_json_value`.

    **Total, unlike `normalise_json_value`.** This also runs over rows read
    back from Postgres (the write-time self-check and `verify_chain`), where
    raising would turn a verifiable chain into an unverifiable one. For the
    same reason `Mapping` keys are coerced with `str(key)`, never validated.

    **Containers recurse here, not in `normalise_json_value`.** One
    unrecognised leaf then falls back to `str()` alone, instead of
    stringifying its siblings. A NaN or infinite `float` hashes as its
    `str()` for the same reason. See `docs/architecture/data-model.md`,
    "Strict vs. total normalisation".
    """
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalise(item) for item in value]
    try:
        return normalise_json_value(value)
    except UnserialisableAuditValueError, ValueError:
        return str(value)


def canonicalise_actor_ip(value: str) -> str:
    """Render `value` the way Postgres's `inet` type displays it, so the digest
    computed before `INSERT` matches the value re-read later.

    `inet` prints a bare address when the mask is the full address width
    (`/32` for IPv4, `/128` for IPv6). Without this, `"203.0.113.7/32"` would
    hash as submitted but re-read as `"203.0.113.7"`, and the write-time
    self-check in `nptc.audit.writer.append_audit_event` would raise
    `AuditChainWriteError`."""
    interface = ipaddress.ip_interface(value)
    host_mask = 32 if isinstance(interface, ipaddress.IPv4Interface) else 128
    if interface.network.prefixlen == host_mask:
        return str(interface.ip)
    return f"{interface.ip}/{interface.network.prefixlen}"


def canonical_payload(fields: Mapping[str, object]) -> bytes:
    """The exact bytes that get hashed: `fields` as one canonical JSON object."""
    normalised = {str(key): _normalise(value) for key, value in fields.items()}
    return json.dumps(normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_entry_hash(fields: Mapping[str, object], prev_hash: str) -> str:
    """The SHA-256 hex digest over `fields`, `prev_hash` and `HASH_SCHEME`.

    `fields` must already exclude `prev_hash` and every name in
    `EXCLUDED_DIGEST_COLUMNS`. It is nested under its own key so that a future
    column named `prev_hash` or `hash_scheme` cannot collide with the
    bookkeeping keys."""
    payload: dict[str, object] = {
        "fields": dict(fields),
        "prev_hash": prev_hash,
        "hash_scheme": HASH_SCHEME,
    }
    return hashlib.sha256(canonical_payload(payload)).hexdigest()
