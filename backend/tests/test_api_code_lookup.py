"""`GET /catalogue/code/{system_token}/{code}` and `GET /catalogue/lookup` -
FR-17's exact-code lookup routes (issue #140).

The by-business-key form (`GET /catalogue/entries/{business_key}`) has its
own coverage in `test_api_public_catalogue.py`; this module is the two
routes FR-17 adds on top of it, plus the one acceptance criterion that ties
all three together - byte-identical bodies for the same entry.

Status filtering's general "hidden entry is reported as absent" shape has
its own module for the by-business-key routes
(`test_api_public_status_filter.py`); this module's own hidden-status test
below is a code-lookup-specific instance of the same rule, not a
duplicate - `_DETAIL_PATHS` there has no `{system_token}`/`{code}` slot to
parametrise over.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from sqlalchemy.engine import Connection

from nptc.db.models.code_binding import SNOMED_CT_SYSTEM


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
seed_public_catalogue = _seed.seed_public_catalogue
seed_code_lookup_fixtures = _seed.seed_code_lookup_fixtures
SeededCatalogue = _seed.SeededCatalogue
SeededCodeLookup = _seed.SeededCodeLookup

_ENCODED_SNOMED_CT_SYSTEM = quote(SNOMED_CT_SYSTEM, safe="")


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return seed_public_catalogue(api.session)


@pytest.fixture
def code_lookup(api: ApiTestApp) -> SeededCodeLookup:
    return seed_code_lookup_fixtures(api.session)


# --- the three route forms agree (FR-17's core acceptance criterion) ------


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_all_three_route_forms_resolve_the_same_entry_byte_identically(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    by_business_key = api.get(f"/catalogue/entries/{seeded.canonical}")
    by_code = api.get(f"/catalogue/code/sct/{_seed.ACTIVE_CODE}")
    by_lookup = api.get(
        "/catalogue/lookup", params={"system": SNOMED_CT_SYSTEM, "code": _seed.ACTIVE_CODE}
    )

    assert by_business_key.status_code == 200, by_business_key.text
    assert by_code.status_code == 200, by_code.text
    assert by_lookup.status_code == 200, by_lookup.text
    assert by_business_key.text == by_code.text
    assert by_business_key.text == by_lookup.text


# --- FR-06: a code round-trips through the URL as a string, unchanged -----


@pytest.mark.req("FR-06")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("code", "attr"),
    [
        ("LEADING_ZERO_CODE", "leading_zero_entry"),
        ("EIGHTEEN_DIGIT_CODE", "eighteen_digit_entry"),
    ],
)
def test_code_round_trips_through_both_url_forms_unchanged(
    api: ApiTestApp, code_lookup: SeededCodeLookup, code: str, attr: str
) -> None:
    """Asserted against raw response text, not a parsed model - a parsed
    model would already have survived the trip through Python's own `int`
    by the time this test ever saw it."""
    value = getattr(_seed, code)
    expected_entry = getattr(code_lookup, attr)

    by_code = api.get(f"/catalogue/code/sct/{value}")
    by_lookup = api.get("/catalogue/lookup", params={"system": SNOMED_CT_SYSTEM, "code": value})

    for response in (by_code, by_lookup):
        assert response.status_code == 200, response.text
        assert response.json()["business_key"] == expected_entry
        # Quoted in the raw response text - a parsed model would already
        # have survived the trip through Python's own `int` by the time
        # this test ever saw it, which is exactly what FR-06 forbids.
        assert f'"{value}"' in response.text


# --- the malformed-token / unregistered-token / unregistered-URI shapes ---


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_malformed_system_token_is_422_before_any_query_runs(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Uppercase fails `SYSTEM_TOKEN_PATTERN` - a path-pattern 422, matching
    `BusinessKeyPath`'s own precedent, never the "well-formed but
    unregistered" 404 the next test covers."""
    response = api.get(f"/catalogue/code/SCT/{_seed.ACTIVE_CODE}")

    assert response.status_code == 422, response.text


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_unregistered_system_token_is_404_naming_registered_tokens(api: ApiTestApp) -> None:
    response = api.get(f"/catalogue/code/loinc/{_seed.UNUSED_CODE}")

    assert response.status_code == 404, response.text
    assert "sct" in response.json()["detail"]


