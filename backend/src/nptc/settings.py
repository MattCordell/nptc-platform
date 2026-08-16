"""Backend runtime configuration (issue #33).

``pydantic-settings``, not the explicit ``os.environ`` reads
``nptc_shared.terminology.config`` uses (ADR-0003): ``pydantic-settings`` is
declared as a backend-only dependency and this is its first consumer, so
there is no cross-package reason to match that module's approach here.

Two separate settings classes, not one combined ``Settings`` with both
DSNs required: ``backend/migrations/env.py`` only ever needs
``MigrationSettings``, and an operator running `alembic upgrade head`
should not be forced to also set ``NPTC_DATABASE_URL``, a variable Alembic
has no use for. Each DSN is a required field with no default, mirroring
``nptc_shared.terminology.config``'s "raise naming the variable, never
silently default" convention: a missing, empty, or whitespace-only value
fails loudly, naming the field, rather than falling back to a placeholder a
misconfigured deployment could run against for a while before anyone
notices.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


class DatabaseSettings(BaseSettings):
    """The app runtime role's DSN - ``nptc_app_login`` in tests, an
    equivalent least-privilege role in a deployment."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "database_url")


class MigrationSettings(BaseSettings):
    """The owning role's DSN Alembic runs migrations as - see
    ``backend/migrations/env.py``. Deliberately separate from
    ``DatabaseSettings``: nothing about running a migration should depend
    on ``NPTC_DATABASE_URL`` being set."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    migration_database_url: str

    @field_validator("migration_database_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "migration_database_url")
