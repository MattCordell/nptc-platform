"""`verify_chain`: walks `audit_event` in `sequence` order and confirms the NFR-10 hash chain
is intact.

`SELECT` only, so no write role is needed and it can run against a read-only replica. It
streams rows via `yield_per` rather than loading the table. `scripts/verify_audit_chain.py`
wraps it and also uses `head_hash` to detect tail truncation, which this walk cannot detect
alone (ADR-0017, hazard H-06).

It reports the **first** break and stops, because that is the location an operator needs.

It deliberately does **not** assert:

- **`sequence` contiguity.** A rolled-back transaction burns an identity value, so gaps are
  legitimate. A deleted row is caught by linkage: the successor's `prev_hash` no longer
  matches the previous surviving row's `entry_hash`.
- **`occurred_at` monotonicity.** `clock_timestamp()` can step backwards across a clock
  adjustment such as an NTP correction. That is operational, not tampering.

The first row's `prev_hash` must equal `GENESIS_HASH`. An empty table and a single-row chain
both verify `ok=True`.

**Known limit** (ADR-0017): an attacker holding table-owner credentials can recompute the
chain from the point of edit forward, because nothing is anchored outside the database. An
unanchored chain detects casual tampering, not a determined rewrite. Periodic off-box
publication of the head hash is the mitigation and is out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select

from nptc.audit.hashing import GENESIS_HASH, compute_entry_hash, digest_field_names
from nptc.db.models.audit import AuditEvent

#: Rows fetched per round trip to the database - large enough to amortise
#: the round-trip cost, small enough that a very large table is still
#: streamed rather than loaded wholesale.
_DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    #: Rows walked up to the break on failure, not the table total. Equals the total only when
    #: `ok` is True, so check `ok` first.
    record_count: int
    first_sequence: int | None
    #: On failure, the `sequence` of the last row walked (`first_broken_sequence`), not the last
    #: one in the table.
    last_sequence: int | None
    #: The `sequence` of the first row found broken, or None if `ok`.
    first_broken_sequence: int | None
    #: "prev_hash mismatch" | "entry_hash mismatch" | None if `ok`.
    break_reason: str | None
    #: The last `entry_hash` accepted before the walk stopped: the chain head when `ok`,
    #: otherwise the last hash confirmed before the break. `None` for an empty table. It comes
    #: from the same walk, not a second query, so it reflects exactly the rows this call
    #: examined. `scripts/verify_audit_chain.py` compares it with an operator-supplied value to
    #: catch tail truncation, which a forward walk from genesis cannot detect (ADR-0017,
    #: hazard H-06).
    head_hash: str | None


def verify_chain(
    connection: Connection, *, batch_size: int = _DEFAULT_BATCH_SIZE
) -> ChainVerification:
    """Walks `audit_event` in `sequence` order, recomputing each row's
    digest and confirming it links to the previous row's `entry_hash`."""
    table = AuditEvent.__table__
    field_names = digest_field_names(table) - {"prev_hash"}
    stmt = select(table).order_by(table.c.sequence)
    result = connection.execution_options(stream_results=True).execute(stmt)

    expected_prev_hash = GENESIS_HASH
    record_count = 0
    first_sequence: int | None = None
    last_sequence: int | None = None
    head_hash: str | None = None

    for row in result.yield_per(batch_size):
        mapping = row._mapping
        record_count += 1
        sequence = mapping["sequence"]
        if first_sequence is None:
            first_sequence = sequence
        last_sequence = sequence

        if mapping["prev_hash"] != expected_prev_hash:
            return ChainVerification(
                ok=False,
                record_count=record_count,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                first_broken_sequence=sequence,
                break_reason="prev_hash mismatch",
                head_hash=head_hash,
            )

        fields = {name: mapping[name] for name in field_names}
        recomputed = compute_entry_hash(fields, mapping["prev_hash"])
        if recomputed != mapping["entry_hash"]:
            return ChainVerification(
                ok=False,
                record_count=record_count,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                first_broken_sequence=sequence,
                break_reason="entry_hash mismatch",
                head_hash=head_hash,
            )

        expected_prev_hash = mapping["entry_hash"]
        head_hash = mapping["entry_hash"]

    return ChainVerification(
        ok=True,
        record_count=record_count,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        first_broken_sequence=None,
        break_reason=None,
        head_hash=head_hash,
    )
