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
from sqlalchemy import select
from sqlalchemy.engine import Connection

from nptc.audit.writer import AuditContext
from nptc.auth.grants import grant_role_unchecked
from nptc.auth.permissions import Role
from nptc.catalogue import facets as facets_module
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


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
        # Scores `float()` accepts and this endpoint never mints. `inf`
        # satisfies `score < :after_score` for every row, so the caller is
        # handed page one again and pages forever - the infinite loop the
        # refusals above exist to prevent, arrived at through the score half
        # instead of the shape. `nan` makes every comparison false and yields
        # a permanently empty page. The digest is no defence: it is unkeyed
        # by design, so a client replaying its own `q` computes it.
        f"inf:{digest}:NPTC-000001",
        f"-inf:{digest}:NPTC-000001",
        f"nan:{digest}:NPTC-000001",
        f"Infinity:{digest}:NPTC-000001",
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


# --- FR-16: faceted filters (issue #139) ----------------------------------
#
# The unit-level half of this - parsing, validation, predicate composition -
# is `test_catalogue_facets.py`, which needs no database. What lives here is
# everything that is only true of a real query against real rows: that a
# bucket's count is the number of rows that bucket actually returns, that a
# multi-valued property counts an entry once per value, and that flipping
# `filterable` changes the answer on a *running* app.


