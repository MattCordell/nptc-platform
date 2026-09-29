"""`ApiSettings` reaches every route through one object, resolved once by
`create_app` (FR-86, FR-98).

Before this, `create_app(settings=...)` used the injected settings for CORS
only. Routes resolved a second, env-read `lru_cache`d instance through
`get_api_settings`, so a caller that injected settings was silently
half-configured.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from nptc.api.app import create_app
from nptc.api.dependencies import get_api_settings
from nptc.settings import ApiSettings


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    """`get_api_settings` is process-wide, so a value cached by an earlier
    test must not decide this one, and this one must not decide the next."""
    get_api_settings.cache_clear()
    yield
    get_api_settings.cache_clear()


@pytest.mark.req("FR-86")
def test_create_app_installs_the_injected_settings_as_the_dependency_override() -> None:
    injected = ApiSettings(frontend_base_url="http://localhost:5173")

    app = create_app(settings=injected)

    assert app.dependency_overrides[get_api_settings]() is injected


@pytest.mark.req("FR-86")
def test_create_app_without_settings_installs_the_process_wide_settings() -> None:
    app = create_app()

    assert app.dependency_overrides[get_api_settings]() is get_api_settings()


@pytest.mark.req("FR-98")
def test_create_app_fails_at_startup_on_a_bad_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The principal failure mode: `lru_cache` does not cache a raised
    exception, so a lazily-resolved bad value would be a 500 on every
    request. Resolved in the factory it is a start-up failure instead."""
    monkeypatch.setenv("NPTC_FSN_SEMANTIC_TAG", "stripped")

    with pytest.raises(ValidationError, match="fsn_semantic_tag"):
        create_app()
