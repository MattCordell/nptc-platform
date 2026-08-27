"""Downgrade/upgrade round-trip fingerprint test (issue #33), plus (issue
#35) post-round-trip behavioural refusal tests.

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
`pg_extension`, plus `information_schema.role_table_grants`.

That fingerprint comparison is only *relative*: `before` and `after` are
both taken from this same database, so a grant that is missing in **both**
(rather than merely one) compares equal and passes. It catches a grant
*changing* across the round-trip, not a grant that was *absent all along* -
absence is exactly what #35's "grants re-asserted after downgrade then
upgrade" criterion is about, and that is what the
`test_app_role_is_still_refused_*_after_round_trip` tests below assert
instead: real UPDATE/DELETE/TRUNCATE statements, run as the genuinely
separate `nptc_app_login` role, against the schema produced by an actual
`downgrade base` -> `upgrade head` round-trip.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url
from testcontainers.community.postgres import PostgresContainer

from nptc.db.property_indexes import GENERATED_INDEX_NAME_RE

_support_spec = importlib.util.spec_from_file_location(
    "_test_db_round_trip_audit_privilege_support",
    Path(__file__).parent / "audit_privilege_support.py",
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
insert_one_row = _support.insert_one_row
assert_refused = _support.assert_refused

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


def _sorted_by_json(items: list[Any]) -> list[Any]:
    """Sorts a list of otherwise order-independent entries (which foreign
    key comes first, which index comes first) by each entry's own canonical
    JSON text - without touching anything *inside* an entry. A composite
    index's `column_names` or a composite primary key's
    `constrained_columns` is itself an ordered list where the order is
    semantically significant, and must be compared verbatim, never
    reordered - only the list of *entries* is reordered here."""
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))


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
            # A single dict per table, its internal column order left
            # exactly as reflection reported it - see _sorted_by_json.
            "primary_key": inspector.get_pk_constraint(table_name),
            "foreign_keys": _sorted_by_json(inspector.get_foreign_keys(table_name)),
            "unique_constraints": _sorted_by_json(inspector.get_unique_constraints(table_name)),
            "check_constraints": _sorted_by_json(inspector.get_check_constraints(table_name)),
            # #54's generated ix_propval_p* indexes are reconciler-managed
            # runtime state, not schema history (ADR-0012) - excluded here
            # for the same reason env.py's include_object excludes them
            # from autogenerate. Not strictly load-bearing today (this
            # dedicated nptc_roundtrip database is dropped and recreated,
            # and nothing reconciles against it), but ADR-0012 mandates the
            # filter so the first test or CLI run that ever does touch it
            # doesn't produce a confusing round-trip failure.
            "indexes": _sorted_by_json(
                [
                    index
                    for index in inspector.get_indexes(table_name)
                    if not GENERATED_INDEX_NAME_RE.match(index["name"] or "")
                ]
            ),
        }

    with engine.connect() as connection:
        extensions = sorted(
            row[0] for row in connection.execute(text("SELECT extname FROM pg_extension"))
        )
        grants = _sorted_by_json(
            [
                list(row)
                for row in connection.execute(
                    text(
                        "SELECT table_name, grantee, privilege_type "
                        "FROM information_schema.role_table_grants "
                        "WHERE table_schema = 'public'"
                    )
                )
            ]
        )
        # role_table_grants alone is blind to a column-level GRANT UPDATE
        # (e.g. app_user's) - that shows up only in column_privileges. Without
        # this, a column-level grant living in the wrong migration would pass
        # this fingerprint despite not surviving a downgrade/upgrade
        # round-trip.
        column_grants = _sorted_by_json(
            [
                list(row)
                for row in connection.execute(
                    text(
                        "SELECT table_name, column_name, grantee, privilege_type "
                        "FROM information_schema.column_privileges "
                        "WHERE table_schema = 'public'"
                    )
                )
            ]
        )

        # issue #48/ADR-0023: `nptc_sctid_is_valid` is this repo's first
        # database function, created by migration 0008 and referenced by
        # `ck_code_binding_code`. Without asserting its own survival here,
        # a downgrade that dropped the function but a re-upgrade that
        # failed to recreate it would pass every other check above (the
        # table's own CHECK constraint list is unaffected by whether the
        # function it calls actually exists).
        functions = sorted(
            row[0]
            for row in connection.execute(
                text(
                    "SELECT proname FROM pg_proc "
                    "JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace "
                    "WHERE pg_namespace.nspname = 'public'"
                )
            )
        )

    return {
        "tables": tables,
        "extensions": extensions,
        "grants": grants,
        "column_grants": column_grants,
        "functions": functions,
    }


@pytest.fixture(scope="module")
def roundtrip_engine(postgres_container: PostgresContainer, migrated: None) -> Iterator[Engine]:
    # Depends on `migrated` (unused directly) purely for ordering: it forces
    # the shared nptc_test database's own migration - and so the first-ever
    # `CREATE ROLE nptc_app` in this cluster - to run before this fixture's
    # migration does, regardless of test collection order. Without this,
    # test_downgrade_base_then_upgrade_head_reproduces_the_same_schema's own
    # comment about proving 0001's `IF NOT EXISTS` guard would only hold by
    # accident of whichever test happened to run first.
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


@pytest.fixture(scope="module")
def round_tripped(roundtrip_engine: Engine) -> tuple[dict[str, Any], dict[str, Any]]:
    """Performs the actual `upgrade head` -> fingerprint -> `downgrade base`
    -> `upgrade head` -> fingerprint sequence once per module, so every test
    that needs the round trip to have happened - the fingerprint comparison
    itself and the post-round-trip refusal tests below - depends on it
    through the fixture graph rather than on test collection order.

    nptc_app already exists in this cluster by the time this migration runs
    here (the session-scoped `migrated` fixture created it against the
    shared nptc_test database first) - a live proof, not just an assertion,
    that 0001's `IF NOT EXISTS` guard is load-bearing: roles are
    cluster-wide, so a plain CREATE ROLE would fail here otherwise.
    """
    _upgrade_head(roundtrip_engine)
    before = _fingerprint(roundtrip_engine)

    _downgrade_base(roundtrip_engine)
    _upgrade_head(roundtrip_engine)
    after = _fingerprint(roundtrip_engine)

    return before, after


@pytest.mark.integration
def test_downgrade_base_then_upgrade_head_reproduces_the_same_schema(
    round_tripped: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    before, after = round_tripped
    assert before == after


@pytest.fixture(scope="module")
def roundtrip_app_engine(
    roundtrip_engine: Engine,
    round_tripped: tuple[dict[str, Any], dict[str, Any]],
    app_login_credentials: tuple[str, str],
) -> Iterator[Engine]:
    """Authenticates as `nptc_app_login` against `nptc_roundtrip`, exactly
    as `app_engine` does against `nptc_test` (conftest.py) - a genuinely
    separate login, never a superuser connection with SET ROLE, so the
    grant it exercises is real. Roles are cluster-wide, so the login the
    session-scoped `migrated` fixture provisions is already usable here;
    `nptc_roundtrip` grants `CONNECT` to `PUBLIC` by default (new database),
    and schema `USAGE` comes from migration 0001.

    Depends on `round_tripped` (unused directly) purely for ordering: these
    tests must run against the schema produced by the actual
    `downgrade base` -> `upgrade head` round trip, not the schema from the
    first `upgrade head` alone.
    """
    login_role, login_password = app_login_credentials
    roundtrip_url = roundtrip_engine.url.set(username=login_role, password=login_password)
    engine = create_engine(roundtrip_url.render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def roundtrip_app_db(roundtrip_app_engine: Engine) -> Iterator[Connection]:
    with roundtrip_app_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_app_role_can_still_insert_and_select_after_round_trip(
    roundtrip_app_db: Connection,
) -> None:
    """Also proves the identity-sequence grant survived re-migration."""
    insert_one_row(roundtrip_app_db)

    rows = roundtrip_app_db.execute(
        text("SELECT action, entity_type, entity_id FROM audit_event")
    ).all()

    assert rows == [("test.action", "test_entity", "1")]


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_still_refused_update_after_round_trip(roundtrip_app_db: Connection) -> None:
    insert_one_row(roundtrip_app_db)

    assert_refused(roundtrip_app_db, "UPDATE audit_event SET reason = 'edited'")


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_still_refused_delete_after_round_trip(roundtrip_app_db: Connection) -> None:
    insert_one_row(roundtrip_app_db)

    assert_refused(roundtrip_app_db, "DELETE FROM audit_event")


@pytest.mark.req("NFR-09")
@pytest.mark.integration
def test_app_role_is_still_refused_truncate_after_round_trip(roundtrip_app_db: Connection) -> None:
    insert_one_row(roundtrip_app_db)

    assert_refused(roundtrip_app_db, "TRUNCATE audit_event")
