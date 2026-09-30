"""`ApiSettings` reaches every route through one object, resolved once by
`create_app` (FR-86, FR-98).

Before this, `create_app(settings=...)` used the injected settings for CORS
only. Routes resolved a second, env-read `lru_cache`d instance through
`get_api_settings`, so a caller that injected settings was silently
half-configured.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Connection

from nptc.api.app import create_app
from nptc.api.dependencies import get_api_settings
from nptc.api.labels import LabelProvenance
from nptc.api.routers import catalogue_shared
from nptc.settings import ApiSettings


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
_seed = _load("public_catalogue_support")

build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    """`get_api_settings` is process-wide, so a value cached by an earlier
    test must not decide this one, and this one must not decide the next."""
    get_api_settings.cache_clear()
    yield
    get_api_settings.cache_clear()


@pytest.mark.req("FR-86")
def test_create_app_installs_the_injected_settings_as_the_dependency_override() -> None:
    injected = _api_support.hermetic_api_settings()

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


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_routes_ignore_an_invalid_env_value_when_settings_are_injected(
    api: ApiTestApp, hostile_api_settings_env: None
) -> None:
    """The bug this closes: `NPTC_FSN_SEMANTIC_TAG=stripped` makes a fresh
    `ApiSettings()` raise, so any route still resolving its own env-read
    instance answered 500 even though the app was built with good settings."""
    seeded = _seed.seed_public_catalogue(api.session)

    bindings = api.get(f"/catalogue/entries/{seeded.canonical}/bindings")
    detail = api.get(f"/catalogue/entries/{seeded.canonical}")

    assert bindings.status_code == 200, bindings.text
    assert detail.status_code == 200, detail.text


@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_one_request_reads_exactly_one_settings_object(
    api: ApiTestApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every consumer of `ApiSettings` inside one request receives the object
    the factory installed - not an equal copy, not a second instance."""
    seeded = _seed.seed_public_catalogue(api.session)
    injected = api.set_api_settings(max_preferred_term_length=42)
    seen: list[ApiSettings] = []
    original: Callable[[ApiSettings], LabelProvenance] = catalogue_shared.fsn_provenance

    def _record(settings: ApiSettings) -> LabelProvenance:
        seen.append(settings)
        return original(settings)

    monkeypatch.setattr(catalogue_shared, "fsn_provenance", _record)

    response = api.get(f"/catalogue/entries/{seeded.canonical}")

    assert response.status_code == 200, response.text
    assert len(response.json()["bindings"]) >= 2
    assert seen, "the binding assembler never asked for settings"
    assert all(settings is injected for settings in seen)


@pytest.mark.req("FR-86")
def test_hermetic_settings_reject_a_mistyped_field_name() -> None:
    """Otherwise `max_preferred_term_lenght=20` is dropped and a length-warning
    test asserts the unset behaviour while passing for the wrong reason."""
    with pytest.raises(ValidationError, match="max_preferred_term_lenght"):
        _api_support.hermetic_api_settings(max_preferred_term_lenght=20)


@pytest.mark.req("FR-86")
@pytest.mark.integration
def test_set_api_settings_keeps_the_fields_it_is_not_given(app_db: Connection) -> None:
    configured = _api_support.hermetic_api_settings(max_preferred_term_length=9)

    for api in build_api_test_app(app_db, api_settings=configured):
        replaced = api.set_api_settings(frontend_base_url="https://other.example")

        assert replaced.max_preferred_term_length == 9
        assert replaced.frontend_base_url == "https://other.example"
        assert api.app.dependency_overrides[get_api_settings]() is replaced
