"""Alembic environment (issue #33).

URL resolution has exactly two routes, tried in this order:

1. ``config.attributes["connection"]`` - a live ``Connection`` the test
   harness hands in directly (Alembic's connection-sharing recipe), so
   tests run migrations against the same testcontainers Postgres the rest
   of the fixture graph uses, with no URL round-trip.
2. ``MigrationSettings().migration_database_url`` - the operator path,
   documented in docs/operations/upgrade.md. A dedicated settings class,
   not the combined app ``Settings`` - an operator running a migration
   should never need ``NPTC_DATABASE_URL`` set too.

Deliberately no fallback to ``config.get_main_option("sqlalchemy.url")``: no
``sqlalchemy.url`` is ever committed (NFR-26), and falling back to it would
mean a misconfigured environment silently tries Alembic's own placeholder
URL (``driver://user:pass@localhost/dbname``) instead of failing loudly and
naming the missing environment variable.
"""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
from typing import cast

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from nptc.db import models  # noqa: F401 -- side effect: registers every model with Base.metadata
from nptc.db.base import Base
from nptc.settings import MigrationSettings

config = context.config

# Mandatory guard. With no alembic.ini on disk, config.config_file_name is
# still the literal string "alembic.ini" when the CLI builds this Config
# (its own default when no ini file is found) - calling fileConfig() on that
# nonexistent path raises. This branch is never hit by the test harness's
# programmatic Config(toml_file=...), which leaves config_file_name as None.
if config.config_file_name is not None and Path(config.config_file_name).is_file():
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _run_migrations_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection (``--sql``)."""
    context.configure(
        url=MigrationSettings().migration_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_migrations_with_connection(cast(Connection, connection))
        return

    connectable = create_engine(MigrationSettings().migration_database_url, poolclass=pool.NullPool)
    with connectable.connect() as new_connection:
        _run_migrations_with_connection(new_connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
