"""`GET /api/v1/catalogue/search` (issue #142, FR-14, FR-15, FR-20).

These tests are about the two properties FR-14/FR-15 actually name -
insensitivity to case and diacritics, and tolerance of typographical error -
plus the one property a search must *not* have: matching everything.

**The principal failure mode here is a search that is too generous, not one
that is too strict.** A user cannot distinguish a page of irrelevant results
from a broken catalogue, and neither can a vendor's integration test; an
empty page for a nonsense query is unambiguous. So
`test_a_query_below_the_threshold_matches_nothing` is not a nice-to-have
alongside the positive cases - it is the case that fails if the threshold is
dropped to make some other test pass.

Whether the query *plans* against the trigram indexes is
`test_db_search_index.py`'s job, not this module's: every test here passes
identically over a sequential scan.
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


def _keys(api: ApiTestApp, **params: Any) -> list[str]:
    response = api.get("/catalogue/search", params=params)
    assert response.status_code == 200, response.text
    return [item["business_key"] for item in response.json()["items"]]


# --- FR-14: case and diacritics -------------------------------------------


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_an_unaccented_query_finds_an_accented_term(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`Müller` is findable by typing `muller`, which is what most people
    with a UK keyboard will type. Both directions matter, so both are
    asserted: the normalisation has to be applied to the query as well as to
    the column, and applying it to only one side still finds nothing."""
    assert seeded.accented in _keys(api, q="muller cell antibody")
    assert seeded.accented in _keys(api, q="MULLER CELL ANTIBODY")
    assert seeded.accented in _keys(api, q=_seed.ACCENTED_TERM)


