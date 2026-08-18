"""`verify_chain`: walks `audit_event` in `sequence` order and confirms the
NFR-10 hash chain is intact (issue #36).

`SELECT` only - no write role required, so this can run against a
read-only replica. Streams rows via `yield_per` rather than loading the
whole table, so it scales to a large table. The operator CLI wrapping this
- `scripts/verify_audit_chain.py` (issue #38) - also uses `head_hash` below
to detect tail truncation, a gap this walk cannot close on its own (see
docs/adr/0017-audit-hash-chain.md and hazard H-06).

It reports the **first** break and stops, since that is the location an
operator needs; a chain broken at row 5 does not need every subsequent row
re-confirmed as also "broken" once the first divergence is found.

Two properties this deliberately does **not** assert:

- **`sequence` contiguity.** A rolled-back transaction burns an identity
  value, so gaps in `sequence` are legitimate and expected, not a sign of
  tampering. Deletion of a row is instead caught by the linkage itself:
  the successor's `prev_hash` no longer matches the previous *surviving*
  row's `entry_hash`.
- **`occurred_at` monotonicity.** `clock_timestamp()` can step backwards
  across a clock adjustment (e.g. NTP correction); that is an operational
  fact, not evidence of tampering.

Genesis is well-defined: the first row's `prev_hash` must equal
`GENESIS_HASH`. An empty table and a single-row chain both verify
`ok=True` rather than raising - both are explicit acceptance criteria.

**Known limit** (see docs/adr/0017-audit-hash-chain.md): an attacker
holding table-owner credentials can recompute the entire chain from the
point of edit forward, since nothing here is anchored outside the
database itself. An unanchored chain detects casual tampering, not a
determined rewrite; periodic off-box publication of the head hash is the
mitigation, and is out of scope for this issue.
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
    #: On failure, this is rows *walked up to the break*, not the total
    #: row count in the table - verification stops at the first break, so
    #: rows after it are never counted. Only equal to the table's total
    #: row count when `ok` is True. Relevant to #38: don't read this as a
    #: total without checking `ok` first.
    record_count: int
    first_sequence: int | None
    #: On failure, the `sequence` of the last row walked before the break
    #: (i.e. `first_broken_sequence`), not the last `sequence` value in the
    #: table - same caveat as `record_count` above.
    last_sequence: int | None
    #: The `sequence` of the first row found broken, or None if `ok`.
    first_broken_sequence: int | None
    #: "prev_hash mismatch" | "entry_hash mismatch" | None if `ok`.
    break_reason: str | None
    #: The last row's `entry_hash` accepted before the walk stopped - the
    #: current chain head when `ok`, otherwise the last hash confirmed
    #: before the break. `None` for an empty table. Taken from the same
    #: walk rather than a second query, so it reflects exactly the rows
    #: this call examined rather than a possibly-different later snapshot.
    #: scripts/verify_audit_chain.py (#38) reports this and compares it
    #: against an operator-supplied expectation to catch tail truncation -
    #: see docs/adr/0017-audit-hash-chain.md's "Known limit" and hazard
    #: H-06 - which a forward walk from genesis cannot detect on its own.
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
