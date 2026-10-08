"""`EntryDetail.snomed_synonyms` over real HTTP: the live SNOMED CT synonyms on an entry's detail.

`nptc.terminology.synonyms` has the filter and cache rules as unit tests
(`test_terminology_synonyms.py`). This module proves what the routes do with them: the field's
three states, that a terminology failure never fails the page (FR-54), that the cache spans
requests and URL forms, and that no other route makes the call.

Marked `integration`: the entries are real rows (NFR-39).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.api.dependencies import get_snomed_synonym_source
from nptc.terminology.synonyms import SnomedSynonymSource
from nptc_shared.terminology import (
    SNOMED_SYSTEM,
    Designation,
    LookupResult,
    Operation,
    StubTerminologyClient,
    TerminologyStatusError,
    TerminologyTimeoutError,
    TerminologyTransportError,
)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
_seed = _load("public_catalogue_support")

ApiTestApp = _api_support.ApiTestApp
SeededCatalogue = _seed.SeededCatalogue

_SYNONYM_USE = "900000000000013009"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from _api_support.build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return _seed.seed_public_catalogue(api.session)


def _synonym(value: str) -> Designation:
    return Designation(value=value, language="en", use_system=SNOMED_SYSTEM, use_code=_SYNONYM_USE)


def _seed_active_code_lookup(api: ApiTestApp, *synonyms: str) -> None:
    api.terminology.seed_lookup(
        _seed.ACTIVE_CODE,
        LookupResult(
            code=_seed.ACTIVE_CODE,
            system=SNOMED_SYSTEM,
            display=_seed.ACTIVE_DISPLAY_TERM,
            designations=(
                Designation(
                    value=_seed.ACTIVE_FSN,
                    language="en",
                    use_system=SNOMED_SYSTEM,
                    use_code="900000000000003001",
                ),
                *(_synonym(value) for value in synonyms),
            ),
        ),
    )


def _lookups(api: ApiTestApp) -> int:
    return sum(request.operation is Operation.LOOKUP for request in api.terminology.requests)


@pytest.mark.req("FR-53")
@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_detail_serves_the_active_bindings_synonyms_to_an_anonymous_caller(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    _seed_active_code_lookup(api, "AFB microscopy", _seed.ACTIVE_DISPLAY_TERM, "AFB microscopy")

    response = api.get(f"/catalogue/entries/{seeded.canonical}")

    assert response.status_code == 200, response.text
    assert response.json()["snomed_synonyms"] == {
        "status": "available",
        "terms": ["AFB microscopy"],
        "label_provenance": {"designation": "synonym", "semantic_tag": "not_applicable"},
    }


@pytest.mark.req("FR-53")
@pytest.mark.integration
def test_a_concept_with_no_synonyms_is_available_and_empty(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    _seed_active_code_lookup(api)

    body = api.get(f"/catalogue/entries/{seeded.canonical}").json()

    assert body["snomed_synonyms"]["status"] == "available"
    assert body["snomed_synonyms"]["terms"] == []


def _load_detail_with_working_server(api: ApiTestApp, seeded: SeededCatalogue) -> Any:
    """The same entry's detail through a fresh synonym cache over a healthy stub, for
    comparing every stored field against the degraded response."""
    healthy_client = StubTerminologyClient()
    healthy_client.seed_lookup(
        _seed.ACTIVE_CODE,
        LookupResult(code=_seed.ACTIVE_CODE, system=SNOMED_SYSTEM, display=None),
    )
    failing_source = api.app.dependency_overrides[get_snomed_synonym_source]
    api.app.dependency_overrides[get_snomed_synonym_source] = lambda: SnomedSynonymSource(
        healthy_client
    )
    try:
        return api.get(f"/catalogue/entries/{seeded.canonical}").json()
    finally:
        api.app.dependency_overrides[get_snomed_synonym_source] = failing_source


@pytest.mark.req("FR-54")
@pytest.mark.integration
@pytest.mark.parametrize(
    "error",
    [
        TerminologyTimeoutError("timed out"),
        TerminologyStatusError("service unavailable", status_code=503),
        TerminologyTransportError("connection refused"),
    ],
    ids=["timeout", "503", "transport"],
)
def test_a_terminology_failure_still_serves_the_entry_with_synonyms_unavailable(
    api: ApiTestApp, seeded: SeededCatalogue, error: Exception
) -> None:
    """FR-54's principal failure mode: the server is down or slow, and the page must still
    open with every stored row, saying only that the synonyms could not be loaded."""
    api.terminology.seed_error(Operation.LOOKUP, error)
    healthy = _load_detail_with_working_server(api, seeded)

    response = api.get(f"/catalogue/entries/{seeded.canonical}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["snomed_synonyms"] == {
        "status": "unavailable",
        "terms": [],
        "label_provenance": {"designation": "synonym", "semantic_tag": "not_applicable"},
    }
    del body["snomed_synonyms"], healthy["snomed_synonyms"]
    assert body == healthy


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_repeat_views_across_every_url_form_make_one_upstream_request(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    _seed_active_code_lookup(api, "AFB microscopy")

    bodies = [
        api.get(f"/catalogue/entries/{seeded.canonical}").json(),
        api.get(f"/catalogue/entries/{seeded.canonical}").json(),
        api.get(f"/catalogue/code/sct/{_seed.ACTIVE_CODE}").json(),
        api.get(
            "/catalogue/lookup", params={"system": SNOMED_SYSTEM, "code": _seed.ACTIVE_CODE}
        ).json(),
    ]

    assert _lookups(api) == 1
    assert all(body == bodies[0] for body in bodies)


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_a_failure_is_cached_so_a_down_server_is_not_asked_on_every_view(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    api.terminology.seed_error(Operation.LOOKUP, TerminologyTransportError("connection refused"))

    for _ in range(3):
        assert api.get(f"/catalogue/entries/{seeded.canonical}").status_code == 200

    assert _lookups(api) == 1


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_an_entry_with_no_active_binding_serves_null_and_asks_nothing(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    response = api.get(f"/catalogue/entries/{seeded.accented}")

    assert response.status_code == 200, response.text
    assert response.json()["snomed_synonyms"] is None
    assert api.terminology.requests == ()


@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_list_and_search_make_no_terminology_call(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    assert api.get("/catalogue/entries", params={"limit": 50}).status_code == 200
    assert api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM}).status_code == 200

    assert api.terminology.requests == ()


@pytest.mark.req("FR-53")
@pytest.mark.integration
def test_the_admin_detail_carries_the_same_field(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    _seed_active_code_lookup(api, "AFB microscopy")
    token = api.admin_token(subject="sub-admin-snomed-synonyms")

    response = api.get(f"/catalogue/admin/entries/{seeded.canonical}", token=token)

    assert response.status_code == 200, response.text
    assert response.json()["snomed_synonyms"]["terms"] == ["AFB microscopy"]
