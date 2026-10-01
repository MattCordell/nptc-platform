"""Test harness for backend/tests (issue #33).

Every fixture here talks to a real Postgres container via testcontainers -
never `metadata.create_all` and never an in-memory substitute (NFR-39). See
docs/adr/0011-database-migration-foundation.md for the rejected
alternatives.

Fixture graph::

    postgres_container (session)  the container itself
    owner_engine        (session)  engine authenticating as the container's
                                    bootstrap superuser-equivalent role
    migrated            (session)  `alembic upgrade head`, plus provisioning
                                    the nptc_app_login role - requested
                                    explicitly by db/app_engine, never
                                    autouse, so a test needing neither a
                                    container nor a connection (e.g.
                                    test_settings.py, test_sql_parameterisation.py)
                                    never starts Docker at all
    app_engine          (session)  engine authenticating as nptc_app_login
    db / app_db         (function) a connection in an outer transaction,
                                    rolled back after the test
    owner_session /     (function) an ORM Session joined to db / app_db
    app_session                     through a SAVEPOINT, so its commit() stays
                                    inside the rolled-back transaction
    capture_statements  (function) context manager recording the SQL a
                                    connection or engine executes
    pristine_audit_event (function) wipes committed audit_event/user_role/
                                    app_user leftovers before AND after the
                                    test - requested explicitly by the few
                                    tests whose assertion is genuinely
                                    whole-table (issue #190)

The `integration` marker is derived, not only hand-written: a test whose
fixture closure reaches `postgres_container` is marked at collection, so
`-m "not integration"` never starts Docker. A container started inside a
test module's own fixture (the Keycloak tests) is invisible to that rule and
still needs the hand-written marker.

No `_no_real_network` autouse guard here, unlike transform/tests and
shared/tests: testcontainers must open a real TCP socket to the mapped
container port to reach it at all, so that guard would break every test in
this tree. NFR-37 is proven for this tree instead by the
`backend-integration` CI job in .github/workflows/ci.yml, which blocks all
*other* egress - see that job's own comment for the exact scope (container
traffic transits FORWARD, not OUTPUT, so this proves the test process makes
no egress, not the container).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from nptc.db.provision_login import APP_LOGIN_ROLE, provision_app_login

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.yml"
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"

#: Obviously-synthetic, local-only credential - this role exists only
#: inside a disposable test container, never a real deployment (NFR-26).
APP_LOGIN_PASSWORD = "nptc-app-login-test-only-not-a-real-secret"


def compose_config() -> dict[str, Any]:
    """The one parser for deploy/compose.yml (issue #33's decision with the
    maintainer, exported for test_keycloak_realm.py by issue #40) - every
    other reader of this file, in this repo or in CI, goes through this
    function or image_from_compose below rather than a second, ad hoc
    yaml.safe_load call that could silently diverge from it."""
    data: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return data


def image_from_compose(service: str = "postgres") -> str:
    """The exact tag a given service pins, so bumping compose moves the
    integration test target automatically."""
    image: str = compose_config()["services"][service]["image"]
    return image


def _alembic_config() -> Config:
    return Config(toml_file=str(PYPROJECT_FILE))


#: Session fixtures that start a container. Register a new container-backed
#: fixture here, not only in the tests that request it.
CONTAINER_FIXTURES = frozenset({"postgres_container"})


# tryfirst: pytest's own `-m` deselection is a `pytest_collection_modifyitems`
# implementation too, and it must see the marker this hook adds. Plugin
# registration order happens to guarantee that already; this makes it explicit.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if isinstance(item, pytest.Function) and CONTAINER_FIXTURES.intersection(item.fixturenames):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    # driver="psycopg" (v3), not testcontainers' own default of psycopg2 -
    # psycopg2 is not a dependency anywhere in this workspace.
    container = PostgresContainer(
        image_from_compose(),
        username="nptc_owner",
        password="nptc-owner-test-only-not-a-real-secret",
        dbname="nptc_test",
        driver="psycopg",
    )
    # The initdb flags compose gives the real database, so the suite runs on
    # the same encoding the deployed stack pins.
    initdb_args = compose_config()["services"]["postgres"]["environment"]["POSTGRES_INITDB_ARGS"]
    container.with_env("POSTGRES_INITDB_ARGS", initdb_args)
    with container:
        yield container


@pytest.fixture(scope="session")
def owner_engine(postgres_container: PostgresContainer) -> Iterator[Engine]:
    engine = create_engine(postgres_container.get_connection_url())
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrated(owner_engine: Engine) -> None:
    """Applies every migration via `command.upgrade`, never
    `metadata.create_all` - the acceptance criteria are about the
    migrations themselves running, and create_all would never execute their
    GRANT/REVOKE statements at all.

    Also provisions nptc_app_login through the same function the deployable
    stack's `migrate` service runs. A superuser connection using SET ROLE
    would still bypass some privilege checks, so only a genuinely separate
    authenticated login proves the grant is real.

    Deliberately not `autouse`: a test needing neither a container nor a
    connection (test_settings.py, test_sql_parameterisation.py) must not be
    forced to start Docker just because it happens to live under
    backend/tests. `db` and `app_engine` request this fixture explicitly
    instead.
    """
    config = _alembic_config()
    with owner_engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    provision_app_login(owner_engine, APP_LOGIN_PASSWORD)


@pytest.fixture(scope="session")
def app_engine(postgres_container: PostgresContainer, migrated: None) -> Iterator[Engine]:
    owner_url = make_url(postgres_container.get_connection_url())
    login_url = owner_url.set(username=APP_LOGIN_ROLE, password=APP_LOGIN_PASSWORD)
    engine = create_engine(login_url.render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db(owner_engine: Engine, migrated: None) -> Iterator[Connection]:
    with owner_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def app_db(app_engine: Engine) -> Iterator[Connection]:
    with app_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _savepoint_session(connection: Connection) -> Session:
    """`join_transaction_mode="create_savepoint"` is what lets the ORM's own
    `commit()` calls nest inside the outer transaction `db`/`app_db` roll back
    at teardown. Without it, a `commit()` would end that outer transaction and
    leave the test's rows in the shared container."""
    return Session(bind=connection, join_transaction_mode="create_savepoint")


@pytest.fixture
def app_session(app_db: Connection) -> Session:
    """An ORM `Session` on `app_db`, i.e. as the `nptc_app_login` role."""
    return _savepoint_session(app_db)


@pytest.fixture
def owner_session(db: Connection) -> Session:
    """The same session shape on the owner connection `db`, for a test whose
    subject is SQL logic rather than the app role's privileges."""
    return _savepoint_session(db)


StatementFilter = Callable[[str, object], bool]
StatementCapture = Callable[..., AbstractContextManager[list[str]]]


@pytest.fixture
def capture_statements() -> StatementCapture:
    """Returns a context manager `capture(bind, keep=None)` that yields the
    list of SQL statements `bind` (a connection or engine) executes inside the
    `with` block. `keep(statement, parameters)` narrows what is recorded."""

    @contextmanager
    def capture(
        bind: Connection | Engine, keep: StatementFilter | None = None
    ) -> Iterator[list[str]]:
        statements: list[str] = []

        def _record(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if keep is None or keep(statement, parameters):
                statements.append(statement)

        event.listen(bind, "before_cursor_execute", _record)
        try:
            yield statements
        finally:
            event.remove(bind, "before_cursor_execute", _record)

    return capture


def _wipe_committed_audit_state(owner_engine: Engine) -> None:
    """Deletes every row a test in this tree could plausibly have
    committed to `user_role`, `user_identity`, `audit_event` or `app_user`
    - every table that FK-references `app_user`, deleted before it (that
    order - FK-safe; `0003_user_and_user_identity.py` gives neither FK an
    `ON DELETE CASCADE`, so `app_user` must go last). Only the owner role
    can (`nptc_app_login` has no DELETE on any of these, NFR-09).

    Safe to run unconditionally: nothing in this test tree relies on
    inherited rows in these tables - the tests that commit real rows at
    all (`test_audit_chain.py`'s concurrency test, `test_grants.py`'s
    concurrency test, the `*_cli.py` modules) each already clean up their
    own ids, so anything left here is exactly the accidental leakage issue
    #190 is about removing.
    """
    with owner_engine.connect() as connection:
        connection.execute(text("DELETE FROM user_role"))
        connection.execute(text("DELETE FROM user_identity"))
        connection.execute(text("DELETE FROM audit_event"))
        connection.execute(text("DELETE FROM app_user"))
        connection.commit()


@pytest.fixture
def pristine_audit_event(owner_engine: Engine, migrated: None) -> Iterator[None]:
    """Explicit isolation for the handful of assertions that are genuinely
    whole-table - an empty-chain check, `first.prev_hash == GENESIS_HASH`,
    or an exact `count(*)` with no scoping predicate available (issue
    #190). Cleans **before** yielding as well as after: cleaning only at
    teardown makes a test's precondition something it inherits from
    whatever happened to run before it in this worker/container, which is
    exactly the ordering dependency this fixture exists to remove -
    scheduling order (serial, randomised, or xdist `--dist loadscope`)
    then stops mattering.
    """
    _wipe_committed_audit_state(owner_engine)
    yield
    _wipe_committed_audit_state(owner_engine)


@pytest.fixture(scope="session")
def app_login_credentials(migrated: None) -> tuple[str, str]:
    """`(APP_LOGIN_ROLE, APP_LOGIN_PASSWORD)`, for a test module that needs
    to authenticate as the login role against a database other than
    `postgres_container`'s own (test_db_round_trip.py's `nptc_roundtrip`) -
    the module constants themselves are already exported, but a fixture
    lets such a module depend on `migrated` (the role actually existing)
    through the fixture graph rather than a bare module-level import."""
    return APP_LOGIN_ROLE, APP_LOGIN_PASSWORD


@pytest.fixture
def hostile_api_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Exports an `NPTC_FSN_SEMANTIC_TAG` that makes any fresh `ApiSettings()`
    raise, with `get_api_settings`' process-wide cache emptied on the way in
    and out. Without the clear, an earlier test that warmed the cache would
    let a route that regressed to calling `get_api_settings()` directly still
    answer 200, so the test would pass for the wrong reason."""
    from nptc.api.dependencies import get_api_settings

    get_api_settings.cache_clear()
    monkeypatch.setenv("NPTC_FSN_SEMANTIC_TAG", "stripped")
    yield
    get_api_settings.cache_clear()
