"""Append-only audit log, hash chain, and field-level diffing.

`nptc.audit.hashing` builds the SHA-256 digest each `audit_event` row
carries (NFR-10); `nptc.audit.writer` is the only sanctioned way to append
a row (`append_audit_event`, NFR-08); `nptc.audit.verification` walks a
chain and reports the first break, if any (`verify_chain`). See
`docs/architecture/data-model.md` for the design and
`docs/adr/0017-audit-hash-chain.md` for the rejected alternatives.

`nptc.audit.serialisation` normalises a value into JSON-safe form, failing
loud on anything it doesn't recognise (issue #37) - the strict counterpart
to `hashing`'s own total normalisation, which must tolerate unfamiliar
content read back from Postgres. `nptc.audit.policy` declares which
columns of a mapped model may ever appear in a diff (allowlist + deny-list,
NFR-26). `nptc.audit.diffing` computes a `FieldDiff` from either a mapped
instance's own SQLAlchemy attribute history or a pair of snapshots.
`nptc.audit.recording` is the one entry point domain code calls
(`record_change`/`record_snapshot_change`), refusing an empty diff rather
than emitting nothing silently. See
`docs/adr/0018-field-level-audit-diffing.md` for the design and rejected
alternatives.

The operator-facing CLI that wraps `verify_chain` with stable exit codes
(`scripts/verify_audit_chain.py`) is issue #38's, not built here.
"""
