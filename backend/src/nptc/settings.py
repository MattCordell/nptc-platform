"""Backend runtime configuration (issue #33).

``pydantic-settings``, not the explicit ``os.environ`` reads
``nptc_shared.terminology.config`` uses (ADR-0003): ``pydantic-settings`` is
declared as a backend-only dependency and this is its first consumer, so
there is no cross-package reason to match that module's approach here.

Both DSNs are required fields with no default, mirroring
``nptc_shared.terminology.config``'s "raise naming the variable, never
silently default" convention: a missing or empty value fails loudly, naming
the field, rather than falling back to a placeholder a misconfigured
deployment could run against for a while before anyone notices.
"""

from __future__ import annotations

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from ``NPTC_``-prefixed environment variables.

    ``database_url`` is the app runtime role's DSN (``nptc_app_login`` in
    tests, an equivalent least-privilege role in a deployment). ``migration_
    database_url`` is the owning role's DSN, used only by Alembic - see
    ``backend/migrations/env.py``.
    """

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="ignore")

    database_url: str
    migration_database_url: str

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _not_empty(cls, value: str, info: ValidationInfo) -> str:
        if not value:
            raise ValueError(f"{info.field_name} must not be empty")
        return value
