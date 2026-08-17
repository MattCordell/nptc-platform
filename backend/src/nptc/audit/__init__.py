"""Append-only audit log and hash chain.

`nptc.audit.hashing` builds the SHA-256 digest each `audit_event` row
carries (NFR-10); `nptc.audit.writer` is the only sanctioned way to append
a row (`append_audit_event`, NFR-08); `nptc.audit.verification` walks a
chain and reports the first break, if any (`verify_chain`). See
`docs/architecture/data-model.md` for the design and
`docs/adr/0017-audit-hash-chain.md` for the rejected alternatives.

The operator-facing CLI that wraps `verify_chain` with stable exit codes
(`scripts/verify_audit_chain.py`) is issue #38's, not built here.
"""
