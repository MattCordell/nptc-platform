#!/usr/bin/env python3
"""Operator CLI for the one grant nothing else in the platform can make:
the first Administrator on a fresh deployment (issue #44, FR-01, FR-44).

**Why this exists at all.** This issue's scope deliberately has no grant/
revoke HTTP endpoint or admin UI - those stay P2. But `nptc.auth.grants.
grant_role`/`revoke_role` both require a `Principal` holding
`role.grant.any`, and FR-01's last-administrator guard means a role can
never be revoked down to zero Administrators - so on a fresh deployment,
with zero grants in the database, there is no `Principal` that could ever
grant the first one. This script is the one, deliberate, out-of-band
escape hatch: an operator with direct database access, not a token, not a
claim.

It is not a bypass of the framework - it calls the same
`nptc.auth.grants.grant_role_unchecked` a first-login Provisional grant
uses, which still emits a `user_role.granted` audit event
(`granted_by_user_id=None`, the one case that field is nullable for - see
`nptc.db.models.user_role`) and is still idempotent. There is no `--force`
and no way to revoke through this script: once a second Administrator
exists, every further grant/revoke is expected to go through the ordinary
`Principal`-checked path (`nptc.auth.grants.grant_role`/`revoke_role`,
landing with the P2 user-administration endpoints).

Usage:
  uv run python scripts/grant_role.py --username jsmith --role administrator
  uv run python scripts/grant_role.py --username jsmith --role administrator \\
      --database-url postgresql+psycopg://...

See docs/operations/upgrade.md for the full bootstrap runbook.
"""

from __future__ import annotations

import argparse
import sys

EXIT_OK = 0
EXIT_USAGE_ERROR = 2
EXIT_NOT_FOUND = 3
EXIT_REFUSED = 4
EXIT_COULD_NOT_COMPLETE = 5

#: Kept in sync with `nptc.auth.permissions.GRANTABLE_ROLES` by
#: `scripts/tests/test_grant_role.py` - this list is user-facing argparse
#: `choices`, so it is written out rather than imported, to keep --help
#: usable even if the workspace import fails; the test is what keeps it
#: honest.
_GRANTABLE_ROLE_CHOICES = ["observer", "provisional", "member", "reviewer", "administrator"]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--username", required=True, help="The app_user.username to grant a role to."
    )
    parser.add_argument("--role", required=True, choices=_GRANTABLE_ROLE_CHOICES)
    parser.add_argument(
        "--database-url",
        default=None,
        help="DSN to connect with. Falls back to NPTC_DATABASE_URL if not given.",
    )
    return parser.parse_args(argv)


def _resolve_database_url(cli_value: str | None) -> str | None:
    """Same precedence style as `scripts/verify_audit_chain.py`: an
    explicitly-empty `--database-url` is a usage mistake, never silently
    falls through to the environment. Deferred import so `--help` never
    requires the workspace to be importable."""
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
        # Deferred, as in verify_audit_chain.py: keeps --help/usage-error
        # paths free of a hard SQLAlchemy/nptc import requirement.
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session

        from nptc.audit.writer import AuditContext
        from nptc.auth.errors_authorisation import LastAdministratorError
        from nptc.auth.grants import grant_role_unchecked
        from nptc.auth.permissions import Role
        from nptc.db.models.user import User

        engine = create_engine(database_url)
        # Deliberately not `with Session(engine) as session, session.begin():`
        # - a `return` from inside that form exits the `session.begin()`
        # context manager normally (no exception), which *commits*. Every
        # refusal path below must roll back explicitly before returning,
        # so a future change that has `grant_role_unchecked` write
        # something before raising can never commit a partial grant while
        # this script reports it as refused.
        session = Session(engine)
        try:
            session.begin()
            user = session.execute(
                select(User).where(User.username == args.username)
            ).scalar_one_or_none()
            if user is None:
                session.rollback()
                print(f"error: no app_user with username {args.username!r}", file=sys.stderr)
                return EXIT_NOT_FOUND

            try:
                grant_role_unchecked(
                    session,
                    target_user_id=user.id,
                    role=Role(args.role),
                    granted_by_user_id=None,
                    audit=AuditContext.system(),
                )
            except LastAdministratorError as exc:
                # Unreachable for a *grant* today (LastAdministratorError
                # is only ever raised on removal), but caught explicitly
                # rather than left to the generic handler below, so a
                # future change to grant_role_unchecked that did start
                # raising it here fails with the right exit code instead
                # of a generic 5 - and, critically, rolls back rather than
                # committing whatever was written before the raise.
                session.rollback()
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_REFUSED

            session.commit()
        finally:
            session.close()
    except Exception as exc:
        # NFR-26: never print the exception body, only its type - a
        # connection/URL failure (e.g. a malformed DSN) can carry
        # host/user/database details in its message, and this is an
        # operator-facing log, not a debugger.
        print(f"error: could not grant role ({type(exc).__name__})", file=sys.stderr)
        return EXIT_COULD_NOT_COMPLETE

    print(f"granted {args.role!r} to {args.username!r}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
