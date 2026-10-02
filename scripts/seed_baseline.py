#!/usr/bin/env python3
"""Operator CLI that loads `import-dataset.json` into an empty catalogue as the seeded
baseline (FR-70, FR-76, ADR-0010, ADR-0042).

This is a one-off setup step, run once on a new deployment after the transform has emitted a
dataset (`nptc-transform run --emit-dataset --release-name YYYY-MM`) and migrations have run. It
is a thin wrapper around `nptc.catalogue.seed_import.seed_baseline`; no seeding logic lives here.

The whole import is one transaction. Any refusal, anywhere, leaves the database as it was. The
loader refuses a catalogue that already holds an entry: a reseed is a deliberate database reset,
described in the runbook.

It connects as the application role (`NPTC_DATABASE_URL`), like `scripts/grant_role.py`, because
it writes through the same code paths the API uses.

Usage:
  uv run python scripts/seed_baseline.py --dataset transform-report/import-dataset.json
  uv run python scripts/seed_baseline.py --dataset path/to/import-dataset.json --dry-run
  uv run python scripts/seed_baseline.py --dataset path/to/import-dataset.json \\
      --database-url postgresql+psycopg://...

See docs/operations/runbooks/seed-baseline.md for the exit code reference and the reset procedure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Exit codes - stable, safe to depend on from a setup script.
#: 0 = seeded (or --dry-run found the dataset seedable and rolled back); 2 = usage error;
#: 3 = the dataset was refused before any write; 4 = the catalogue already holds data, nothing
#: written; 5 = a write was refused and everything rolled back; 6 = could not complete.
EXIT_OK = 0
EXIT_USAGE_ERROR = 2
EXIT_DATASET_REFUSED = 3
EXIT_CATALOGUE_NOT_EMPTY = 4
EXIT_IMPORT_REFUSED = 5
EXIT_COULD_NOT_COMPLETE = 6


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
        help="Path to the transform's import-dataset.json.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="DSN to connect with. Falls back to NPTC_DATABASE_URL if not given.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the whole import, then roll it back instead of committing.",
    )
    return parser.parse_args(argv)


def _resolve_database_url(cli_value: str | None) -> str | None:
    """`--database-url`, then `NPTC_DATABASE_URL`. An explicitly empty `--database-url` is a
    usage mistake and never falls through to the environment. The `nptc` import is deferred so
    `--help` never needs the workspace to be importable."""
    if cli_value is not None:
        if not cli_value:
            raise ValueError("--database-url must not be empty")
        return cli_value

    from pydantic import ValidationError

    from nptc.settings import DatabaseSettings

    try:
        return DatabaseSettings().database_url
    except ValidationError as exc:
        if all(error["type"] == "missing" for error in exc.errors()):
            return None
        raise


def _print_problems(heading: str, problems: tuple[str, ...]) -> None:
    print(f"error: {heading}", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        database_url = _resolve_database_url(args.database_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    except Exception as exc:
        print(f"error: could not resolve database URL ({type(exc).__name__})", file=sys.stderr)
        return EXIT_COULD_NOT_COMPLETE

    if not database_url:
        print(
            "error: no database URL configured - pass --database-url or set NPTC_DATABASE_URL",
            file=sys.stderr,
        )
        return EXIT_USAGE_ERROR

    try:
        # Deferred, as in grant_role.py: keeps --help and usage-error paths free of a hard
        # SQLAlchemy/nptc import. The exception text is never printed for an unexpected failure,
        # only its type: it can carry connection details (NFR-26).
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from nptc.catalogue.seed_dataset import (
            DatasetInvalidError,
            DatasetNotSeedableError,
            DatasetReadError,
            read_import_dataset,
        )
        from nptc.catalogue.seed_import import (
            CatalogueNotEmptyError,
            SeedImportError,
            SeedPrerequisiteError,
            seed_baseline,
        )
        from nptc.db.session import REQUIRED_ISOLATION_LEVEL

        try:
            dataset = read_import_dataset(args.dataset)
        except (DatasetInvalidError, DatasetNotSeedableError) as exc:
            _print_problems("the dataset was refused; nothing was written", exc.problems)
            return EXIT_DATASET_REFUSED
        except DatasetReadError as exc:
            print(f"error: {exc}; nothing was written", file=sys.stderr)
            return EXIT_DATASET_REFUSED

        # Explicit READ COMMITTED: the audit append lock raises under anything stricter, and an
        # ad hoc engine would inherit whatever the server defaults to.
        engine = create_engine(database_url, isolation_level=REQUIRED_ISOLATION_LEVEL)
        # Not `with session.begin()`: a `return` inside it commits. Every refusal below rolls back
        # explicitly, so no path can commit a partial import.
        session = Session(engine)
        try:
            session.begin()
            try:
                report = seed_baseline(session, dataset)
            except CatalogueNotEmptyError as exc:
                session.rollback()
                print(f"error: {exc}; nothing was written", file=sys.stderr)
                return EXIT_CATALOGUE_NOT_EMPTY
            except SeedPrerequisiteError as exc:
                session.rollback()
                _print_problems("the import was refused and rolled back", exc.problems)
                return EXIT_IMPORT_REFUSED
            except SeedImportError as exc:
                session.rollback()
                print(f"error: {exc}; the import was rolled back", file=sys.stderr)
                return EXIT_IMPORT_REFUSED

            if args.dry_run:
                session.rollback()
            else:
                session.commit()
        finally:
            session.close()
            engine.dispose()
    except Exception as exc:
        print(f"error: could not seed the baseline ({type(exc).__name__})", file=sys.stderr)
        return EXIT_COULD_NOT_COMPLETE

    prefix = "DRY RUN (rolled back): would have seeded" if args.dry_run else "SEEDED"
    print(f"{prefix} {report.entries} entries as baseline release {report.release_name!r}")
    print(f"  source: {report.source_filename} (sha256 {report.source_sha256})")
    print(
        f"  synonyms: {report.synonyms}, code bindings: {report.code_bindings}, "
        f"property values: {report.property_values}"
    )
    print(f"  highest business key: {report.highest_business_key}")
    for key in report.system_properties_created:
        print(f"  created system property: {key}")
    for label in report.provisional_subgroup_codes:
        print(f"  created provisional subgroup code: {label}")
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # last line of defence - see main()'s own handling above
        print(f"error: could not seed the baseline ({type(exc).__name__})", file=sys.stderr)
        sys.exit(EXIT_COULD_NOT_COMPLETE)
