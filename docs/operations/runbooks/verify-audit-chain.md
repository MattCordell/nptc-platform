# Audit chain verification CLI

`scripts/verify_audit_chain.py` walks the `audit_event` hash chain end to end and
reports the first broken link, so chain integrity can be checked on demand - or from a
scheduled check - rather than only inferred from application behaviour (NFR-12/NFR-13's
sibling requirements NFR-10 and NFR-38 test 5), delivered with backlog issue
[#38](https://github.com/MattCordell/nptc-platform/issues/38). It is a thin operator
wrapper around `nptc.audit.verification.verify_chain`, built with backlog issue
[#36](https://github.com/MattCordell/nptc-platform/issues/36)
([ADR-0017](../../adr/0017-audit-hash-chain.md)); no new verification logic lives here.

The command issues `SELECT`s only - it never needs the application's write role
(`nptc_app`'s INSERT/SELECT grant, NFR-09), so it can run against a read-only replica or
a restored backup without ever touching a primary.

## Usage

```powershell
uv run python scripts/verify_audit_chain.py
uv run python scripts/verify_audit_chain.py --database-url postgresql+psycopg://nptc_app_login:change-me@localhost:5432/nptc
uv run python scripts/verify_audit_chain.py --expected-head-hash <64-hex> --expected-record-count 42
```

| Flag | Default | Meaning |
|---|---|---|
| `--database-url` | *(none)* | DSN to connect with. Falls back to `NPTC_AUDIT_VERIFY_DATABASE_URL`, then `NPTC_DATABASE_URL`, if not given. Use this for a one-off run against a replica or a restored backup. |
| `--expected-head-hash` | *(none)* | 64-character lowercase hex `entry_hash`. Fails (exit `4`) if the verified chain's head differs - see "Tail truncation and anchoring" below. |
| `--expected-record-count` | *(none)* | Fails (exit `4`) if the verified row count differs. Can be used with or without `--expected-head-hash`. |
| `--batch-size` | `500` | Rows fetched per round trip to the database, passed straight through to `verify_chain`. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The chain is intact. Prints the record count, the `sequence` range verified, and the head `entry_hash`. An empty `audit_event` table exits `0` with a count of `0` - this is an explicit acceptance criterion, not an error. |
| `1` | A break was found. Prints the **first** broken `sequence`, the break reason (`prev_hash mismatch` or `entry_hash mismatch`), and how many rows were walked before it. Rows after the break are never re-confirmed as "also broken" - the first break is the location an operator needs. |
| `2` | Usage/configuration error: no DSN resolvable from `--database-url`, `NPTC_AUDIT_VERIFY_DATABASE_URL` or `NPTC_DATABASE_URL`; a malformed `--expected-head-hash` (not 64 lowercase hex characters) or `--expected-record-count` (not a non-negative integer). |
| `3` | Could not complete: the database was unreachable, or the connection succeeded but `audit_event` doesn't exist (an unmigrated database). An outage, not a finding about the chain itself. |
| `4` | The chain itself verified (`0` would otherwise apply), but the head `entry_hash` and/or record count didn't match what `--expected-head-hash`/`--expected-record-count` supplied. This is the tail-truncation signal - see below. |

These codes are stable and safe to depend on from a scheduled check.

## What a break means

A `1` exit names the exact `sequence` where the chain first diverges from its own
recorded hash - that row (or, for a `prev_hash mismatch`, the row immediately after a
deleted or reordered predecessor) is where to start investigating: pull `audit_event`
around that `sequence`, and cross-reference with database access logs for who could have
issued a direct `UPDATE`/`DELETE` against it (only the owner role can; `nptc_app` cannot -
NFR-09). `entry_hash mismatch` means that row's own content changed since it was written.
`prev_hash mismatch` means either that row's stored `prev_hash` was tampered with, or its
true predecessor was deleted outright, breaking the link at the successor.

## Tail truncation and anchoring

A forward walk from genesis cannot detect deleting the most *recent* rows off the end of
the chain: once the walk reaches the truncation point, there is nothing left to check a
break against, so the table still reports `ok` (see
[ADR-0017](../../adr/0017-audit-hash-chain.md)'s "Known limit" and hazard H-06 in
[the hazard log](../../governance/hazard-log.md)). This is a different, cheaper attack
than editing a row in the middle, and this command cannot catch it from the table alone.

`--expected-head-hash`/`--expected-record-count` close that gap only when an operator
actually supplies them: record the head hash and count a scheduled run prints on success,
store that expectation somewhere the database itself cannot reach (a separate monitoring
system, an off-box log), and pass it back on the next run. If a later run's chain still
verifies (exit would otherwise be `0`) but the head or count no longer matches what was
recorded, exit `4` reports it as a truncation candidate. Running the command with neither
flag - the common case today - leaves tail truncation exactly as undetectable as
`verify_chain` alone; there is no automatically-maintained off-box anchor yet.

## Not implemented yet

- No automated off-box anchor store: today an operator must record and supply
  `--expected-head-hash`/`--expected-record-count` themselves.
- No search/filter/export over the audit log (NFR-12) - this command only verifies chain
  integrity, it does not browse audit content.
