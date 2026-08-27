#!/usr/bin/env python3
"""Operator CLI wrapping `nptc.db.property_reconciler.reconcile_property_indexes`
(issue #54, FR-13): converges the `property_value` indexes actual `pg_index`
state carries against every `property_definition` row's own desired index -
building the missing, dropping the orphaned, and repairing anything a failed
`CREATE INDEX CONCURRENTLY` left `indisvalid = false`.

`reconcile_property_indexes()` needs a schema-owning credential (`CREATE
INDEX`), never the API's own least-privilege `nptc_app` role - see
`nptc.settings.IndexerSettings`'s own docstring for why that credential is
never the API's `NPTC_DATABASE_URL` or the migration role's
`NPTC_MIGRATION_DATABASE_URL`. This CLI is the operator-facing "converge
now" path for a deployment that leaves `NPTC_INDEXER_DATABASE_URL` unset in
the API process itself (see that settings class): run it by hand, or from a
scheduled check, after a property's `filterable` flag changes.

Usage:
  uv run python scripts/reconcile_property_indexes.py
  uv run python scripts/reconcile_property_indexes.py --database-url postgresql+psycopg://...
  uv run python scripts/reconcile_property_indexes.py --dry-run

See docs/operations/runbooks/reconcile-property-indexes.md for the full
exit code reference.
"""

from __future__ import annotations

import argparse
import sys

#: Exit codes - stable, safe to depend on from a scheduled check.
#: 0 = converged (or --dry-run found nothing to do); 1 = --dry-run found
#: drift to report (nothing was executed); 2 = usage error (bad arguments,
#: no DSN configured); 3 = could not complete (connection/import failure, or
#: - on a real, non-dry-run run - one or more individual indexes failed to
#: converge; see the FAILED lines). A real run that converges everything it
#: attempted always returns 0 - "drift was found and fixed" is success, not
#: a failure code, since fixing it is exactly what the run is for.
EXIT_OK = 0
EXIT_DRIFT_REMAINS = 1
EXIT_USAGE_ERROR = 2
EXIT_COULD_NOT_COMPLETE = 3


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "DSN to reconcile with. Must be a role that can CREATE/DROP INDEX on "
            "property_value. Falls back to NPTC_INDEXER_DATABASE_URL if not given."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without executing any DDL.",
    )
    return parser.parse_args(argv)


def _resolve_database_url(cli_value: str | None) -> str | None:
    """`--database-url`, then `NPTC_INDEXER_DATABASE_URL`. Imports of
    `nptc.settings` are deferred into this function (not at module level) so
    `--help` and argument-parsing failures never require the workspace's
    `nptc` package to even be importable - the same posture
    `scripts/verify_audit_chain.py::_resolve_database_url` takes.

    Raises `ValueError` for an explicitly-empty `--database-url` (an
    operator who typed `--database-url ""` almost certainly meant to pin a
    specific DSN, not fall through to the environment)."""
    if cli_value is not None:
        if not cli_value:
            raise ValueError("--database-url must not be empty")
        return cli_value

    from nptc.settings import IndexerSettings

    return IndexerSettings().indexer_database_url or None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        database_url = _resolve_database_url(args.database_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    if not database_url:
        print(
            "error: no database URL configured - pass --database-url, or set "
            "NPTC_INDEXER_DATABASE_URL",
            file=sys.stderr,
        )
        return EXIT_USAGE_ERROR

    try:
        # Deferred (not module-level): keeps --help/usage-error paths free of
        # a hard SQLAlchemy/nptc import requirement. Any failure past this
        # point is "could not complete", not "drift found" - only the
        # returned report's own fields are allowed to report drift. The
        # exception itself is never printed (only its type name): it can
        # carry connection details (host/user/dbname) that don't belong in
        # operator-facing output (NFR-26).
        #
        # `database_url` is passed straight through to
        # `reconcile_property_indexes`, not written into `os.environ` (issue
        # #54 review) - the DSN resolved above can carry a DDL-capable
        # credential, and every subprocess this process spawns would
        # otherwise inherit it purely to smuggle it past `IndexerSettings`.
        from nptc.db import property_reconciler

        report = property_reconciler.reconcile_property_indexes(
            dry_run=args.dry_run, database_url=database_url
        )
    except Exception as exc:
        print(
            f"error: could not reconcile property indexes ({type(exc).__name__})",
            file=sys.stderr,
        )
        return EXIT_COULD_NOT_COMPLETE

    if report.skipped_locked:
        print("SKIPPED: another reconciliation is already in progress")
        return EXIT_OK

    verb = "WOULD CREATE" if args.dry_run else "CREATED"
    for name in report.created:
        print(f"{verb}: {name}")
    verb = "WOULD DROP" if args.dry_run else "DROPPED"
    for name in report.dropped:
        print(f"{verb}: {name}")
    verb = "WOULD REBUILD (invalid)" if args.dry_run else "REBUILT (was invalid)"
    for name in report.repaired_invalid:
        print(f"{verb}: {name}")
    verb = "WOULD REBUILD (definition changed)" if args.dry_run else "REBUILT (definition changed)"
    for name in report.rebuilt_stale_definition:
        print(f"{verb}: {name}")
    verb = "WOULD REPAIR COMMENT" if args.dry_run else "REPAIRED COMMENT"
    for name in report.repaired_comment:
        print(f"{verb}: {name}")
    for name, exception_type in report.failed:
        print(f"FAILED: {name} ({exception_type})")

    if not report.changed and not report.repaired_comment:
        print("OK: no drift - every filterable property's index is already converged")
        return EXIT_OK

    if report.failed and not args.dry_run:
        return EXIT_COULD_NOT_COMPLETE

    return EXIT_DRIFT_REMAINS if args.dry_run else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # last line of defence - see main()'s own
        # try/except above for the primary handling: nothing between here
        # and process exit is allowed to become an *unhandled* exception.
        print(
            f"error: could not reconcile property indexes ({type(exc).__name__})", file=sys.stderr
        )
        sys.exit(EXIT_COULD_NOT_COMPLETE)
