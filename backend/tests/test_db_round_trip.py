"""Downgrade/upgrade round-trip fingerprint test (issue #33).

Needs its own dedicated database, not the shared `nptc_test` database the
rest of backend/tests uses: `downgrade base` drops every table, and
sharing a database with the session-scoped `owner_engine`/`app_engine`
fixtures would give intermittent `UndefinedTable` errors from pooled
connections still open against the dropped schema.

`compare_metadata` (test_db_migrations.py) only asks "do the migrations
match the models?" - it is blind to extensions and grants, and a
downgrade/upgrade pair broken identically in both directions would still
compare clean. This test is therefore a **reflection fingerprint** instead:
columns/PK/FKs/uniques/checks/indexes via `inspect()`, plus
`pg_extension`, plus `information_schema.role_table_grants` - the last of
which is what makes this test also satisfy #35's "grants re-asserted after
downgrade then upgrade" criterion.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from testcontainers.community.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"
_ROUNDTRIP_DB = "nptc_roundtrip"


def _alembic_config() -> Config:
    return Config(toml_file=str(PYPROJECT_FILE))


def _upgrade_head(engine: Engine) -> None:
    config = _alembic_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()


def _downgrade_base(engine: Engine) -> None:
    config = _alembic_config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        connection.commit()


def _normalize(value: Any) -> Any:
    """Recursively sorts every list by its own canonical JSON text, so two
    fingerprints of the same schema compare equal regardless of the order
    Postgres's catalogs happen to return rows in - which can differ across
    a DROP+CREATE even when the resulting schema is identical."""
    if isinstance(value, list):
        items = [_normalize(v) for v in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, dict):
        return {key: _normalize(val) for key, val in value.items()}
    return value


def _fingerprint(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    tables: dict[str, Any] = {}
    for table_name in inspector.get_table_names():
        columns = []
        for column in inspector.get_columns(table_name):
            normalized_column = dict(column)
            normalized_column["type"] = str(column["type"])
            normalized_column.pop("comment", None)
            columns.append(normalized_column)
        tables[table_name] = {
            "columns": columns,
            "primary_key": inspector.get_pk_constraint(table_name),
            "foreign_keys": inspector.get_foreign_keys(table_name),
            "unique_constraints": inspector.get_unique_constraints(table_name),
            "check_constraints": inspector.get_check_constraints(table_name),
            "indexes": inspector.get_indexes(table_name),
        }

    with engine.connect() as connection:
        extensions = [
            row[0] for row in connection.execute(text("SELECT extname FROM pg_extension"))
        ]
        grants = [
            list(row)
            for row in connection.execute(
                text(
                    "SELECT table_name, grantee, privilege_type "
                    "FROM information_schema.role_table_grants "
                    "WHERE table_schema = 'public'"
                )
            )
        ]

    return _normalize({"tables": tables, "extensions": extensions, "grants": grants})


@pytest.fixture(scope="module")
def roundtrip_engine(postgres_container: PostgresContainer) -> Iterator[Engine]:
    owner_url = make_url(postgres_container.get_connection_url())

    admin_engine = create_engine(owner_url.render_as_string(hide_password=False))
    with admin_engine.connect() as raw_connection:
        connection = raw_connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(f"DROP DATABASE IF EXISTS {_ROUNDTRIP_DB}"))
        connection.execute(text(f"CREATE DATABASE {_ROUNDTRIP_DB}"))
    admin_engine.dispose()

    roundtrip_url = owner_url.set(database=_ROUNDTRIP_DB)
    engine = create_engine(roundtrip_url.render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.integration
def test_downgrade_base_then_upgrade_head_reproduces_the_same_schema(
    roundtrip_engine: Engine,
) -> None:
    # nptc_app already exists in this cluster by the time this migration
    # runs here (the session-scoped `migrated` fixture created it against
    # the shared nptc_test database first) - a live proof, not just an
    # assertion, that 0001's `IF NOT EXISTS` guard is load-bearing: roles
    # are cluster-wide, so a plain CREATE ROLE would fail here otherwise.
    _upgrade_head(roundtrip_engine)
    before = _fingerprint(roundtrip_engine)

    _downgrade_base(roundtrip_engine)
    _upgrade_head(roundtrip_engine)
    after = _fingerprint(roundtrip_engine)

    assert before == after
