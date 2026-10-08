"""The index reconciler's own login role (FR-13): what it can and cannot do as a
non-superuser. The container's bootstrap role is a superuser, so only a separate
authenticated login proves the privilege model is real."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from nptc.db.models.property_definition import (
    PropertyCardinality,
    PropertyDefinition,
    PropertyOrigin,
    PropertyScope,
)
from nptc.db.property_indexes import index_name
from nptc.db.property_reconciler import get_indexer_engine, reconcile_property_indexes
from nptc.db.provision_login import INDEXER_LOGIN_ROLE, provision_indexer_login

INDEXER_LOGIN_PASSWORD = "nptc-indexer-login-test-only-not-a-real-secret"
_KEY = "test_indexer_login_property"


@pytest.fixture
def _indexer_login_configured(
    owner_engine: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
    migrated: None,
) -> Iterator[None]:
    monkeypatch.setenv("NPTC_INDEXER_DATABASE_URL", _login_url(postgres_container, owner_engine))
    get_indexer_engine.cache_clear()
    yield
    get_indexer_engine.cache_clear()


def _login_url(postgres_container: PostgresContainer, owner_engine: Engine) -> str:
    provision_indexer_login(owner_engine, INDEXER_LOGIN_PASSWORD)
    owner_url = make_url(postgres_container.get_connection_url())
    login_url = owner_url.set(username=INDEXER_LOGIN_ROLE, password=INDEXER_LOGIN_PASSWORD)
    return login_url.render_as_string(hide_password=False)


@pytest.fixture
def indexer_connection(
    owner_engine: Engine, postgres_container: PostgresContainer, migrated: None
) -> Iterator[Connection]:
    engine = create_engine(_login_url(postgres_container, owner_engine))
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


@pytest.fixture
def _filterable_property(owner_engine: Engine, migrated: None) -> Iterator[int]:
    """One committed `filterable` string property; yields its `index_seq`. Reconciler DDL is
    not transactional, so the row and any index it built are removed explicitly."""
    with Session(bind=owner_engine) as session:
        definition = PropertyDefinition(
            key=_KEY,
            label="Indexer login property",
            datatype="string",
            cardinality=PropertyCardinality.ZERO_OR_MANY,
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            filterable=True,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
        session.add(definition)
        session.commit()
        index_seq = definition.index_seq
    try:
        yield index_seq
    finally:
        with owner_engine.connect() as connection:
            connection.execute(text("DELETE FROM property_definition WHERE key = :k"), {"k": _KEY})
            connection.commit()
        with owner_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name(index_seq, 1)}"')
            )


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_indexer_login_creates_and_drops_a_generated_index(
    owner_engine: Engine, _indexer_login_configured: None, _filterable_property: int
) -> None:
    name = index_name(_filterable_property, 1)

    created = reconcile_property_indexes()
    assert created.converged is True
    assert name in created.created

    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE property_definition SET filterable = false WHERE key = :k"), {"k": _KEY}
        )
    dropped = reconcile_property_indexes()
    assert dropped.converged is True
    assert name in dropped.dropped


@pytest.mark.req("FR-13")
@pytest.mark.integration
@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE catalogue_entry ADD COLUMN indexer_probe text",
        "DROP TABLE catalogue_entry",
        "CREATE INDEX indexer_probe ON catalogue_entry (business_key)",
        "SELECT count(*) FROM catalogue_entry",
        "DELETE FROM property_definition",
        "ALTER TABLE property_definition ADD COLUMN indexer_probe text",
    ],
)
def test_indexer_login_is_refused_outside_property_value(
    indexer_connection: Connection, statement: str
) -> None:
    with pytest.raises(ProgrammingError) as refused:
        indexer_connection.execute(text(statement))

    assert getattr(refused.value.orig, "sqlstate", None) == "42501"


@pytest.mark.req("FR-13")
@pytest.mark.integration
@pytest.mark.parametrize(
    "statement",
    [
        "CREATE INDEX app_role_probe ON property_value (entry_id)",
        "ALTER TABLE property_value ADD COLUMN app_role_probe text",
        "DROP TABLE property_value",
        "TRUNCATE property_value",
    ],
)
def test_app_login_still_cannot_change_property_value_structure(
    app_db: Connection, statement: str
) -> None:
    """Moving ownership to the index owner role must not hand the runtime login any DDL."""
    with pytest.raises(ProgrammingError) as refused:
        app_db.execute(text(statement))

    assert getattr(refused.value.orig, "sqlstate", None) == "42501"
