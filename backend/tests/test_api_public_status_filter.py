"""Only `active` entries are public - on every endpoint (issue #142, FR-20).

A separate module, and parametrised over every endpoint *and* every hidden
status, because this is the requirement most easily broken by accident: the
status filter is a `where` clause that has to be repeated, and an endpoint
added later without it looks exactly like one with it. `nptc.catalogue.
queries.PUBLIC_STATUSES` is imported rather than the string `"active"` being
retyped here, so widening the public set is a one-line change that this test
follows rather than contradicts.

**Why `deprecated` is hidden too, not served with a flag.** The FR-20
surface is what a vendor builds a request form from. A deprecated entry is
precisely one they must stop offering, and "present in the API with a status
field they may or may not read" is a weaker guarantee than "absent". A
client that wants deprecation history is asking for the release/history
surface (#141), not the current catalogue.

**Why a hidden entry is a 404 and not a 403.** A 403 confirms the key
exists, which for a `draft` entry discloses unpublished editorial work - and
the disclosure is worse than useless to the caller, who can do nothing with
it either way.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.catalogue.queries import PUBLIC_STATUSES
from nptc.db.models.catalogue_entry import CatalogueEntryStatus


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
SeededCatalogue = _seed.SeededCatalogue

#: Every sub-resource path, as a format string. Each one resolves the entry
#: through `queries.get_entry`, so each one is a place the status filter
#: could have been omitted.
_DETAIL_PATHS = (
    "/catalogue/entries/{key}",
    "/catalogue/entries/{key}/designations",
    "/catalogue/entries/{key}/bindings",
    "/catalogue/entries/{key}/properties",
)


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return seed_public_catalogue(api.session)


def test_public_statuses_is_exactly_active() -> None:
    """A guard on the constant itself, with no container. Every other test
    in this module is parametrised over what `PUBLIC_STATUSES` excludes, so
    if the constant silently grew to include `deprecated` those tests would
    start asserting the wrong thing while still passing."""
    assert set(PUBLIC_STATUSES) == {CatalogueEntryStatus.ACTIVE.value}
    assert len(PUBLIC_STATUSES) == 1


def test_every_non_public_status_is_covered_by_this_module() -> None:
    """Exhaustiveness, so adding a fifth `CatalogueEntryStatus` fails here
    rather than shipping an untested visibility rule. Deliberately checked
    against the enum, not against a list retyped in this module."""
    hidden_statuses = {
        status.value for status in CatalogueEntryStatus if status.value not in PUBLIC_STATUSES
    }
    assert hidden_statuses == {"draft", "deprecated", "withdrawn"}


@pytest.mark.req("FR-20")
@pytest.mark.integration
@pytest.mark.parametrize("status", ["draft", "deprecated", "withdrawn"])
@pytest.mark.parametrize("path", _DETAIL_PATHS)
def test_a_non_public_entry_is_reported_as_absent(
    api: ApiTestApp, seeded: SeededCatalogue, status: str, path: str
) -> None:
    key = {
        "draft": seeded.draft,
        "deprecated": seeded.deprecated,
        "withdrawn": seeded.withdrawn,
    }[status]

    response = api.get(path.format(key=key))

    assert response.status_code == 404, response.text
    # Indistinguishable from a key that was never minted - including the
    # detail string, which is what actually makes it indistinguishable.
    never_minted = api.get(path.format(key=_seed.unused_business_key()))
    assert response.json() == never_minted.json()


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_no_non_public_entry_appears_in_the_list(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    response = api.get("/catalogue/entries", params={"after": seeded.before_all, "limit": 200})

    assert response.status_code == 200, response.text
    keys = {item["business_key"] for item in response.json()["items"]}
    assert keys == set(seeded.active_in_key_order)
    assert not keys & set(seeded.hidden)


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_no_non_public_entry_appears_in_search_results(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Searched by a term the hidden entries actually match, or this test
    would pass for the wrong reason - the failure mode of every
    absence assertion. The three hidden fixtures carry near-copies of the
    canonical entry's preferred term precisely so this query scores them
    above the threshold, and the positive assertion below proves the query
    matched something rather than the endpoint returning nothing at all."""
    response = api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM, "limit": 200})

    assert response.status_code == 200, response.text
    keys = {item["business_key"] for item in response.json()["items"]}
    assert seeded.canonical in keys, "the query matched nothing, so absence proves nothing"
    assert not keys & set(seeded.hidden)
