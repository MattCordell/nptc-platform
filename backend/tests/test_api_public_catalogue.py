"""`GET /api/v1/catalogue/*` - the FR-20 public read API (issue #142).

Paging, the detail response, and each sub-resource, over real HTTP against
the real app. Status filtering has its own module
(`test_api_public_status_filter.py`), search has
`test_api_public_search.py`, and the no-leak acceptance criteria have
`test_api_public_response_hygiene.py` - kept apart because each answers a
different question and a single module would make it easy to think the
whole surface is covered when only the happy path is.

Every request here is made with **no** `Authorization` header. That is not
incidental to the tests: FR-20 requires the API be available without
authentication, so a fixture that quietly authenticated would leave the
requirement unproven.

Marked `integration`: these are queries against real tables with real
constraints (NFR-39).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection


def _load(name: str) -> Any:
    # `backend/tests` has no `__init__.py` (pytest's `--import-mode=
    # importlib`), so support modules are loaded by path. Registered in
    # sys.modules before exec_module - see test_authz_negative_http.py for
    # why @dataclass requires it.
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


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def seeded(api: ApiTestApp) -> SeededCatalogue:
    return seed_public_catalogue(api.session)


# --- paging (FR-20) --------------------------------------------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_paging_walks_every_entry_exactly_once(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    """The property that matters is not "a page comes back" but "walking
    the cursor visits every entry exactly once" - no overlap and no gap. A
    keyset that forgot to make the cursor exclusive repeats a row at every
    boundary, and one that overshot skips one; both return plausible pages.
    """
    visited: list[str] = []
    cursor: str | None = seeded.before_all
    for _ in range(10):  # a bound, so a broken cursor loops finitely
        params = {"limit": 2, "after": cursor}
        response = api.get("/catalogue/entries", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        visited.extend(item["business_key"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert cursor is None, "paging did not terminate"
    assert visited == list(seeded.active_in_key_order)
    assert len(visited) == len(set(visited))


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_cursor_past_the_end_is_an_empty_page_not_an_error(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The end of the collection is an ordinary state, not a 404: a client
    polling for new entries with a stored cursor is the normal case."""
    response = api.get(
        "/catalogue/entries", params={"after": seeded.active_in_key_order[-1], "limit": 50}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_next_cursor_is_null_on_a_page_that_exactly_fills_the_limit(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The off-by-one this endpoint is most likely to get wrong. Asking for
    exactly as many entries as remain must report the end, not hand back a
    cursor that leads to an empty page - a client that trusts a non-null
    cursor makes one pointless round trip per poll forever."""
    remaining = len(seeded.active_in_key_order)
    response = api.get(
        "/catalogue/entries", params={"after": seeded.before_all, "limit": remaining}
    )

    body = response.json()
    assert len(body["items"]) == remaining
    assert body["next_cursor"] is None


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_limit_above_the_ceiling_is_refused(api: ApiTestApp) -> None:
    """The principal failure mode of a paged endpoint: a caller asking for
    the whole table in one request. Refused, not silently clamped - a
    silently clamped limit makes the response a lie about what was asked
    for, and a client that pages by "did I get `limit` items" then stops
    early."""
    assert api.get("/catalogue/entries", params={"limit": 201}).status_code == 422
    assert api.get("/catalogue/entries", params={"limit": 0}).status_code == 422


# --- detail ---------------------------------------------------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_detail_carries_the_summary_fields_and_every_sub_resource(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    response = api.get(f"/catalogue/entries/{seeded.canonical}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_key"] == seeded.canonical
    assert body["preferred_term"] == _seed.CANONICAL_TERM
    # FR-85: the character count of the catalogue's own preferred term,
    # computed rather than stored.
    assert body["length"] == len(_seed.CANONICAL_TERM)
    assert body["status"] == "active"
    assert body["specimen_unconstrained"] is True
    assert body["designations"] and body["bindings"] and body["properties"]


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_unknown_business_key_is_a_404(api: ApiTestApp) -> None:
    response = api.get(f"/catalogue/entries/{_seed.unused_business_key()}")

    assert response.status_code == 404, response.text
    # The body says what to do, and does not echo the key back (the
    # convention `nptc.api.errors` sets for every detail string).
    assert "detail" in response.json()


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_uuid_in_the_path_is_rejected_as_malformed_not_looked_up(api: ApiTestApp) -> None:
    """422, not 404. A 404 would imply a UUID is a kind of identifier this
    API accepts and simply did not find - PRD SS6.2 says the internal id is
    not an identifier callers have at all."""
    response = api.get(f"/catalogue/entries/{_seed.a_uuid()}")

    assert response.status_code == 422, response.text


# --- sub-resources --------------------------------------------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_designations_endpoint_serves_active_designations_only(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """A retired synonym is editorial history, not a term the entry is
    known by - unlike a retired *binding*, which FR-08 requires be
    published."""
    response = api.get(f"/catalogue/entries/{seeded.canonical}/designations")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    terms = {item["term"] for item in items}
    assert _seed.CANONICAL_SYNONYM in terms
    # The non-en-AU preferred variant is present, and is `use=preferred`.
    assert {"preferred", "synonym"} <= {item["use"] for item in items}
    assert all(item["status"] == "active" for item in items)
    assert _seed.RETIRED_SYNONYM not in terms
    # ADR-0022: the catalogue's own en-AU preferred term is never a
    # designation row, so it must not appear here even though a client
    # needs it - it is on the entry.
    assert _seed.CANONICAL_TERM not in terms


@pytest.mark.req("FR-08")
@pytest.mark.integration
def test_retired_binding_is_served_with_its_reason_and_successor_code(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """FR-08's actual point: an implementer holding an inactivated code
    must be able to learn that, and learn what replaced it, from this API.
    `replaced_by_code` is a *code* - the database stores a UUID there, and
    resolving it is what stops that UUID reaching a client."""
    response = api.get(f"/catalogue/entries/{seeded.canonical}/bindings")

    assert response.status_code == 200, response.text
    by_code = {item["code"]: item for item in response.json()["items"]}
    assert set(by_code) == {_seed.ACTIVE_CODE, _seed.RETIRED_CODE}

    retired = by_code[_seed.RETIRED_CODE]
    assert retired["status"] == "retired"
    assert retired["retirement_reason"]
    assert retired["replaced_by_code"] == _seed.ACTIVE_CODE

    active = by_code[_seed.ACTIVE_CODE]
    assert active["status"] == "active"
    assert active["retirement_reason"] is None
    assert active["replaced_by_code"] is None


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_every_code_is_serialised_as_a_json_string(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The defect class the platform exists to eliminate, asserted on the
    parsed value as well as on the raw text elsewhere: a code that survived
    as a JSON number would arrive here as an `int`."""
    items = api.get(f"/catalogue/entries/{seeded.canonical}/bindings").json()["items"]

    assert items
    for item in items:
        assert isinstance(item["code"], str)
        assert item["replaced_by_code"] is None or isinstance(item["replaced_by_code"], str)


@pytest.mark.req("FR-83")
@pytest.mark.integration
def test_display_term_has_its_semantic_tag_stripped_exactly_once(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """PRD SS6.4's named regression case. `391483001`'s FSN carries two
    parenthesised groups and only the last is the semantic tag, so a strip
    applied twice - or applied to every group - silently yields
    `Microscopy`."""
    items = api.get(f"/catalogue/entries/{seeded.canonical}/bindings").json()["items"]
    active = next(item for item in items if item["code"] == _seed.ACTIVE_CODE)

    assert active["fsn"] == _seed.ACTIVE_FSN, "the stored FSN must be served exactly as stored"
    assert active["display_term"] == _seed.ACTIVE_DISPLAY_TERM
    assert active["display_term"] != "Microscopy"


@pytest.mark.req("FR-83")
@pytest.mark.integration
def test_an_unrenderable_stored_fsn_is_a_500_not_a_422(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """An FSN that is not a served FSN is a *server-side data* fault, and the
    status has to say so.

    The underlying refusal (`NotAServedFSNError`) is a validation error
    carrying `http_status = 422`, which is right on a write path where the
    caller supplied the FSN. Serving that same 422 here would be actively
    counterproductive: the request is a well-formed `GET` on a valid business
    key, so a vendor's client reads 422 as "I sent something wrong", does not
    retry, and files the problem against itself - while FR-83's entire reason
    for failing loudly is to get an administrator to look at the binding. So
    the read path reports 5xx.

    Both routes that render a `display_term` are asserted, because the strip
    happens in one shared helper and covering only the detail route would let
    the sub-resource regress unnoticed. The body still carries the fixed
    client-facing sentence and, per this module's no-leak rules, no internal
    identifier.
    """
    _seed.corrupt_stored_fsn(api.session, seeded.canonical)

    for path in (
        f"/catalogue/entries/{seeded.canonical}",
        f"/catalogue/entries/{seeded.canonical}/bindings",
    ):
        response = api.get(path)
        assert response.status_code == 500, f"{path}: {response.status_code} {response.text}"
        assert response.json()["detail"]
        assert _seed.CORRUPT_FSN not in response.text

    # And the sub-resources that do not render a display term are unaffected -
    # one corrupted binding must not take the whole entry's API down.
    assert api.get(f"/catalogue/entries/{seeded.canonical}/designations").status_code == 200
    assert api.get(f"/catalogue/entries/{seeded.canonical}/properties").status_code == 200


@pytest.mark.req("FR-77")
@pytest.mark.integration
def test_property_values_are_rendered_through_the_datatype_registry(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Multi-valued property values keep their `ordinal` and their order,
    and each value's `datatype` is carried so a client knows how to read
    it. A `positiveInt` arrives as a number and a `string` as a string -
    which is what proves the handler ran rather than the JSONB being echoed
    through some single generic path."""
    response = api.get(f"/catalogue/entries/{seeded.canonical}/properties")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    specimens = [item for item in items if item["key"] == seeded.specimen_property_key]
    assert [item["ordinal"] for item in specimens] == [0, 1]
    assert [item["value"] for item in specimens] == list(_seed.SPECIMEN_VALUES)
    assert {item["datatype"] for item in specimens} == {"string"}
    assert specimens[0]["label"] == "Specimen type"
    assert specimens[0]["cardinality"] == "0..*"

    volume = next(item for item in items if item["key"] == seeded.volume_property_key)
    assert volume["value"] == _seed.VOLUME_VALUE
    assert volume["datatype"] == "positiveInt"


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_an_entry_with_no_children_serves_empty_lists_not_nulls(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """A `null` where a client expects a list is the single most common
    cause of a crash in a generated client, and "this entry has no
    bindings yet" is an ordinary state for a real catalogue."""
    response = api.get(f"/catalogue/entries/{seeded.accented}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["designations"] == []
    assert body["bindings"] == []
    assert body["properties"] == []


# --- authentication posture (FR-20, NFR-20) -------------------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_anonymous_callers_are_served_and_bad_credentials_still_refused(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Both halves in one test, because either alone is misleading. FR-20
    requires no authentication; NFR-07 requires that presenting a *bad*
    credential is refused rather than silently downgraded to the public
    view, or a client could never tell a forged token from none."""
    anonymous = api.get(f"/catalogue/entries/{seeded.canonical}")
    with_garbage = api.get(f"/catalogue/entries/{seeded.canonical}", token="not-a-jwt")

    assert anonymous.status_code == 200, anonymous.text
    assert with_garbage.status_code == 401, with_garbage.text


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_malformed_entries_cursor_is_refused(api: ApiTestApp) -> None:
    """`/catalogue/entries` pages on `business_key`, so its cursor is one -
    and a mangled cursor is a 422 rather than "the page after whatever this
    sorts before". Silently serving a plausible page would give a client no
    way to notice it has been corrupting its own cursor, which is the same
    reasoning `/catalogue/search` refuses a cursor it did not mint."""
    for bogus in ("not-a-key", "NPTC-12345", _seed.a_uuid()):
        response = api.get("/catalogue/entries", params={"after": bogus})
        assert response.status_code == 422, f"{bogus!r}: {response.text}"