# --- FR-15: typographical error -------------------------------------------


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_transposition_still_finds_the_entry(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    """`Haemoglobni` for `Haemoglobin` - a transposition of adjacent
    letters, the most common typing error there is. This is the case a
    `tsvector` search cannot serve at all: the misspelling stems to a
    different lexeme and scores exactly zero."""
    assert seeded.canonical in _keys(api, q="Haemoglobni electrophoresis")


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_dropped_letter_still_finds_the_entry(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    assert seeded.canonical in _keys(api, q="Haemoglobin electrophresis")


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_query_below_the_threshold_matches_nothing(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The failure mode that matters. A search that quietly broadens until
    it finds something is worse than one that finds nothing: the caller
    cannot tell it apart from a working search over a catalogue that has
    nothing to offer, so they trust the noise."""
    assert _keys(api, q="zzzqqqxxwv") == []


# --- matching through a synonym -------------------------------------------


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_an_entry_matched_only_by_its_synonym_is_returned_once(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Two things at once, deliberately. The entry's own preferred term
    shares nothing with the query, so reaching it proves the designation
    half of the union actually runs - and it appears exactly once even
    though the entry could match through several rows, which is what the
    `GROUP BY` is for. Without it, an entry with five near-matching
    synonyms would fill a page by itself."""
    keys = _keys(api, q=_seed.SYNONYM_ONLY_SYNONYM, limit=200)

    assert seeded.synonym_only in keys
    assert keys.count(seeded.synonym_only) == 1


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_retired_synonym_is_not_a_way_into_the_catalogue(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The canonical entry has a retired synonym whose text nothing else
    resembles. Searching it must not match: a retired designation is
    history, and `ix_designation_term_trgm` is partial on `status =
    'active'` for this reason - so a query that ignored the status filter
    would also be a query the index cannot serve."""
    assert _keys(api, q=_seed.RETIRED_SYNONYM) == []


# --- relevance order and the keyset ---------------------------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_results_are_ordered_by_score_then_business_key(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Descending score, and `business_key` ascending inside a tie. The
    tie-break is what makes the order total - the two tie fixtures share an
    identical preferred term, so their scores are equal to the bit and any
    order between them would otherwise be arbitrary from one request to the
    next."""
    response = api.get("/catalogue/search", params={"q": _seed.TIE_TERM, "limit": 200})
    items = response.json()["items"]

    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)

    tied = [item["business_key"] for item in items if item["score"] == scores[0]]
    assert tied == sorted(tied)
    assert [seeded.tie_first, seeded.tie_second] == [
        key for key in tied if key in (seeded.tie_first, seeded.tie_second)
    ]


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_paging_across_a_score_tie_neither_drops_nor_repeats_a_row(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """A page boundary landing *inside* a tie is the case a score-only
    keyset gets wrong: `score < :after_score` skips every other row with the
    same score, and `score <= :after_score` repeats the row just served.
    Paging one row at a time forces every boundary to be a tie boundary."""
    visited: list[str] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {"q": _seed.TIE_TERM, "limit": 1}
        if cursor is not None:
            params["after"] = cursor
        response = api.get("/catalogue/search", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        visited.extend(item["business_key"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert cursor is None, "search paging did not terminate"
    assert len(visited) == len(set(visited)), f"a row was served twice: {visited}"
    assert {seeded.tie_first, seeded.tie_second} <= set(visited)
    # And the same rows a single unpaged request returns - no gaps.
    unpaged = _keys(api, q=_seed.TIE_TERM, limit=200)
    assert visited == unpaged


# --- refusals -------------------------------------------------------------


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_blank_query_is_refused_rather_than_returning_the_catalogue(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Both an absent `q` and a whitespace-only one. Returning the whole
    catalogue for a blank search box is the tempting behaviour and the wrong
    one: it hides a broken client from everybody, and it is the most
    expensive query the endpoint can run."""
    assert api.get("/catalogue/search").status_code == 422
    assert api.get("/catalogue/search", params={"q": ""}).status_code == 422
    assert api.get("/catalogue/search", params={"q": "   "}).status_code == 422


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_cursor_this_api_did_not_issue_is_refused(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Refused, not ignored. Silently restarting from page one turns a
    client bug into an infinite paging loop that reads as a slow
    catalogue."""
    issued = api.get("/catalogue/search", params={"q": _seed.TIE_TERM, "limit": 1}).json()[
        "next_cursor"
    ]
    assert issued is not None
    score, digest, _key = issued.split(":")

    bogus = (
        "not-a-cursor",
        # No digest part at all - the two-part shape this endpoint used to
        # issue.
        f"{score}:NPTC-000001",
        "abc:NPTC-000001",
        "0.5",
        # A well-formed score and digest, but a key half that is not a
        # business key. Accepted as "any non-empty string" this would page
        # from whatever it happens to sort after, which is how a client
        # corrupting its own cursor goes unnoticed - `/catalogue/entries`
        # pattern-validates its `after` for the same reason.
        f"{score}:{digest}:not-a-business-key",
        f"{score}:{digest}:NPTC-12345",
        f"{score}:{digest}:nptc-000001",
        f"{score}:{digest}:",
    )
    for cursor in bogus:
        response = api.get("/catalogue/search", params={"q": _seed.TIE_TERM, "after": cursor})
        assert response.status_code == 422, f"{cursor!r}: {response.text}"


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_cursor_replayed_under_a_different_query_is_refused(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The silent-wrong-answer case, which is why the cursor carries a digest
    of the `q` that minted it.

    A score is only meaningful against the query it was computed for. Unbound,
    replaying this cursor under a second query would compare the *new* query's
    scores against the *old* query's boundary and serve a window that is the
    next page of neither - and it would do so with a 200 and a plausible-
    looking body, which is precisely the class of failure a client cannot
    detect. The same cursor under the same `q` must still work, or the binding
    has broken paging rather than protected it.
    """
    first = api.get("/catalogue/search", params={"q": _seed.TIE_TERM, "limit": 1})
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    replayed = api.get("/catalogue/search", params={"q": _seed.CANONICAL_TERM, "after": cursor})
    assert replayed.status_code == 422, replayed.text

    same_query = api.get("/catalogue/search", params={"q": _seed.TIE_TERM, "after": cursor})
    assert same_query.status_code == 200, same_query.text


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_a_search_hit_carries_the_same_summary_fields_as_the_list(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """UI parity in miniature: a search result page has to be renderable
    without a second request per row, so it carries the summary fields, not
    just a key and a score."""
    hit = next(
        item
        for item in api.get(
            "/catalogue/search", params={"q": _seed.CANONICAL_TERM, "limit": 200}
        ).json()["items"]
        if item["business_key"] == seeded.canonical
    )
    listed = next(
        item
        for item in api.get(
            "/catalogue/entries", params={"after": seeded.before_all, "limit": 200}
        ).json()["items"]
        if item["business_key"] == seeded.canonical
    )

    assert {key: hit[key] for key in listed} == listed
    assert 0 < hit["score"] <= 1
