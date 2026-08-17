"""Offline unit tests for nptc.settings (issue #33).

No container, no network - pure environment-variable plumbing.
"""

import pytest
from pydantic import ValidationError

from nptc.settings import AuthSettings, DatabaseSettings, MigrationSettings

_APP_DSN = "postgresql+psycopg://nptc_app_login:pw@localhost/nptc"
_MIGRATION_DSN = "postgresql+psycopg://nptc_owner:pw@localhost/nptc"


@pytest.fixture(autouse=True)
def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)
    monkeypatch.delenv("NPTC_MIGRATION_DATABASE_URL", raising=False)


def test_database_settings_reads_dsn_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", _APP_DSN)

    assert DatabaseSettings().database_url == _APP_DSN


def test_database_settings_does_not_require_migration_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this guards against: DatabaseSettings must not demand
    NPTC_MIGRATION_DATABASE_URL, or every app-side read would need a
    variable it has no use for."""
    monkeypatch.setenv("NPTC_DATABASE_URL", _APP_DSN)

    DatabaseSettings()  # must not raise


def test_database_settings_missing_url_names_the_variable() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        DatabaseSettings()


def test_database_settings_rejects_blank_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "   ")

    with pytest.raises(ValidationError, match="database_url"):
        DatabaseSettings()


def test_migration_settings_reads_dsn_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", _MIGRATION_DSN)

    assert MigrationSettings().migration_database_url == _MIGRATION_DSN


def test_migration_settings_does_not_require_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this guards against: an operator running `alembic upgrade
    head` must not be forced to also set NPTC_DATABASE_URL."""
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", _MIGRATION_DSN)

    MigrationSettings()  # must not raise


def test_migration_settings_missing_url_names_the_variable() -> None:
    with pytest.raises(ValidationError, match="migration_database_url"):
        MigrationSettings()


def test_migration_settings_rejects_blank_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", "   ")

    with pytest.raises(ValidationError, match="migration_database_url"):
        MigrationSettings()


@pytest.mark.req("NFR-05")
def test_auth_settings_defaults_to_no_trusted_issuers() -> None:
    """Fail-closed default: no issuer is trusted until an operator names
    one explicitly."""
    assert AuthSettings().trusted_issuers == frozenset()


@pytest.mark.req("NFR-05")
def test_auth_settings_reads_a_single_trusted_issuer_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this guards against: `frozenset[str]` is a "complex" type to
    pydantic-settings, which tries to JSON-decode the raw environment
    string before any validator runs - every non-JSON value, including the
    single-issuer case, raised a SettingsError instead of being split."""
    monkeypatch.setenv("NPTC_TRUSTED_ISSUERS", "https://good.example")

    assert AuthSettings().trusted_issuers == frozenset({"https://good.example"})


@pytest.mark.req("NFR-05")
def test_auth_settings_reads_a_comma_separated_list_of_trusted_issuers_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_TRUSTED_ISSUERS", "https://a.example, https://b.example")

    assert AuthSettings().trusted_issuers == frozenset({"https://a.example", "https://b.example"})
