"""Creates the ``nptc_app_login`` LOGIN role and makes it a member of
``nptc_app``, and the ``nptc_indexer`` LOGIN role and makes it a member of
``nptc_property_index_owner``, so the deployable stack needs no manual SQL
(NFR-41, FR-13).

Run as ``python -m nptc.db.provision_login`` by the ``migrate`` compose
service, after ``alembic upgrade head``, as the owning role
(``NPTC_MIGRATION_DATABASE_URL``). It is idempotent: a second run rotates each
password to whatever ``NPTC_APP_DB_PASSWORD`` and ``NPTC_INDEXER_DB_PASSWORD``
now hold.

``CREATE ROLE ... PASSWORD`` cannot take a bound parameter, and NFR-22 forbids
building the statement from runtime data. So the password travels as a bound
parameter of ``set_config`` and the anonymous ``DO`` block quotes it
server-side with ``format('%L')``. Every statement below is a fixed literal.
"""

from __future__ import annotations

import sys

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text

from nptc.settings import AppLoginSettings, IndexerLoginSettings, MigrationSettings

APP_LOGIN_ROLE = "nptc_app_login"
INDEXER_LOGIN_ROLE = "nptc_indexer"

#: ``is_local = true`` scopes the value to the current transaction, so it
#: never outlives the ``begin()`` block that runs all three statements.
_STASH_PASSWORD_SQL = "SELECT set_config('nptc.app_login_password', :password, true)"

_UPSERT_LOGIN_SQL = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nptc_app_login') THEN
    EXECUTE format(
      'ALTER ROLE nptc_app_login LOGIN PASSWORD %L',
      current_setting('nptc.app_login_password')
    );
  ELSE
    EXECUTE format(
      'CREATE ROLE nptc_app_login LOGIN PASSWORD %L',
      current_setting('nptc.app_login_password')
    );
  END IF;
END $$;
"""

_GRANT_MEMBERSHIP_SQL = "GRANT nptc_app TO nptc_app_login;"


_STASH_INDEXER_PASSWORD_SQL = "SELECT set_config('nptc.indexer_login_password', :password, true)"

_UPSERT_INDEXER_LOGIN_SQL = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nptc_indexer') THEN
    EXECUTE format(
      'ALTER ROLE nptc_indexer LOGIN PASSWORD %L',
      current_setting('nptc.indexer_login_password')
    );
  ELSE
    EXECUTE format(
      'CREATE ROLE nptc_indexer LOGIN PASSWORD %L',
      current_setting('nptc.indexer_login_password')
    );
  END IF;
END $$;
"""


_GRANT_INDEXER_MEMBERSHIP_SQL = "GRANT nptc_property_index_owner TO nptc_indexer;"


def provision_app_login(engine: Engine, password: str) -> None:
    """Runs on the owning role's engine. ``nptc_app`` must already exist,
    which the migrations guarantee."""
    with engine.begin() as connection:
        connection.execute(text(_STASH_PASSWORD_SQL), {"password": password})
        connection.execute(text(_UPSERT_LOGIN_SQL))
        connection.execute(text(_GRANT_MEMBERSHIP_SQL))


def provision_indexer_login(engine: Engine, password: str) -> None:
    """Runs on the owning role's engine, like `provision_app_login`.
    `nptc_property_index_owner` must already exist, which migration 0025 guarantees."""
    with engine.begin() as connection:
        connection.execute(text(_STASH_INDEXER_PASSWORD_SQL), {"password": password})
        connection.execute(text(_UPSERT_INDEXER_LOGIN_SQL))
        connection.execute(text(_GRANT_INDEXER_MEMBERSHIP_SQL))


def main() -> int:
    # Only the exception type (or, for a settings error, the field names) is
    # printed: a connection failure can carry the DSN, and this output lands
    # in `docker compose logs` (NFR-26).
    try:
        dsn = MigrationSettings().migration_database_url
        app_password = AppLoginSettings().app_db_password.get_secret_value()
        indexer_password = IndexerLoginSettings().indexer_db_password.get_secret_value()
        engine = create_engine(dsn)
        try:
            provision_app_login(engine, app_password)
            provision_indexer_login(engine, indexer_password)
        finally:
            engine.dispose()
    except ValidationError as exc:
        names = ", ".join(
            f"NPTC_{str(error['loc'][0]).upper()}" if error["loc"] else "the settings as a whole"
            for error in exc.errors()
        )
        print(f"error: missing or invalid setting(s): {names}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"error: could not provision {APP_LOGIN_ROLE} ({type(exc).__name__})", file=sys.stderr
        )
        return 1

    print(f"provisioned {APP_LOGIN_ROLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
