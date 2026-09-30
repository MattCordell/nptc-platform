"""`ApiSettings` for tests that must not depend on the developer's environment.

Not a `test_*.py` module - loaded by path via `importlib`, like
`api_app_support.py`, which re-exports `hermetic_api_settings`.
"""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from nptc.settings import ApiSettings


class _HermeticApiSettings(ApiSettings):
    """Reads constructor arguments and nothing else: no `NPTC_*` variable and
    no `.env`. `extra="forbid"` so a mistyped field name fails instead of
    silently leaving the default in place."""

    model_config = SettingsConfigDict(env_prefix="NPTC_", extra="forbid")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def hermetic_api_settings(**fields: Any) -> ApiSettings:
    """Field defaults plus `fields`; never the process environment. Unlike
    passing every field explicitly, a field added to `ApiSettings` later is
    covered by its own default rather than by editing each caller."""
    return _HermeticApiSettings(**fields)