@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_unregistered_lookup_system_uri_is_404_on_the_same_sentence(api: ApiTestApp) -> None:
    unregistered_token = api.get(f"/catalogue/code/loinc/{_seed.UNUSED_CODE}")
    unregistered_uri = api.get(
        "/catalogue/lookup", params={"system": "http://loinc.org", "code": _seed.UNUSED_CODE}
    )

    assert unregistered_uri.status_code == 404, unregistered_uri.text
    assert unregistered_uri.json() == unregistered_token.json()


@pytest.mark.req("FR-17")
@pytest.mark.integration
@pytest.mark.parametrize(
    "params",
    [
        {"code": "123"},
        {"system": SNOMED_CT_SYSTEM},
        {"system": "", "code": "123"},
        {"system": SNOMED_CT_SYSTEM, "code": ""},
    ],
)
def test_lookup_missing_or_blank_system_or_code_is_422(
    api: ApiTestApp, params: dict[str, str]
) -> None:
    response = api.get("/catalogue/lookup", params=params)

    assert response.status_code == 422, response.text


# --- FR-08: a retired binding still resolves ------------------------------


@pytest.mark.req("FR-08")
@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_retired_only_code_resolves_with_its_binding_flagged_retired(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    response = api.get(f"/catalogue/code/sct/{_seed.RETIRED_CODE}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_key"] == seeded.canonical
    matched = next(b for b in body["bindings"] if b["code"] == _seed.RETIRED_CODE)
    assert matched["status"] == "retired"
    assert matched["retirement_reason"]
    assert matched["replaced_by_code"] == _seed.ACTIVE_CODE


@pytest.mark.req("FR-08")
@pytest.mark.req("FR-17")
@pytest.mark.integration
def test_retired_code_collision_resolves_to_the_most_recently_retired_entry(
    api: ApiTestApp, code_lookup: SeededCodeLookup
) -> None:
    """Two different entries each retired the same code, with no active
    binding anywhere - `ix_code_binding_one_active_entry_per_code` cannot
    prevent this (it is scoped to `status = 'active'`). The most recently
    retired one wins, deterministically."""
    response = api.get(f"/catalogue/code/sct/{_seed.RETIRED_COLLISION_CODE}")

    assert response.status_code == 200, response.text
    assert response.json()["business_key"] == code_lookup.retired_collision_newer_entry


# --- FR-20's non-disclosure rule, applied to code lookup ------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_hidden_status_code_404s_identically_to_a_never_bound_code(
    api: ApiTestApp, code_lookup: SeededCodeLookup
) -> None:
    hidden = api.get(f"/catalogue/code/sct/{_seed.HIDDEN_ONLY_CODE}")
    never_bound = api.get(f"/catalogue/code/sct/{_seed.UNUSED_CODE}")

    assert hidden.status_code == 404, hidden.text
    assert hidden.json() == never_bound.json()


# --- FR-44: the negative permission case gets its own test ----------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_anonymous_callers_are_served_and_bad_credentials_still_refused(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Mirrors `test_api_public_catalogue.py`'s own test of the same shape
    for the by-business-key route: `Role.ANON` holds `CATALOGUE_BROWSE`, so
    an anonymous caller is served, but a *bad* credential is refused rather
    than silently downgraded to the anonymous view."""
    anonymous_code = api.get(f"/catalogue/code/sct/{_seed.ACTIVE_CODE}")
    garbage_code = api.get(f"/catalogue/code/sct/{_seed.ACTIVE_CODE}", token="not-a-jwt")
    anonymous_lookup = api.get(
        "/catalogue/lookup", params={"system": SNOMED_CT_SYSTEM, "code": _seed.ACTIVE_CODE}
    )
    garbage_lookup = api.get(
        "/catalogue/lookup",
        params={"system": SNOMED_CT_SYSTEM, "code": _seed.ACTIVE_CODE},
        token="not-a-jwt",
    )

    assert anonymous_code.status_code == 200, anonymous_code.text
    assert garbage_code.status_code == 401, garbage_code.text
    assert anonymous_lookup.status_code == 200, anonymous_lookup.text
    assert garbage_lookup.status_code == 401, garbage_lookup.text
