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
  uv run python scripts/verify_audit_chain.py --database-url postgresql+psycopg://...
  uv run python scripts/verify_audit_chain.py \\
      --expected-head-hash <hex> --expected-record-count <n>

See docs/operations/runbooks/verify-audit-chain.md for the full exit code
reference and what a break means.
"""

from __future__ import annotations

import argparse
import re
import sys

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
    `nptc` package to even be importable.

    Raises `ValueError` for an explicitly-empty `--database-url` (rather than
    silently falling through to the environment - an operator who typed
    `--database-url ""` almost certainly meant to pin a specific DSN) and
    re-raises a genuinely malformed `NPTC_DATABASE_URL` (a `pydantic`
    `ValidationError` whose cause isn't simply "the variable is unset") so
    that case is never conflated with "no DSN configured anywhere" - see
    `main`'s handling of both.
    """
    if cli_value is not None:
        if not cli_value:
            raise ValueError("--database-url must not be empty")
        return cli_value

    from pydantic import ValidationError

    from nptc.settings import AuditVerifySettings, DatabaseSettings

    verify_url = AuditVerifySettings().audit_verify_database_url
    if verify_url:
        return verify_url

    try:
        return DatabaseSettings().database_url
    except ValidationError as exc:
        if all(error["type"] == "missing" for error in exc.errors()):
            return None
        raise


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


def _validate_batch_size(value: int) -> int:
    if value < 1:
        raise ValueError(f"--batch-size must be a positive integer, got {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        expected_head_hash = _validate_expected_head_hash(args.expected_head_hash)
        expected_record_count = _validate_expected_record_count(args.expected_record_count)
        batch_size = _validate_batch_size(args.batch_size)
        database_url = _resolve_database_url(args.database_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    except Exception as exc:
        # Anything else here (e.g. `_resolve_database_url`'s deferred
        # `nptc.settings` import failing because the workspace isn't
        # importable - a plausible slip running this script outside
        # `uv run`/the venv) is an environment problem, not a usage mistake
        # or a chain break - see the block below and "Exit 1 means only a
        # break" in the runbook. Same NFR-26/NFR-35 rule as below: print
        # only the exception's type.
        print(
            f"error: could not verify the audit chain ({type(exc).__name__})",
            file=sys.stderr,
        )
        return EXIT_COULD_NOT_COMPLETE

    if not database_url:
        print(
            "error: no database URL configured - pass --database-url, or set "
            "NPTC_AUDIT_VERIFY_DATABASE_URL or NPTC_DATABASE_URL",
            file=sys.stderr,
        )
        return EXIT_USAGE_ERROR

    try:
        # Deferred (not module-level): keeps --help/usage-error paths free
        # of a hard SQLAlchemy/nptc import requirement. Any failure past
        # this point - an unimportable workspace, a missing DBAPI driver, a
        # malformed DSN, a dropped connection mid-walk - is "could not
        # complete", never "chain broken": only `result.ok` below is
        # allowed to report a break. The exception itself is never printed
        # (only its type name) - it can carry connection details
        # (host/user/dbname) that don't belong in operator-facing output
        # (NFR-26).
        from sqlalchemy import create_engine

        from nptc.audit.verification import verify_chain

        engine = create_engine(database_url)
        with engine.connect() as connection:
            result = verify_chain(connection, batch_size=batch_size)
    except Exception as exc:
        print(
            f"error: could not verify the audit chain ({type(exc).__name__})",
            file=sys.stderr,
        )
        return EXIT_COULD_NOT_COMPLETE

    if not result.ok:
        print(
            f"BROKEN at sequence {result.first_broken_sequence} "
            f"({result.break_reason}); {result.record_count} row(s) walked "
            f"before the break (first sequence verified: {result.first_sequence}).",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    print(
        f"OK: {result.record_count} row(s) verified "
        f"(sequence {result.first_sequence}..{result.last_sequence}); "
        f"head entry_hash={result.head_hash or '(none)'}"
    )

    anchor_mismatch = False
    if expected_head_hash is not None and result.head_hash != expected_head_hash:
        print(
            f"ANCHOR MISMATCH: expected head entry_hash {expected_head_hash}, "
            f"got {result.head_hash or '(none)'} - possible tail truncation.",
            file=sys.stderr,
        )
        anchor_mismatch = True
    if expected_record_count is not None and result.record_count != expected_record_count:
        print(
            f"ANCHOR MISMATCH: expected {expected_record_count} row(s), "
            f"got {result.record_count} - possible tail truncation.",
            file=sys.stderr,
        )
        anchor_mismatch = True

    return EXIT_ANCHOR_MISMATCH if anchor_mismatch else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # last line of defence - see main()'s own
        # try/except above for the primary handling: nothing between here
        # and process exit is allowed to become an *unhandled* exception,
        # since Python reports that via exit 1 - coincidentally identical
        # to EXIT_BROKEN and misleading a scheduled check into reporting a
        # false chain-tamper alert.
        print(f"error: could not verify the audit chain ({type(exc).__name__})", file=sys.stderr)
        sys.exit(EXIT_COULD_NOT_COMPLETE)
