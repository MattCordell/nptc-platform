#!/usr/bin/env python3
"""Operator CLI wrapping `nptc.audit.verification.verify_chain` (issue #38,
NFR-10, NFR-38 test 5): walks the `audit_event` hash chain end to end and
reports the first broken link, so chain integrity can be checked on demand -
or from a scheduled check - rather than only inferred from application
behaviour.

`verify_chain` itself is `SELECT`-only (see backend/src/nptc/audit/
verification.py), so this command never needs the application's write role
and can run against a read-only replica or a restored backup.

**Tail truncation.** A forward walk from genesis cannot detect deleting the
most recent rows off the end of the chain - the table still verifies `ok`
with nothing left to walk against (docs/adr/0017-audit-hash-chain.md's
"Known limit", hazard H-06). `--expected-head-hash`/`--expected-record-count`
close that gap when an operator supplies them (e.g. recorded from a
previous run's output and stored off-box); omitting both leaves truncation
undetected, same as `verify_chain` alone.

Usage:
  uv run python scripts/verify_audit_chain.py
  uv run python scripts/verify_audit_chain.py --database-url postgresql://...
  uv run python scripts/verify_audit_chain.py \\
      --expected-head-hash <hex> --expected-record-count <n>

See docs/operations/runbooks/verify-audit-chain.md for the full exit code
reference and what a break means.
"""

from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, ProgrammingError

#: Matches nptc.audit.hashing's own hex-digest shape (`ck_audit_event_*_hex`).
_HEAD_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: Exit codes - stable, documented in the runbook, safe to depend on from a
#: scheduled check.
EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_USAGE_ERROR = 2
EXIT_COULD_NOT_COMPLETE = 3
EXIT_ANCHOR_MISMATCH = 4


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "DSN to connect with, e.g. for a one-off run against a replica or a "
            "restored backup. Falls back to NPTC_AUDIT_VERIFY_DATABASE_URL, then "
            "NPTC_DATABASE_URL, if not given."
        ),
    )
    parser.add_argument(
        "--expected-head-hash",
        default=None,
        help="Fail (exit 4) if the verified chain's head entry_hash differs.",
    )
    parser.add_argument(
        "--expected-record-count",
        default=None,
        help="Fail (exit 4) if the verified row count differs.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows fetched per round trip to the database (default: 500).",
    )
    return parser.parse_args(argv)


def _resolve_database_url(cli_value: str | None) -> str | None:
    """`--database-url`, then `NPTC_AUDIT_VERIFY_DATABASE_URL`, then
    `NPTC_DATABASE_URL` - the precedence documented in the runbook. Imports
    of `nptc.settings` are deferred into this function (not at module level)
    so `--help` and argument-parsing failures never require the workspace's
    `nptc` package to even be importable."""
    if cli_value:
        return cli_value

    from nptc.settings import AuditVerifySettings, DatabaseSettings

    verify_url = AuditVerifySettings().audit_verify_database_url
    if verify_url:
        return verify_url

    try:
        return DatabaseSettings().database_url
    except Exception:
        return None


def _validate_expected_head_hash(value: str | None) -> str | None:
    if value is None:
        return None
    if not _HEAD_HASH_RE.match(value):
        raise ValueError(f"--expected-head-hash must be 64 lowercase hex characters, got {value!r}")
    return value


def _validate_expected_record_count(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        count = int(value)
    except ValueError as exc:
        raise ValueError(f"--expected-record-count must be an integer, got {value!r}") from exc
    if count < 0:
        raise ValueError(f"--expected-record-count must not be negative, got {count}")
    return count


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        expected_head_hash = _validate_expected_head_hash(args.expected_head_hash)
        expected_record_count = _validate_expected_record_count(args.expected_record_count)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    database_url = _resolve_database_url(args.database_url)
    if not database_url:
        print(
            "error: no database URL configured - pass --database-url, or set "
            "NPTC_AUDIT_VERIFY_DATABASE_URL or NPTC_DATABASE_URL",
            file=sys.stderr,
        )
        return EXIT_USAGE_ERROR

    # Deferred: keep --help/usage-error paths free of a hard nptc/SQLAlchemy
    # import requirement (see _resolve_database_url).
    from nptc.audit.verification import verify_chain

    try:
        engine = create_engine(database_url)
        with engine.connect() as connection:
            result = verify_chain(connection, batch_size=args.batch_size)
    except (OperationalError, ProgrammingError) as exc:
        print(f"error: could not verify the audit chain: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_COMPLETE

    if not result.ok:
        print(
            f"BROKEN at sequence {result.first_broken_sequence} "
            f"({result.break_reason}); {result.record_count} row(s) walked "
            f"before the break (first sequence verified: {result.first_sequence})."
        )
        return EXIT_BROKEN

    print(
        f"OK: {result.record_count} row(s) verified "
        f"(sequence {result.first_sequence}..{result.last_sequence}); "
        f"head entry_hash={result.head_hash}"
    )

    anchor_mismatch = False
    if expected_head_hash is not None and result.head_hash != expected_head_hash:
        print(
            f"ANCHOR MISMATCH: expected head entry_hash {expected_head_hash}, "
            f"got {result.head_hash} - possible tail truncation."
        )
        anchor_mismatch = True
    if expected_record_count is not None and result.record_count != expected_record_count:
        print(
            f"ANCHOR MISMATCH: expected {expected_record_count} row(s), "
            f"got {result.record_count} - possible tail truncation."
        )
        anchor_mismatch = True

    return EXIT_ANCHOR_MISMATCH if anchor_mismatch else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