def _search(api: ApiTestApp, **params: Any) -> dict[str, Any]:
    response = api.get("/catalogue/search", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _facets(api: ApiTestApp, **params: Any) -> dict[str, dict[str, Any]]:
    return {facet["key"]: facet for facet in _search(api, **params)["facets"]}


def _bucket(facet: dict[str, Any], value: str) -> dict[str, Any]:
    return next(bucket for bucket in facet["buckets"] if bucket["value"] == value)


def _admin_token(api: ApiTestApp, *, subject: str) -> str:
    """An Administrator token, resolved through the real auth chain.

    Copied in shape from `test_api_registry_properties.py`'s own helper
    rather than imported: these two modules load their support modules by
    path (no `__init__.py` in this tree), and importing a private helper
    across test modules would couple this file's fixture to that one's.
    """
    bootstrap = api.token(subject=subject)
    api.get("/auth/me", token=bootstrap)
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    grant_role_unchecked(
        api.session,
        target_user_id=user.id,
        role=Role.ADMINISTRATOR,
        granted_by_user_id=None,
        audit=AuditContext.system(),
    )
    api.session.flush()
    return api.token(subject=subject, extra_claims={"acr": "2"})


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_facet_list_comes_from_the_registry_and_not_from_a_static_list(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The requirement's own sentence. Every `filterable` property this
    fixture seeded is a facet; the two it seeded as non-filterable are not,
    and neither is any hard-coded name.

    Scoped to this fixture's own keys throughout (CLAUDE.md's shared-container
    rule): the response also carries whatever else the running database holds,
    which is not this test's business."""
    facets = _facets(api, q=_seed.CANONICAL_TERM)

    assert seeded.discipline_property_key in facets
    assert seeded.specimen_code_property_key in facets
    assert seeded.specimen_property_key in facets
    # `volume_ml` and `flippable` are seeded `filterable=False`.
    assert seeded.volume_property_key not in facets
    assert seeded.flippable_property_key not in facets
    # The declared core-column facet, present without being a property.
    assert "status" in facets


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_facet_bucket_s_count_is_the_number_of_rows_that_bucket_returns(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The parity check the plan calls for, and the one drift no manual use
    would reveal: a count computed over a differently-composed base than the
    result set looks entirely plausible and is simply wrong.

    Asserted for *every* bucket of the facet, not one - a per-bucket
    off-by-one (an entry counted under a value it does not hold) survives a
    single-bucket check."""
    key = seeded.discipline_property_key
    facet = _facets(api, q=_seed.CANONICAL_TERM)[key]
    assert facet["buckets"], "the fixture's discipline values should produce buckets"

    for bucket in facet["buckets"]:
        keys = _keys(api, **{"q": _seed.CANONICAL_TERM, f"filter.{key}": bucket["value"]})
        assert len(keys) == bucket["count"], (
            f"facet {key!r} bucket {bucket['value']!r} claims {bucket['count']} "
            f"entries and returns {len(keys)}"
        )


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_multi_valued_property_counts_an_entry_once_under_each_of_its_values(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The canonical entry holds seven specimen codes. It must contribute
    one to each of seven buckets - never seven to one, which is what a join
    instead of an `EXISTS`/`COUNT(DISTINCT ...)` produces, and which every
    other test in this suite passes happily."""
    facet = _facets(api, q=_seed.CANONICAL_TERM)[seeded.specimen_code_property_key]

    counts = {bucket["value"]: bucket["count"] for bucket in facet["buckets"]}
    for code, _display in _seed.SPECIMEN_CODES:
        assert counts[code] == 1, f"{code} counted {counts[code]} times, expected once"


@pytest.mark.req("FR-16")
@pytest.mark.req("FR-54")
@pytest.mark.integration
def test_a_coded_bucket_is_labelled_from_the_stored_display(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """ADR-0013's open question 2, as resolved by ADR-0032: a coded facet
    groups on the code alone and is labelled from the `display` stored beside
    it when the value was recorded. No terminology call happens on the search
    path (FR-54), which is also why the label is stable when the server is
    unreachable."""
    facet = _facets(api, q=_seed.CANONICAL_TERM)[seeded.specimen_code_property_key]
    code, display = _seed.SPECIMEN_CODES[0]

    assert _bucket(facet, code)["label"] == display
    # The stub terminology client this app is built over records every call
    # it receives; a facet response must not have made one.
    assert api.terminology.requests == ()


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_truncated_facet_says_so(
    api: ApiTestApp, seeded: SeededCatalogue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FACET_BUCKET_CAP`'s whole contract - "a client is never quietly
    shown a partial list it cannot tell from a whole one" - rests on
    `truncated`, and nothing in this suite asserted the flag before this
    test: the `limit(FACET_BUCKET_CAP + 1)` / `len(rows) > FACET_BUCKET_CAP`
    / `rows[:FACET_BUCKET_CAP]` triple is exactly where an off-by-one hides.
    Lowering the cap below the fixture's own discipline value count is
    cheaper and more direct than seeding two dozen new entries just to reach
    the real one."""
    monkeypatch.setattr(facets_module, "FACET_BUCKET_CAP", 1)
    facet = _facets(api, q=_seed.CANONICAL_TERM)[seeded.discipline_property_key]
    assert facet["truncated"] is True
    assert len(facet["buckets"]) == 1


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_facet_at_exactly_the_cap_is_not_truncated(
    api: ApiTestApp, seeded: SeededCatalogue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative case alongside the above: a facet whose value count
    lands exactly on the cap must not report truncation it did not do."""
    monkeypatch.setattr(facets_module, "FACET_BUCKET_CAP", 2)
    facet = _facets(api, q=_seed.CANONICAL_TERM)[seeded.discipline_property_key]
    assert facet["truncated"] is False
    assert len(facet["buckets"]) == 2


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_filterable_property_that_cannot_be_grouped_says_so_rather_than_vanishing(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """ADR-0013 SS8's "stated cost". A `decimal` property has no meaningful
    grouping - every value is its own bucket - so its handler returns `None`
    from `facet_expression()`. It is still a usable filter, and it is
    reported with `facetable: false` rather than omitted: a facet that
    vanished silently is indistinguishable, to a client, from one whose
    values happen to match nothing."""
    facet = _facets(api, q=_seed.CANONICAL_TERM)[seeded.turnaround_property_key]

    assert facet["facetable"] is False
    assert facet["buckets"] == []
    # ... and still a filter. The canonical entry's value is inside this
    # range and it is the entry the query matches best.
    key = seeded.turnaround_property_key
    assert seeded.canonical in _keys(
        api, **{"q": _seed.CANONICAL_TERM, f"filter.{key}:range": "1..2"}
    )
    assert seeded.canonical not in _keys(
        api, **{"q": _seed.CANONICAL_TERM, f"filter.{key}:range": "10..20"}
    )


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_values_or_within_a_facet_and_facets_and_with_each_other(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The only composition under which a facet panel behaves the way every
    user expects: ticking a second value in one facet broadens, ticking a
    value in a second facet narrows."""
    discipline = seeded.discipline_property_key
    specimen = seeded.specimen_code_property_key

    chemistry = _keys(api, **{"q": _seed.CANONICAL_TERM, f"filter.{discipline}": "Chemistry"})
    both = _keys(
        api,
        **{
            "q": _seed.CANONICAL_TERM,
            f"filter.{discipline}": ["Chemistry", "Haematology"],
        },
    )
    # A second value in the same facet broadens, strictly.
    assert set(chemistry) < set(both)
    assert set(both) == {seeded.canonical, seeded.synonym_only}

    narrowed = _keys(
        api,
        **{
            "q": _seed.CANONICAL_TERM,
            f"filter.{discipline}": ["Chemistry", "Haematology"],
            # Only the canonical entry carries any specimen code at all.
            f"filter.{specimen}": _seed.SPECIMEN_CODES[0][0],
        },
    )
    # A value in a second facet narrows, strictly.
    assert set(narrowed) < set(both)
    assert narrowed == [seeded.canonical]


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_facet_s_own_selection_does_not_narrow_its_own_counts(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """Drill-down. Choosing "Chemistry" must not collapse the discipline
    facet to a single bucket - a user has to be able to see what switching to
    "Haematology" would give them, or the panel is a dead end. Every *other*
    facet does narrow, which is the half that makes the counts useful."""
    discipline = seeded.discipline_property_key
    unfiltered = _facets(api, q=_seed.CANONICAL_TERM)[discipline]
    filtered = _facets(api, **{"q": _seed.CANONICAL_TERM, f"filter.{discipline}": "Chemistry"})[
        discipline
    ]

    # More than one bucket, or the assertion below would hold vacuously and
    # this test would pass against an implementation with no drill-down at
    # all.
    assert len(unfiltered["buckets"]) > 1
    assert filtered["buckets"] == unfiltered["buckets"]


@pytest.mark.req("FR-16")
@pytest.mark.req("FR-09")
@pytest.mark.integration
def test_flipping_filterable_makes_a_property_a_facet_with_no_restart(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """FR-09's actual claim, asserted end to end rather than by hand.

    The property is seeded `filterable=False`, flipped through the real
    registry `PATCH` route on the *same running app*, and re-queried. Nothing
    is rebuilt, no dependency is re-resolved and no process restarts - so a
    facet list computed once at start-up, or cached anywhere, fails here and
    passes everything else in this file."""
    key = seeded.flippable_property_key

    # --- before: not a facet, and refused as a filter -----------------
    assert key not in _facets(api, q=_seed.CANONICAL_TERM)
    refused = api.get(
        "/catalogue/search", params={"q": _seed.CANONICAL_TERM, f"filter.{key}": "Routine"}
    )
    assert refused.status_code == 422, refused.text

    # --- the flip, through the real route -----------------------------
    token = _admin_token(api, subject="sub-fr16-flip")
    current = api.get(f"/registry/properties/{key}", token=token)
    assert current.status_code == 200, current.text
    patched = api.request(
        "PATCH",
        f"/registry/properties/{key}",
        token=token,
        json={
            "expected_row_version": current.json()["row_version"],
            "reason": "Made filterable to prove FR-09's no-restart claim (issue #139).",
            "filterable": True,
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["filterable"] is True

    # --- after: a facet, with counts that match the rows ---------------
    facet = _facets(api, q=_seed.CANONICAL_TERM)[key]
    assert facet["facetable"] is True
    assert facet["buckets"]
    for bucket in facet["buckets"]:
        keys = _keys(api, **{"q": _seed.CANONICAL_TERM, f"filter.{key}": bucket["value"]})
        assert len(keys) == bucket["count"]


# --- FR-16 refusals -------------------------------------------------------
#
# Every one of these is the same failure in a different disguise: a filter
# the server does not understand. Ignoring it serves a page that looks like
# an answer to the question the caller asked and is an answer to a different
# one, with nothing in the response to tell them apart.


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_an_unknown_filter_key_is_a_422_not_an_unfiltered_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    response = api.get(
        "/catalogue/search",
        params={"q": _seed.CANONICAL_TERM, "filter.no_such_property": "x"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_non_filterable_property_is_a_422_not_an_unfiltered_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """The key names a real property. Silently ignoring it is the worst
    outcome available: the caller is served the whole result set and told
    nothing."""
    response = api.get(
        "/catalogue/search",
        params={"q": _seed.CANONICAL_TERM, f"filter.{seeded.volume_property_key}": "5"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_an_operator_the_property_does_not_support_is_a_422(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`prefix` against a coded property: the stored value is an object, so
    there is no prefix to take, and `CodeHandler.supported_filter_ops()` is
    what says so - not a list in the router."""
    response = api.get(
        "/catalogue/search",
        params={
            "q": _seed.CANONICAL_TERM,
            f"filter.{seeded.specimen_code_property_key}:prefix": "119",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_value_of_the_wrong_kind_is_a_422(api: ApiTestApp, seeded: SeededCatalogue) -> None:
    response = api.get(
        "/catalogue/search",
        params={
            "q": _seed.CANONICAL_TERM,
            f"filter.{seeded.turnaround_property_key}": "not a number",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_an_unrecognised_status_value_is_a_422_not_an_empty_page(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`status` has no `PropertyDefinition` and so no handler to validate a
    value against - it must check itself. Before this check existed, a
    typo'd status silently matched zero rows: a 200 with an empty result,
    indistinguishable from a legitimately empty search."""
    response = api.get(
        "/catalogue/search",
        params={"q": _seed.CANONICAL_TERM, "filter.status": "activee"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_same_facet_sent_with_two_operators_is_a_422(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """`?filter.discipline=chemistry&filter.discipline:in=haematology` reads
    as two selections on the same key, which would AND into a predicate no
    row can satisfy - a caller error, and it must be reported as one rather
    than served as a silently empty page."""
    key = seeded.discipline_property_key
    response = api.get(
        "/catalogue/search",
        params={
            "q": _seed.CANONICAL_TERM,
            f"filter.{key}": "Chemistry",
            f"filter.{key}:in": "Haematology",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_a_cursor_replayed_under_a_different_filter_set_is_refused(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """A search cursor carries a relevance score, and narrowing the filters
    changes which entries exist to be scored - so the window the cursor names
    is the next page of neither request. Refused, exactly as a cursor
    replayed under a different `q` already is: an arbitrarily truncated
    result set is the kind of wrong answer a client cannot detect."""
    key = seeded.discipline_property_key
    first = _search(api, **{"q": _seed.CANONICAL_TERM, "limit": 1})
    cursor = first["next_cursor"]
    assert cursor is not None, "the fixture should match more than one entry"

    # The same cursor, on the same `q`, but now with a filter applied.
    response = api.get(
        "/catalogue/search",
        params={
            "q": _seed.CANONICAL_TERM,
            "limit": 1,
            "after": cursor,
            f"filter.{key}": "Chemistry",
        },
    )
    assert response.status_code == 422, response.text

    # ... and the unchanged request still pages, so the refusal above is
    # about the filter set and not about the cursor being unusable at all.
    assert (
        api.get(
            "/catalogue/search",
            params={"q": _seed.CANONICAL_TERM, "limit": 1, "after": cursor},
        ).status_code
        == 200
    )


@pytest.mark.req("FR-16")
@pytest.mark.integration
def test_the_browse_listing_filters_but_returns_no_facets(
    api: ApiTestApp, seeded: SeededCatalogue
) -> None:
    """ADR-0032's split: `/catalogue/entries` accepts the same filters and
    computes no counts, because counting on every page of a browse costs
    something no caller has asked for."""
    key = seeded.discipline_property_key
    response = api.get(
        "/catalogue/entries",
        params={"after": seeded.before_all, f"filter.{key}": "Haematology"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "facets" not in body
    # In `business_key` order, and only the two entries holding that value -
    # `before_all` excludes everything this fixture did not seed.
    assert [item["business_key"] for item in body["items"]] == [
        seeded.synonym_only,
        seeded.tie_first,
    ]

    refused = api.get("/catalogue/entries", params={"filter.no_such_property": "x"})
    assert refused.status_code == 422, refused.text
