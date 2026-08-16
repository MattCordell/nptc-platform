"""Offline unit tests for nptc.settings.Settings (issue #33).

No container, no network - pure environment-variable plumbing.
"""

import pytest
from pydantic import ValidationError

from nptc.settings import Settings

_APP_DSN = "postgresql+psycopg://nptc_app_login:pw@localhost/nptc"
_MIGRATION_DSN = "postgresql+psycopg://nptc_owner:pw@localhost/nptc"


@pytest.fixture(autouse=True)
def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NPTC_DATABASE_URL", raising=False)
    monkeypatch.delenv("NPTC_MIGRATION_DATABASE_URL", raising=False)


def test_settings_reads_both_dsns_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", _APP_DSN)
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", _MIGRATION_DSN)

    settings = Settings()

    assert settings.database_url == _APP_DSN
    assert settings.migration_database_url == _MIGRATION_DSN


def test_settings_missing_database_url_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", _MIGRATION_DSN)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()


def test_settings_missing_migration_database_url_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", _APP_DSN)

    with pytest.raises(ValidationError, match="migration_database_url"):
        Settings()


def test_settings_rejects_empty_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPTC_DATABASE_URL", "")
    monkeypatch.setenv("NPTC_MIGRATION_DATABASE_URL", _MIGRATION_DSN)

    with pytest.raises(ValidationError, match="database_url"):
        Settings()
