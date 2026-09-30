"""`nptc.db.provision_login` (NFR-41): the deployable stack's `migrate`
service creates the app runtime login itself, so no operator runs SQL by hand.

`nptc_app_login` is cluster-wide, and the session-scoped `migrated` fixture
already provisioned it with `APP_LOGIN_PASSWORD`. A test that changes that
password restores it in a `finally`, because every later test in the worker
authenticates with it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import BaseModel, model_validator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError, ProgrammingError
from testcontainers.community.postgres import PostgresContainer

from nptc.db import provision_login
from nptc.db.provision_login import APP_LOGIN_ROLE, main, provision_app_login

_conftest_spec = importlib.util.spec_from_file_location(
    "_test_provision_login_conftest", Path(__file__).parent / "conftest.py"
)
assert _conftest_spec is not None and _conftest_spec.loader is not None
_conftest = importlib.util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
#: The value `migrated` provisioned. Imported, not re-typed: the `finally`
#: blocks below restore it, and a stale copy would lock every later test in
#: the worker out of the login.
_APP_LOGIN_PASSWORD: str = _conftest.APP_LOGIN_PASSWORD

_INSUFFICIENT_PRIVILEGE = "42501"
#: A quote, a backslash, a dollar-quote terminator and a percent sign: each
#: breaks a hand-quoted CREATE ROLE ... PASSWORD statement. This proves the
#: *function* stores any string verbatim. It is not a password an operator may
#: use: the CLI refuses `%` (see `test_cli_refuses_a_password_with_a_url_delimiter`)
#: because compose places the password inside a database URL.
_HOSTILE_PASSWORD = "it's a \\ $$ 100% test-only-not-a-real-secret"
_HOSTILE_URL_SAFE_PASSWORD = "it's a \\ $$ test-only-not-a-real-secret"


def _login_engine(owner_engine: Engine, password: str) -> Engine:
    url = owner_engine.url.set(username=APP_LOGIN_ROLE, password=password)
    return create_engine(url)


def _can_log_in(owner_engine: Engine, password: str) -> bool:
    engine = _login_engine(owner_engine, password)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError:
        return False
    finally:
        engine.dispose()
    return True


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_login_role_is_a_member_of_the_app_role(db: Connection, migrated: None) -> None:
    is_member = db.execute(
        text("SELECT pg_has_role(:login, 'nptc_app', 'member')"), {"login": APP_LOGIN_ROLE}
    ).scalar_one()

    assert is_member is True


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_provisioning_twice_leaves_a_working_login(owner_engine: Engine, migrated: None) -> None:
    provision_app_login(owner_engine, _APP_LOGIN_PASSWORD)

    assert _can_log_in(owner_engine, _APP_LOGIN_PASSWORD)


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_provisioning_again_rotates_the_password(owner_engine: Engine, migrated: None) -> None:
    rotated = "rotated-test-only-not-a-real-secret"
    try:
        provision_app_login(owner_engine, rotated)

        assert _can_log_in(owner_engine, rotated)
        assert not _can_log_in(owner_engine, _APP_LOGIN_PASSWORD)
    finally:
        provision_app_login(owner_engine, _APP_LOGIN_PASSWORD)


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_a_password_with_quoting_characters_is_stored_verbatim(
    owner_engine: Engine, migrated: None
) -> None:
    try:
        provision_app_login(owner_engine, _HOSTILE_PASSWORD)

        assert _can_log_in(owner_engine, _HOSTILE_PASSWORD)
    finally:
        provision_app_login(owner_engine, _APP_LOGIN_PASSWORD)


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_the_login_role_cannot_create_tables(app_db: Connection) -> None:
    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text("CREATE TABLE provision_login_probe (id integer)"))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-41")
@pytest.mark.integration
def test_cli_provisions_from_the_environment_without_printing_the_password(
    postgres_container: PostgresContainer,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", postgres_container.get_connection_url())
    monkeypatch.setenv("NPTC_APP_DB_PASSWORD", _APP_LOGIN_PASSWORD)

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert _APP_LOGIN_PASSWORD not in captured.out + captured.err
    assert _can_log_in(owner_engine, _APP_LOGIN_PASSWORD)


def test_cli_names_the_missing_password_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", "postgresql+psycopg://o:x@localhost/none")
    monkeypatch.delenv("NPTC_APP_DB_PASSWORD", raising=False)

    exit_code = main()

    assert exit_code == 1
    assert "NPTC_APP_DB_PASSWORD" in capsys.readouterr().err


def test_cli_refuses_a_blank_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", "postgresql+psycopg://o:x@localhost/none")
    monkeypatch.setenv("NPTC_APP_DB_PASSWORD", "   ")

    assert main() == 1
    assert "NPTC_APP_DB_PASSWORD" in capsys.readouterr().err


def test_cli_failure_prints_the_error_type_but_not_the_dsn_or_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "dsn-secret-test-only-not-a-real-secret"
    monkeypatch.setenv(
        "NPTC_MIGRATION_DATABASE_URL", f"postgresql+psycopg://owner:{secret}@127.0.0.1:1/none"
    )
    monkeypatch.setenv("NPTC_APP_DB_PASSWORD", _HOSTILE_URL_SAFE_PASSWORD)

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "OperationalError" in captured.err
    assert secret not in captured.out + captured.err
    assert _HOSTILE_URL_SAFE_PASSWORD not in captured.out + captured.err


@pytest.mark.req("NFR-41")
@pytest.mark.parametrize("delimiter", ["@", ":", "/", "?", "#", "%"])
def test_cli_refuses_a_password_with_a_url_delimiter(
    delimiter: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Such a password would provision, then fail to log in through the
    DSN compose builds, and the only symptom would be an unhealthy backend."""
    password = f"before{delimiter}after-test-only-not-a-real-secret"
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", "postgresql+psycopg://o:x@127.0.0.1:1/none")
    monkeypatch.setenv("NPTC_APP_DB_PASSWORD", password)

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "NPTC_APP_DB_PASSWORD" in captured.err
    assert "OperationalError" not in captured.err, "must refuse before connecting"
    assert password not in captured.out + captured.err


def test_a_settings_error_without_a_field_is_reported_without_crashing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Whole(BaseModel):
        @model_validator(mode="after")
        def _always_refuse(self) -> Whole:
            raise ValueError("refused as a whole")

    def refuse() -> object:
        Whole()
        raise AssertionError("unreachable")

    monkeypatch.setattr(provision_login, "AppLoginSettings", refuse)
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", "postgresql+psycopg://o:x@127.0.0.1:1/none")

    exit_code = main()

    assert exit_code == 1
    assert "the settings as a whole" in capsys.readouterr().err
