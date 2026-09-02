"""Hybrid full-text and trigram ranking across all five searchable fields
(issue #138, FR-14, FR-15, FR-98).

`test_api_public_search.py` owns the two properties #142 landed - case and
diacritic folding, and the refusal to match everything. This module owns what
#138 adds: that all five of FR-14's named fields are reachable from the one
query box, and that the ranking puts an exact hit above a fuzzy one.

**Why the ranking assertions are about *order*, not about scores.** A test
pinning `score == 0.95` would fail on any weight change while proving nothing
a user would notice; what FR-14 requires is that an exact code or
preferred-term match *outranks* a fuzzy synonym hit, which is a statement
about position in the result list. The one exception is
`test_the_score_bands_cannot_overlap`, which asserts the inequality between
the constants directly - that is the property which makes every ordering
assertion below hold for inputs no fixture contains, rather than only for the
ones it does.

**The principal failure mode this module guards.** Adding seven scans to a
query is an invitation to a search that matches everything: each new branch
is another way for a weak match to enter the union, and the `code_binding`
labels are the longest strings in the catalogue. So the negative cases here
are not decoration - `test_a_retired_binding_is_not_a_way_in` and
`test_a_nonsense_query_still_matches_nothing_across_all_five_fields` are the
tests that fail if a later edit widens a predicate or drops a status filter.

Whether these queries *plan* against the nine indexes is
`test_db_search_index.py`'s job. Every test here passes identically over a
sequential scan.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from nptc.catalogue.search import (
    BINDING_LABEL_WEIGHT,
    DESIGNATION_WEIGHT,
    EXACT_CODE_SCORE,
    EXACT_LABEL_SCORE,
    EXACT_PREFERRED_TERM_SCORE,
    PREFERRED_TERM_WEIGHT,
    SIMILARITY_THRESHOLD,
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

build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


@pytest.fixture
def example(api: ApiTestApp) -> Any:
    return _seed.seed_worked_example(api.session)


@pytest.fixture
def catalogue(api: ApiTestApp) -> Any:
    return _seed.seed_public_catalogue(api.session)


def _keys(api: ApiTestApp, **params: Any) -> list[str]:
    response = api.get("/catalogue/search", params=params)
    assert response.status_code == 200, response.text
    return [item["business_key"] for item in response.json()["items"]]


# --- FR-14: the PRD's own worked example ----------------------------------


#: PRD FR-14, verbatim: "A user typing `49466006`, `ACTH`,
#: `Adrenocorticotropic hormone` or `Corticotropin` MUST reach the same
#: entry." Parametrised rather than written as four asserts in one test so a
#: failure names which of the four routes broke - they are four different
#: scans, and which one regressed is the whole diagnostic.
_WORKED_EXAMPLE_QUERIES = (
    _seed.WORKED_EXAMPLE_CODE,
    "ACTH",
    "Adrenocorticotropic hormone",
    "Corticotropin",
)


@pytest.mark.req("FR-14")
@pytest.mark.integration
@pytest.mark.parametrize("query", _WORKED_EXAMPLE_QUERIES)
def test_the_prd_worked_example_reaches_one_entry_as_the_top_result(
    api: ApiTestApp, example: Any, query: str
) -> None:
    """The requirement's own acceptance test, and the reason the ranking
    bands exist at all.

    `in the results` would be the weaker assertion and would pass on a search
    that buried the entry on page four; FR-14's example is about a user
    reaching the entry, so this asserts it is *first*."""
    keys = _keys(api, q=query)
    assert keys, f"{query!r} returned nothing at all"
    assert keys[0] == example.acth, (
        f"{query!r} put {keys[0]} above the entry it names ({example.acth})"
    )


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_the_code_is_matched_exactly_and_not_by_similarity(api: ApiTestApp, example: Any) -> None:
    """A code is right or wrong (ADR-0029).

    The near-miss below differs from the seeded code in one digit, so its
    trigram similarity is high enough to clear the 0.3 threshold with room to
    spare - which is exactly why fuzzy code matching was rejected, and why
    this asserts the near-miss finds *nothing* rather than merely ranking
    lower. It is also a Verhoeff-invalid SCTID, so no catalogue could hold
    it, which is what makes 'nothing' the honest answer."""
    near_miss = "49466007"
    assert near_miss != _seed.WORKED_EXAMPLE_CODE
    assert example.acth not in _keys(api, q=near_miss)


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_padded_query_still_reaches_the_exact_band(api: ApiTestApp, example: Any) -> None:
    """A code or a term pasted out of a spreadsheet or an email arrives
    padded, and every exact comparison has to survive that.

    Without a trim, the code branch is a silent empty result - which reads to
    a user as 'this code is not in the catalogue'. The four *label*
    comparisons fail more quietly still, and that is the case this test grew
    to cover (PR #237 review): `nptc_search_text` lowercases and unaccents
    but does not trim, so a padded preferred term is not equal to the stored
    one while its `similarity()` is 1.0. The hit is returned either way, so
    presence proves nothing here - it would simply be scored as fuzzy, and an
    exact synonym match on a *different* entry would outrank the entry the
    user pasted the name of. Asserting the band is what detects that.

    The padding is parametrised over more than spaces because the first fix
    was too narrow (second review round): a single cell copied out of a
    spreadsheet arrives as a value ending in a carriage return and a newline,
    and SQL's `btrim(text)` trims
    spaces only. The trim is now Python's `str.strip()`, applied to the query
    side before binding, which is why a tab and a newline belong in this
    list."""
    for padding in ("  ", "\t", "\r\n", " \t\r\n "):
        assert _keys(api, q=f"{padding}{_seed.WORKED_EXAMPLE_CODE}{padding}")[:1] == [
            example.acth
        ], repr(padding)

        padded = _hits(api, q=f"{padding}{_seed.WORKED_EXAMPLE_TERM}{padding}")
        assert padded[0]["business_key"] == example.acth, padded
        assert padded[0]["score"] == pytest.approx(EXACT_PREFERRED_TERM_SCORE, abs=1e-6), (
            f"a preferred term padded with {padding!r} scored below the exact "
            "band - the equality comparison is no longer trimming"
        )


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_query_that_excludes_every_word_does_not_return_the_catalogue(
    api: ApiTestApp, catalogue: Any, example: Any
) -> None:
    """The one-character denial of service (PR #237 review).

    `websearch_to_tsquery` reads a leading `-` as NOT, so `-glucose` lexes to
    `!'glucos'` - a query with nothing positive in it, which `@@` satisfies
    for *every* row and which GIN cannot probe. Unguarded, each of the four
    full-text branches becomes a sequential scan returning the whole
    catalogue at a floor score, on an unauthenticated endpoint.

    The existing nonsense-query test cannot catch this: a nonsense *word*
    still lexes to a positive lexeme. Neither can a scan for a leading `-` -
    the third case below has no leading `-` at all, and reduces to a pure
    negation only because its positive half is an english stopword. Hence the
    guard is `NOT ('' :: tsvector @@ nptc_search_query(:q))`, which asks the
    question directly.

    The fourth case is that guard being deliberately *wider* than "every word
    was excluded" (second review round). `zymogen or -kinase` lexes to
    `'zymogen' | !'kinas'`, and a disjunction with one negated branch is still
    satisfied by absence - the `!'kinas'` half alone would return the
    catalogue - so it is pruned, and the positive word loses its full-text
    route with it. That is a documented degradation rather than a bug:
    trigram is unguarded, so `zymogen` is still matched by similarity. A
    conjunction such as `vitamin -d` still requires a lexeme and is correctly
    left alone.

    Trigram is unaffected and deliberately still runs, so these queries are
    not refused - they are searched, by similarity, for the literal string
    typed. That is why this asserts nothing comes back for text nothing in
    the catalogue resembles, rather than asserting an error."""
    assert _keys(api, q=_seed.WORKED_EXAMPLE_TERM) != [], (
        "the fixtures are not seeded - an empty result below would prove nothing"
    )
    for query in ("-zymogen", "-zymogen -kinase", "the -zymogen", "zymogen or -kinase"):
        assert _keys(api, q=query, limit=100) == [], query


# --- FR-98: both tag forms reach the entry --------------------------------


@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_an_fsn_reaches_its_entry_with_the_semantic_tag_typed_and_omitted(
    api: ApiTestApp, example: Any
) -> None:
    """FR-98's search-index row: 'Both forms indexed, so a user who searches
    any label ever published reaches the entry.'

    The fixture entry here has no `au_preferred_term` and a preferred term
    that shares no word with its FSN, so the stored `fsn` is the only route
    to it - on the worked-example entry both queries would also match through
    `au_preferred_term` and this would pass without the FSN scan running.

    Only one index backs both forms, which is the substantive claim: see
    ADR-0029 on why a SQL-side `nptc_strip_semantic_tag` was rejected. The
    semantic tag is extra text to trigram and its own lexeme to full-text, so
    neither form needs a stripped copy of the column to be findable."""
    assert example.fsn_only in _keys(api, q=_seed.FSN_ONLY_FSN)
    assert example.fsn_only in _keys(api, q=_seed.FSN_ONLY_FSN_WITHOUT_TAG)


def _hits(api: ApiTestApp, **params: Any) -> list[dict[str, Any]]:
    response = api.get("/catalogue/search", params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


@pytest.mark.req("FR-98")
@pytest.mark.integration
def test_a_bare_semantic_tag_is_only_ever_a_weak_match(api: ApiTestApp, example: Any) -> None:
    """The documented cost of indexing the tag-intact form, pinned so it
    cannot get worse.

    Indexing the FSN exactly as served (FR-82) means the semantic tag is
    searchable text like any other. A bare `procedure` therefore *does* reach
    every procedure-tagged entry - both seeded bindings carry that tag - and
    asserting otherwise would be asserting against the design ADR-0029
    records rather than testing it. The alternative, a SQL-side tag stripper,
    was rejected for putting a second copy of the semantic-tag regex in the
    database (ADR-0006, FR-83).

    What must stay true is that a bare tag is only ever a *weak* match. It
    can never enter an exact band, so it can never outrank a real query, and
    the ranking keeps it below anything a user actually meant. That is the
    property this asserts, and it is the one that fails if a later edit
    boosts binding labels or drops the tag out of the weighting."""
    for hit in _hits(api, q="procedure"):
        assert hit["score"] < EXACT_LABEL_SCORE, (
            f"a bare semantic tag scored {hit['score']} on {hit['business_key']}, "
            "which is inside an exact-match band - it can now outrank a real query"
        )


# --- FR-15: typographical error and word-order variation ------------------


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_transposed_character_in_a_designation_still_reaches_the_entry(
    api: ApiTestApp, catalogue: Any
) -> None:
    """FR-15's typo half, against a *designation* rather than the entry's own
    preferred term - the synonym-only entry is unreachable except through its
    synonym, so this cannot pass by matching the preferred term instead.

    `Haemoglboin` transposes two characters of `Haemoglobin`. Top three, per
    the issue's acceptance criterion: a typo is allowed to cost the entry its
    first place to an exact match on a similar term, but not to bury it.

    This is the case full-text search cannot answer at all - the transposed
    form stems to a different lexeme and scores zero - and so it is the
    reason the trigram scans survive ADR-0029 rather than being replaced."""
    keys = _keys(api, q="Haemoglboin electrophoresis by capillary method")
    assert catalogue.synonym_only in keys[:3], keys


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_two_word_term_searched_in_reverse_order_still_matches(
    api: ApiTestApp, catalogue: Any
) -> None:
    """FR-15's word-order half.

    Asserted as the top result, not merely present: reversing the words of a
    term is not a partial or degraded query, and a user who typed the right
    words in the wrong order should not be ranked below someone else's near
    match."""
    assert _keys(api, q="electrophoresis haemoglobin")[:1] == [catalogue.canonical]


# --- FR-14: ranking ---------------------------------------------------------


@pytest.mark.req("FR-14")
def test_the_score_bands_cannot_overlap() -> None:
    """The arithmetic behind every ordering assertion above.

    Every fuzzy contribution is a `similarity()` or a `ts_rank_cd()` result -
    both at most 1.0 - multiplied by its source's weight, so the largest
    score any fuzzy match can reach from any source is the largest weight.
    Asserting the exact tiers sit strictly above that ceiling is what makes
    'an exact match outranks a fuzzy one' true for every possible input,
    rather than for the particular corpus a fixture happens to hold.

    No database and no fixture: this is a property of the constants, and a
    test that needed rows to demonstrate it would be demonstrating something
    weaker."""
    highest_fuzzy = max(PREFERRED_TERM_WEIGHT, DESIGNATION_WEIGHT, BINDING_LABEL_WEIGHT)

    assert highest_fuzzy < EXACT_LABEL_SCORE, (
        "a fuzzy match can reach the exact-label band - an exact synonym hit "
        "no longer reliably outranks a near-miss"
    )
    assert EXACT_LABEL_SCORE < EXACT_PREFERRED_TERM_SCORE, (
        "an exact synonym hit can tie an exact preferred-term hit"
    )
    assert EXACT_PREFERRED_TERM_SCORE < EXACT_CODE_SCORE, (
        "an exact preferred-term hit can tie an exact code hit"
    )
    # The bands are only meaningful if a weight actually attenuates. A weight
    # of 1.0 anywhere would let a perfect fuzzy score reach the exact band.
    assert highest_fuzzy < 1.0


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_an_inflected_form_reaches_the_entry_and_is_not_ranked_beneath_a_typo(
    api: ApiTestApp, example: Any
) -> None:
    """Both halves of what the full-text branches are for (PR #237 review).

    `counting` appears in no seeded text; `count` does, inside
    `Full blood count (procedure)`. Trigram cannot bridge that on its own -
    measured against this fixture the similarity is 0.16, well under the 0.3
    threshold, because the query is short and the field it has to match is
    long and the length ratio alone sinks it. That is precisely the gap
    ADR-0029 added full-text to close, and the entry's own preferred term
    (`Unrelated haematology placeholder`) shares nothing with the query, so
    the stored `fsn` is the only route in and the full-text branch is the
    only thing that can find it. The entry being reachable at all is
    therefore the recall claim, and it fails outright without that branch.

    The score is the ranking claim, and it is the one that regressed in
    review. `ts_rank_cd(..., 32)` measures a *complete* match in this text at
    0.0909, so an unscaled weighted full-text contribution tops out around
    0.07 - beneath every trigram hit the threshold admits, which would mean
    an entry found only by an inflected form sorts below every typo in the
    catalogue. Each contribution is therefore mapped onto
    `[SIMILARITY_THRESHOLD, 1]` before weighting, so the weakest admissible
    match of either kind enters at the same rank. Asserting against that
    floor - rather than a measured constant - is what keeps this a test of
    the rescale rather than of `ts_rank_cd`'s current output."""
    hits = _hits(api, q="counting", limit=100)
    scored = {hit["business_key"]: hit["score"] for hit in hits}
    assert example.fsn_only in scored, (
        "an inflected form no longer reaches its entry - the full-text "
        "branches are not contributing recall"
    )
    assert scored[example.fsn_only] >= SIMILARITY_THRESHOLD * BINDING_LABEL_WEIGHT - 1e-6, (
        "a full-text match scored below the weakest admissible trigram match "
        "from the same source - the rescale is gone and full-text is buying "
        "recall at the bottom of the list rather than ranking"
    )


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_an_exact_preferred_term_outranks_a_fuzzy_hit_on_the_same_query(
    api: ApiTestApp, catalogue: Any
) -> None:
    """The issue's fourth acceptance criterion, at the HTTP level.

    The seeded `draft`/`deprecated`/`withdrawn` entries hold near-copies of
    the canonical preferred term, but they are status-filtered out, so the
    fuzzy rival here is the synonym-only entry, whose synonym
    (`Haemoglobin electrophoresis by capillary method`) contains the query as
    a proper prefix and therefore matches well. The canonical entry's
    preferred term *is* the query, so it must come first."""
    keys = _keys(api, q=_seed.CANONICAL_TERM)
    assert keys[0] == catalogue.canonical, keys
    assert catalogue.synonym_only in keys, (
        "the fuzzy rival did not match at all - this test is no longer "
        "comparing an exact hit against a fuzzy one"
    )
    assert keys.index(catalogue.canonical) < keys.index(catalogue.synonym_only)


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_an_entry_matched_through_several_fields_is_returned_once(
    api: ApiTestApp, example: Any
) -> None:
    """Nine scans, one row per entry.

    `Adrenocorticotropic hormone` matches the worked example's preferred term,
    its FSN and its AU preferred term, by both trigram and full-text - six of
    the nine branches at once. The `GROUP BY` is what collapses those into a
    single result, and without it a well-bound entry would crowd a whole page
    with copies of itself."""
    keys = _keys(api, q=_seed.WORKED_EXAMPLE_TERM)
    assert keys.count(example.acth) == 1, keys


# --- the negative cases -----------------------------------------------------


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_retired_binding_is_not_a_way_in(api: ApiTestApp, example: Any) -> None:
    """A retired binding is history, exactly as a retired designation is.
    The five `code_binding` indexes are partial on `status = 'active'` and
    `_SEARCH_SQL` spells the predicate as a literal so the planner can prove
    it covers the query.

    **The fixture choice is the test.** `seed_worked_example`'s retired
    binding is `Poikilocytosis (finding)`, which shares no word with any
    other string that fixture seeds. An earlier version of this test used
    `seed_public_catalogue`'s retired `Procedure (procedure)` and passed for
    the wrong reason in reverse: that FSN shares its tag with the *active*
    binding's `Microscopy (acid fast bacilli) (procedure)`, so the query
    reached the entry through the active binding and the assertion could
    never have detected a missing status filter. `RETIRED_SYNONYM`'s own note
    records the same lesson for designations."""
    assert example.fsn_only not in _keys(api, q=_seed.FSN_ONLY_RETIRED_WORD)
    assert example.fsn_only not in _keys(api, q=_seed.FSN_ONLY_RETIRED_FSN)
    # The entry itself is reachable - so the assertions above are about the
    # retired binding being invisible, not about the entry having vanished.
    assert example.fsn_only in _keys(api, q=_seed.FSN_ONLY_FSN)


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_nonsense_query_still_matches_nothing_across_all_five_fields(
    api: ApiTestApp, catalogue: Any, example: Any
) -> None:
    """The test that fails if a later edit widens the query to be generous.

    #142 already had this assertion over two fields; the risk it guards grew
    with the other three, because the `code_binding` labels are the longest
    strings in the catalogue and a long string shares a trigram with almost
    anything. A user cannot tell a page of noise from a working search over a
    catalogue that has nothing to offer, so they trust the noise - which is
    why `SIMILARITY_THRESHOLD` moves up and never down (ADR-0024)."""
    assert _keys(api, q="zzzqqqxxwv nonsense that matches nothing") == []


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_hidden_entry_is_unreachable_through_a_binding_label(
    api: ApiTestApp, catalogue: Any
) -> None:
    """The status filter has to hold on the new scans too.

    `seed_public_catalogue`'s `draft` entry carries an *active* binding
    (`Poikilocytosis (finding)`), so the binding-side branches will match it
    on this query and the only thing keeping it out of the result is the
    entry-status join. That makes this the case a `code_binding` branch
    written without the outer status filter would fail - and it is a real
    hazard, because the binding is active even though the entry is not."""
    assert catalogue.draft not in _keys(api, q=_seed.DRAFT_FSN)
