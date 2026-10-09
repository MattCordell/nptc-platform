"""Facets on the browse route, and the two facets that list every value (FR-16, ADR-0032).

The catalogue page offers Discipline and Specimen as type-to-narrow comboboxes before the
visitor has searched, so `GET /catalogue/entries?facets=true` returns counts over the
whole filtered catalogue, and those two facets are never cut at `FACET_BUCKET_CAP`.

Every value here carries a per-test token, and every assertion is about the buckets this test
created: the Postgres container is shared, so the response also holds whatever else the
database contains.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.catalogue.facets import FACET_BUCKET_CAP
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.property_value import PropertyValue


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp

_DISCIPLINE_SYSTEM = "https://example.org/nptc/discipline"
_SUBGROUP_SYSTEM = "https://example.org/nptc/subgroup"
_SNOMED = "http://snomed.info/sct"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@dataclass(frozen=True)
class _Seeded:
    token: str
    before_all: str
    first: str
    second: str
    third: str
    draft: str

    def code(self, name: str) -> str:
        return f"{self.token}-{name}"


def _coded(system: str, code: str) -> dict[str, str]:
    return {"system": system, "code": code, "display": f"Label {code}"}


def _add_values(
    session: Any, entry: CatalogueEntry, key: str, system: str, codes: list[str]
) -> None:
    session.add_all(
        PropertyValue(
            entry_id=entry.id,
            property_key=key,
            ordinal=ordinal,
            value=_coded(system, code),
        )
        for ordinal, code in enumerate(codes)
    )


@pytest.fixture
def seeded(api: ApiTestApp) -> _Seeded:
    """Three active entries and one draft, in a nine-digit key block so `before_all` scopes a
    listing to them.

    `first` and `second` carry discipline `chem`, `third` carries `haem`. Specimens: `first`
    holds `urine` and `serum`, `second` holds `serum`, `third` holds `urine`. The draft holds
    `chem` and `urine` and must be counted nowhere."""
    session = api.session
    seed_system_properties(session)
    base = random.randrange(100_000_000, 999_000_000)
    seeded = _Seeded(
        token=f"bf{base}",
        before_all=f"NPTC-{base - 1}",
        first=f"NPTC-{base}",
        second=f"NPTC-{base + 1}",
        third=f"NPTC-{base + 2}",
        draft=f"NPTC-{base + 3}",
    )
    entries = {
        seeded.first: CatalogueEntry(
            business_key=seeded.first,
            preferred_term=f"Browse facets {seeded.token} first",
            status=CatalogueEntryStatus.ACTIVE.value,
        ),
        seeded.second: CatalogueEntry(
            business_key=seeded.second,
            preferred_term=f"Browse facets {seeded.token} second",
            status=CatalogueEntryStatus.ACTIVE.value,
        ),
        seeded.third: CatalogueEntry(
            business_key=seeded.third,
            preferred_term=f"Browse facets {seeded.token} third",
            status=CatalogueEntryStatus.ACTIVE.value,
        ),
        seeded.draft: CatalogueEntry(
            business_key=seeded.draft,
            preferred_term=f"Browse facets {seeded.token} draft",
            status=CatalogueEntryStatus.DRAFT.value,
        ),
    }
    session.add_all(entries.values())
    session.flush()
    chem, haem = seeded.code("chem"), seeded.code("haem")
    urine, serum = seeded.code("urine"), seeded.code("serum")
    for business_key, disciplines, specimens in (
        (seeded.first, [chem], [urine, serum]),
        (seeded.second, [chem], [serum]),
        (seeded.third, [haem], [urine]),
        (seeded.draft, [chem], [urine]),
    ):
        _add_values(session, entries[business_key], "discipline", _DISCIPLINE_SYSTEM, disciplines)
        _add_values(session, entries[business_key], "specimen", _SNOMED, specimens)
    session.flush()
    return seeded


def _browse(api: ApiTestApp, **params: Any) -> dict[str, Any]:
    response = api.get("/catalogue/entries", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _counts(body: dict[str, Any], facet_key: str, codes: list[str]) -> dict[str, int]:
    """The counts of just these buckets, so rows other tests committed stay out of the
    assertion."""
    facet = next(f for f in body["facets"] if f["key"] == facet_key)
    return {b["value"]: b["count"] for b in facet["buckets"] if b["value"] in codes}


def _bucket_values(body: dict[str, Any], facet_key: str) -> list[str]:
    facet = next(f for f in body["facets"] if f["key"] == facet_key)
    return [b["value"] for b in facet["buckets"]]


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_browse_with_facets_counts_the_active_catalogue(api: ApiTestApp, seeded: _Seeded) -> None:
    body = _browse(api, facets="true", limit=1)

    chem, haem = seeded.code("chem"), seeded.code("haem")
    urine, serum = seeded.code("urine"), seeded.code("serum")
    # The draft carries chem and urine and counts for neither.
    assert _counts(body, "discipline", [chem, haem]) == {chem: 2, haem: 1}
    assert _counts(body, "specimen", [urine, serum]) == {urine: 2, serum: 2}
    # Counts are over the whole catalogue, not the one-row page.
    assert len(body["items"]) == 1


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_browse_facets_are_ordered_by_count_then_value(api: ApiTestApp, seeded: _Seeded) -> None:
    body = _browse(api, facets="true")

    chem, haem = seeded.code("chem"), seeded.code("haem")
    values = [v for v in _bucket_values(body, "discipline") if v in (chem, haem)]
    assert values == [chem, haem]


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_browse_facets_follow_the_filters_and_exclude_a_facet_s_own_selection(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    chem, haem = seeded.code("chem"), seeded.code("haem")
    urine, serum = seeded.code("urine"), seeded.code("serum")

    body = _browse(api, facets="true", **{"filter.discipline": chem})

    # Discipline still offers `haem`, because its own selection is left out of its counts.
    assert _counts(body, "discipline", [chem, haem]) == {chem: 2, haem: 1}
    # Specimen counts only the entries `chem` leaves: first (urine, serum) and second (serum).
    assert _counts(body, "specimen", [urine, serum]) == {urine: 1, serum: 2}


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_bucket_s_count_is_what_the_browse_route_returns_for_it(
    api: ApiTestApp, seeded: _Seeded
) -> None:
    body = _browse(api, facets="true")
    serum = seeded.code("serum")

    listed = _browse(api, after=seeded.before_all, **{"filter.specimen": serum})

    assert _counts(body, "specimen", [serum]) == {serum: len(listed["items"])}


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_browse_without_facets_computes_none(api: ApiTestApp, seeded: _Seeded) -> None:
    assert _browse(api, after=seeded.before_all)["facets"] is None
    assert _browse(api, after=seeded.before_all, facets="false")["facets"] is None


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_browse_with_facets_still_refuses_a_bad_filter(api: ApiTestApp, seeded: _Seeded) -> None:
    """Asking for facets must not soften the filter contract: an unknown or non-filterable key
    is a 422, never a quietly unfiltered page."""
    unknown = api.get(
        "/catalogue/entries", params={"facets": "true", "filter.no_such_property": "x"}
    )
    assert unknown.status_code == 422, unknown.text

    not_filterable = api.get(
        "/catalogue/entries", params={"facets": "true", "filter.usage_guidance": "x"}
    )
    assert not_filterable.status_code == 422, not_filterable.text


# --- the facets that list every value --------------------------------------


@dataclass(frozen=True)
class _Wide:
    """One entry carrying more distinct values than `FACET_BUCKET_CAP` for two facets: the
    uncapped specimen and the capped subgroup."""

    business_key: str
    term: str
    specimens: tuple[str, ...]
    subgroups: tuple[str, ...]


@pytest.fixture
def wide(api: ApiTestApp) -> _Wide:
    session = api.session
    seed_system_properties(session)
    base = random.randrange(100_000_000, 999_000_000)
    token = f"wide{base}"
    wide = _Wide(
        business_key=f"NPTC-{base}",
        term=f"Wide facet probe {token}",
        specimens=tuple(f"{token}-s{n:02d}" for n in range(FACET_BUCKET_CAP + 5)),
        subgroups=tuple(f"{token}-g{n:02d}" for n in range(FACET_BUCKET_CAP + 5)),
    )
    entry = CatalogueEntry(
        business_key=wide.business_key,
        preferred_term=wide.term,
        status=CatalogueEntryStatus.ACTIVE.value,
    )
    session.add(entry)
    session.flush()
    _add_values(session, entry, "specimen", _SNOMED, list(wide.specimens))
    _add_values(session, entry, "subgroup", _SUBGROUP_SYSTEM, list(wide.subgroups))
    session.flush()
    return wide


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_specimen_lists_every_bucket_on_browse(api: ApiTestApp, wide: _Wide) -> None:
    body = _browse(api, facets="true", limit=1)

    facet = next(f for f in body["facets"] if f["key"] == "specimen")
    assert facet["truncated"] is False
    assert set(wide.specimens) <= {b["value"] for b in facet["buckets"]}
    assert len(facet["buckets"]) > FACET_BUCKET_CAP


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_specimen_lists_every_bucket_on_search_too(api: ApiTestApp, wide: _Wide) -> None:
    """The page asks `/catalogue/search` for its facets once a term is typed, so the combobox
    must be as complete there as on browse."""
    response = api.get("/catalogue/search", params={"q": wide.term})
    assert response.status_code == 200, response.text
    facet = next(f for f in response.json()["facets"] if f["key"] == "specimen")

    assert facet["truncated"] is False
    assert set(wide.specimens) <= {b["value"] for b in facet["buckets"]}


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_facet_outside_the_uncapped_set_still_truncates(api: ApiTestApp, wide: _Wide) -> None:
    """The lift is by key, not a change to the cap for every facet: `subgroup` has the same
    number of values on the same entry and is still cut."""
    body = _browse(api, facets="true")

    facet = next(f for f in body["facets"] if f["key"] == "subgroup")
    assert facet["truncated"] is True
    assert len(facet["buckets"]) == FACET_BUCKET_CAP
