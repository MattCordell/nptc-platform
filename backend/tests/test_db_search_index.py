"""The search normalisation function and its two trigram indexes
(issue #142, migration 0012, FR-14, FR-15).

**Why an `EXPLAIN` test exists at all.** Every functional search test in
`test_api_public_search.py` passes identically whether the query uses the
GIN trigram indexes or sequentially scans `catalogue_entry` and
`designation` in full. A predicate written so that the index cannot be used
- `similarity(...) > 0.3` instead of the `%` operator, or `unaccent(lower(
term))` instead of `nptc_search_text(term)`, a composition Postgres has no
reason to recognise as the indexed expression - is therefore invisible to
every other test in the suite and shows up only as a slow catalogue in
production. This module is the only defence, so it asserts the plan, not
just the answer.

Marked `integration`: `nptc_search_text` is a database function and a query
plan is a database fact - there is no unit-level substitute (NFR-39).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

_NORMALISE_SQL = text("SELECT nptc_search_text(:value)")

_INDEX_DEFINITION_SQL = text(
    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :name"
)


def _normalise(db: Connection, value: str) -> str | None:
    result: str | None = db.execute(_NORMALISE_SQL, {"value": value}).scalar_one()
    return result


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_search_text_folds_case_and_diacritics(db: Connection) -> None:
    """The property FR-14 actually needs: a user typing `muller` and a
    catalogue holding `Müller` must meet in the middle."""
    assert _normalise(db, "Müller") == "muller"
    assert _normalise(db, "MÜLLER") == "muller"
    assert _normalise(db, "muller") == "muller"
    # Not a no-op on every input - a function that returned its argument
    # unchanged would satisfy the third assertion above on its own.
    assert _normalise(db, "Fœtal Hæmoglobin") != "Fœtal Hæmoglobin"


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_search_text_is_strict_so_null_does_not_fold_to_empty(db: Connection) -> None:
    """`STRICT` matters for correctness, not just tidiness: folding NULL to
    `''` would make every NULL-termed row share a trigram set with every
    other, so a short query would match rows with no term at all."""
    assert _normalise(db, None) is None  # type: ignore[arg-type]


#: How PostgreSQL renders the partial-index predicate back in `indexdef`.
#: Asserted rather than assumed: on every index carrying it, the predicate is
#: what makes a retired row unreachable, not a size optimisation.
_ACTIVE_ONLY = "WHERE (status = 'active'::text)"

#: Every index `nptc.catalogue.search._SEARCH_SQL` depends on, with what its
#: definition must contain. Nine entries for nine branches - the table is the
#: point, because the failure this module exists to catch is a branch that
#: quietly stops being index-supported, and a list that omitted one would
#: never notice.
_EXPECTED_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_catalogue_entry_preferred_term_trgm",
        ("USING gin", "nptc_search_text(preferred_term)", "gin_trgm_ops"),
    ),
    (
        "ix_catalogue_entry_preferred_term_fts",
        ("USING gin", "nptc_search_document(preferred_term)"),
    ),
    (
        "ix_designation_term_trgm",
        ("USING gin", "nptc_search_text(term)", "gin_trgm_ops", _ACTIVE_ONLY),
    ),
    ("ix_designation_term_fts", ("USING gin", "nptc_search_document(term)", _ACTIVE_ONLY)),
    (
        "ix_code_binding_fsn_trgm",
        ("USING gin", "nptc_search_text(fsn)", "gin_trgm_ops", _ACTIVE_ONLY),
    ),
    ("ix_code_binding_fsn_fts", ("USING gin", "nptc_search_document(fsn)", _ACTIVE_ONLY)),
    (
        "ix_code_binding_au_preferred_term_trgm",
        ("USING gin", "nptc_search_text(au_preferred_term)", "gin_trgm_ops", _ACTIVE_ONLY),
    ),
    (
        "ix_code_binding_au_preferred_term_fts",
        ("USING gin", "nptc_search_document(au_preferred_term)", _ACTIVE_ONLY),
    ),
    # btree, not gin: the code is matched by equality (ADR-0029).
    ("ix_code_binding_code", ("USING btree", "(code)", _ACTIVE_ONLY)),
)


@pytest.mark.req("FR-15")
@pytest.mark.integration
@pytest.mark.parametrize(("name", "fragments"), _EXPECTED_INDEXES, ids=lambda v: v)
def test_each_search_index_exists_over_the_expression_the_query_uses(
    db: Connection, name: str, fragments: tuple[str, ...]
) -> None:
    """Index *definitions*, not merely names: an index created over the raw
    column instead of `nptc_search_text(...)`/`nptc_search_document(...)`
    would still be present under the expected name while accelerating
    nothing the search predicate asks for.

    The `WHERE (status = 'active'::text)` fragment is asserted on all five
    `code_binding` indexes and on `ix_designation_term_fts` because a
    partial index is not an optimisation here - it is what makes a retired
    row unreachable, and dropping the predicate would silently make history
    searchable."""
    definition = db.execute(_INDEX_DEFINITION_SQL, {"name": name}).scalar_one()
    for fragment in fragments:
        assert fragment in definition, f"{name}: expected {fragment!r} in {definition}"


# --- the full-text half of the pair ---------------------------------------

_DOCUMENT_SQL = text("SELECT nptc_search_document(:value)")
_MATCH_SQL = text("SELECT nptc_search_document(:value) @@ nptc_search_query(:q)")


def _matches(db: Connection, value: str, q: str) -> bool:
    result: bool = db.execute(_MATCH_SQL, {"value": value, "q": q}).scalar_one()
    return result


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_the_document_and_query_functions_agree_with_each_other(db: Connection) -> None:
    """The property the pair exists for. A document lexed by one text search
    configuration and a query lexed by another share no stems and match
    nothing at all - a failure that is invisible in the index definition and
    shows up only as a search that silently returns less than it should."""
    assert _matches(db, "Haemoglobin electrophoresis", "haemoglobin electrophoresis")
    # Word order is irrelevant to a tsquery over a conjunction of lexemes.
    assert _matches(db, "Haemoglobin electrophoresis", "electrophoresis haemoglobin")


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_full_text_matches_an_inflected_form_that_trigram_scores_as_a_near_miss(
    db: Connection,
) -> None:
    """The half of FR-15 that the trigram scans do *not* cover, and therefore
    the justification for the second index family existing at all
    (ADR-0029).

    `english` stemming is what makes these match; under the `simple`
    configuration each pair lexes to two different lexemes and the FTS
    branches would find nothing trigram had not already found."""
    assert _matches(db, "Antibody screening", "antibodies screen")
    assert _matches(db, "Cultured cells", "culture cell")


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_the_document_function_folds_diacritics_like_its_trigram_counterpart(
    db: Connection,
) -> None:
    """Both halves normalise through `nptc_search_text`, so `muller` reaches
    `Müller` by either route. If only the trigram half folded, an accented
    term would be findable at one score and not at all at another."""
    assert _matches(db, "Müller cell antibody", "muller cell antibody")


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_the_document_function_is_strict_so_a_missing_label_indexes_as_null(
    db: Connection,
) -> None:
    """`au_preferred_term` is nullable, and this is what keeps a binding
    without one from indexing as an empty `tsvector` shared with every other
    such binding."""
    assert db.execute(_DOCUMENT_SQL, {"value": None}).scalar_one() is None


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_a_query_that_is_entirely_stopwords_matches_nothing_rather_than_everything(
    db: Connection,
) -> None:
    """`english` drops stopwords, so `the` lexes to an empty `tsquery`.

    An empty query matching everything would be the worst possible
    degradation - a page of noise a user cannot distinguish from a working
    search. `websearch_to_tsquery` yields an empty query and `@@` is false
    against it, so the FTS branches simply contribute nothing and the
    trigram branches still answer. No special case is needed in
    `_SEARCH_SQL`, which is why this is asserted here rather than assumed."""
    assert not _matches(db, "Haemoglobin electrophoresis", "the")


# --- the plan, not just the answer ----------------------------------------

#: Large enough that the trigram indexes are the cheapest way to answer the
#: predicate, not merely a legal one. Both halves of the query have a rival
#: plan the planner will otherwise prefer at small scale: `designation` has
#: `ix_designation_no_duplicate_active_term`, which can serve
#: `status = 'active'` on its own and leave the trigram match as a `Filter` -
#: cheaper than a GIN scan over a few hundred rows, and not cheaper at all
#: over a realistic catalogue. This figure is therefore about making the
#: fixture resemble a real catalogue's *shape*, not about winning an
#: arbitrary cost race.
_ROW_COUNT = 20_000

#: Raw INSERTs rather than the ORM: this test needs volume, not realism, and
#: individually flushed model instances would dominate its runtime.
#: `preferred_term_key`/`term_key` have `server_default ''`, so a raw INSERT
#: that bypasses the `@validates` hooks still satisfies `NOT NULL` - the same
#: allowance every other `test_db_*.py` constraint test relies on.
#:
#: The terms are md5 hex strings, deliberately dissimilar. A family like
#: 'fixture number 1', 'fixture number 2', ... would have every member
#: scoring well above the threshold against every other, so *every* row
#: would genuinely match any query drawn from it - and this test would then
#: be measuring the fixture rather than the index.
_BULK_ENTRIES_SQL = text("""
INSERT INTO catalogue_entry (business_key, preferred_term, status)
SELECT
    'NPTC-8' || lpad(g::text, 8, '0'),
    'assay ' || md5(g::text),
    'active'
FROM generate_series(1, :count) AS g
""")

_BULK_DESIGNATIONS_SQL = text("""
INSERT INTO designation (entry_id, term, use, language, status)
SELECT
    e.id,
    'synonym ' || md5(e.business_key),
    'synonym',
    'en-AU',
    'active'
FROM catalogue_entry AS e
WHERE e.business_key LIKE 'NPTC-8%'
""")

#: One active binding per bulk entry, so the five `code_binding` branches
#: have a table of realistic shape to plan against rather than an empty one.
#:
#: **Why the codes are generated by filtering rather than counted off.**
#: `code_binding.code` carries a `CHECK` calling `nptc_sctid_is_valid`, so a
#: sequence of consecutive integers would be rejected outright - roughly one
#: number in ten has a valid Verhoeff check digit. Generating a wide range
#: and keeping the ones that pass is the cheapest way to get 20,000 codes
#: that are valid by the same rule the catalogue enforces, rather than by a
#: second implementation of Verhoeff written for the fixture. The two
#: `row_number()`s then pair each surviving code with exactly one entry,
#: which is what `ix_code_binding_one_active_per_entry` (one active binding
#: per entry) and `ix_code_binding_one_active_entry_per_code` (one active
#: entry per code) between them require.
#:
#: The FSNs carry a `(procedure)` tag because a real one does and because
#: the trigram scores of a tagged and an untagged corpus differ - a fixture
#: without tags would be a slightly easier catalogue than the real thing.
_BULK_BINDINGS_SQL = text("""
INSERT INTO code_binding (entry_id, code, fsn, au_preferred_term, edition_hint, status)
SELECT
    e.id,
    c.code,
    'assay ' || md5(e.business_key) || ' (procedure)',
    'assay ' || md5(e.business_key),
    'int',
    'active'
FROM (
    SELECT
        inner_entry.id AS id,
        inner_entry.business_key AS business_key,
        row_number() OVER (ORDER BY inner_entry.business_key) AS rn
    FROM catalogue_entry AS inner_entry
    WHERE inner_entry.business_key LIKE 'NPTC-8%'
    LIMIT :bindings
) AS e
JOIN (
    SELECT
        g::text AS code,
        row_number() OVER (ORDER BY g) AS rn
    FROM generate_series(100000000, 100000000 + :candidates) AS g
    WHERE nptc_sctid_is_valid(g::text)
) AS c ON c.rn = e.rn
""")

#: How many integers to sieve for valid check digits. Roughly one in ten
#: passes, so this yields comfortably more than `_BINDING_COUNT` codes while
#: keeping the sieve - a recursive CTE evaluated per candidate - off the
#: critical path of a test whose subject is the query plan, not Verhoeff.
_CODE_CANDIDATES = 60_000

#: Fewer bindings than entries, and deliberately so. Not every catalogue
#: entry is bound to a SNOMED concept, so a binding table smaller than the
#: entry table is the realistic shape as well as the cheaper one - and the
#: assertion this fixture serves is that each branch *can* be answered from
#: its index, which does not turn on the row count. `_ROW_COUNT`'s own note
#: explains why the entry and designation tables do need volume: they have
#: rival indexes the planner would otherwise prefer, and `code_binding`'s
#: rivals (`ix_code_binding_one_active_per_entry`, keyed on `entry_id`)
#: cannot serve a text predicate at all.
_BINDING_COUNT = 5_000

_ONE_TERM_SQL = text(
    "SELECT preferred_term FROM catalogue_entry WHERE business_key = 'NPTC-800000123'"
)


def _explain(db: Connection, sql: str, params: dict[str, object]) -> str:
    # String concatenation into `text()` is confined to this test tree:
    # `test_sql_parameterisation.py` scans `backend/src` and
    # `backend/migrations` only, and the statement being explained is
    # `nptc.catalogue.search`'s own module-level literal, with every value
    # still bound as a parameter.
    rows = db.execute(text("EXPLAIN " + sql), params).scalars().all()
    return chr(10).join(rows)


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_the_real_search_query_plans_against_the_trigram_indexes(db: Connection) -> None:
    """`EXPLAIN` on the exact statement `nptc.catalogue.search` runs.

    Imports the module's private `_SEARCH_SQL` on purpose: explaining a
    hand-copied approximation of the query would be a test of the copy, and
    the copy is precisely what cannot drift when the real predicate does.

    **Why `enable_seqscan = off`, and why that is not cheating.** What this
    test needs to establish is that the predicate is *expressed so the index
    can serve it* - that the trigram indexes are a plan the planner is free
    to choose. Whether it chooses one on any given day is a cost decision
    that depends on table size, `random_page_cost` and how many trigrams the
    query string happens to have, and a test that turned on winning that
    cost race would be a test of the fixture's row count. Disabling
    sequential scans removes the cost race without weakening the assertion
    at all: an unindexable predicate - `similarity(...) >= 0.3` instead of
    `%`, or `lower(unaccent(term))` instead of `nptc_search_text(term)` -
    still cannot become an `Index Cond`, because no amount of cost
    persuasion makes an expression the index does not contain usable. It
    appears as a `Filter` on a (now expensive) sequential scan, and the
    assertions below fail exactly as they should.
    """
    from nptc.catalogue import search

    db.execute(_BULK_ENTRIES_SQL, {"count": _ROW_COUNT})
    db.execute(_BULK_DESIGNATIONS_SQL)
    db.execute(
        _BULK_BINDINGS_SQL,
        {"bindings": _BINDING_COUNT, "candidates": _CODE_CANDIDATES},
    )
    # Statistics, or the planner is working from defaults that have nothing
    # to do with the table in front of it. ANALYZE inside a transaction sees
    # this transaction's own uncommitted rows.
    db.execute(text("ANALYZE catalogue_entry"))
    db.execute(text("ANALYZE designation"))
    db.execute(text("ANALYZE code_binding"))
    # `is_local => true`, matching what `nptc.catalogue.search` itself does:
    # a session-scoped `set_limit()` here would survive this test's rollback
    # and change the threshold for every later test handed this connection.
    db.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', CAST(:threshold AS text), true)"),
        {"threshold": 0.3},
    )
    # LOCAL: reverted with this test's own transaction, so it cannot leak
    # into another test sharing the session-scoped container.
    db.execute(text("SET LOCAL enable_seqscan = off"))

    term: str = db.execute(_ONE_TERM_SQL).scalar_one()
    plan = _explain(
        db,
        str(search._SEARCH_SQL),
        {
            "q": term,
            "q_exact": term.strip(),
            "statuses": ["active"],
            "threshold": search.SIMILARITY_THRESHOLD,
            "exact_code_score": search.EXACT_CODE_SCORE,
            "exact_preferred_term_score": search.EXACT_PREFERRED_TERM_SCORE,
            "exact_label_score": search.EXACT_LABEL_SCORE,
            "preferred_term_weight": search.PREFERRED_TERM_WEIGHT,
            "designation_weight": search.DESIGNATION_WEIGHT,
            "binding_label_weight": search.BINDING_LABEL_WEIGHT,
            "after_score": None,
            "after_key": None,
            "limit": 51,
        },
    )

    # Every branch, by name. Asserting the count alone would let one index
    # be scanned twice while another was not scanned at all.
    for name, _fragments in _EXPECTED_INDEXES:
        assert f"Index Scan on {name}" in plan or f"Index Scan using {name}" in plan, (
            f"{name} is not in the plan - its branch is no longer index-supported\n{plan}"
        )

    # `Index Cond`, not `Filter`: the distinction between the index
    # answering the predicate and the index merely being read before the
    # predicate is applied by hand. Four trigram scans, four full-text
    # scans, and the code equality.
    assert plan.count("Index Cond: (nptc_search_text(") == 4, plan
    assert plan.count("Index Cond: (to_tsvector(") == 4, plan
    assert plan.count("Index Cond: (code = ") == 1, plan
    # The match predicates specifically must not be demoted to filters. The
    # threshold restatement *does* appear as a filter on the four trigram
    # branches, and correctly so - it is a recheck of an already-scanned
    # row, not the predicate that selected it - so it is spelled
    # `Filter: ((similarity(nptc_search_text(...` and does not collide with
    # this assertion.
    assert "Filter: (nptc_search_text(" not in plan, plan
    assert "Filter: (to_tsvector(" not in plan, plan


@pytest.mark.req("FR-14")
@pytest.mark.integration
def test_a_negation_only_query_plans_away_the_full_text_branches(db: Connection) -> None:
    """The plan-level half of the guard added in PR #237 review.

    `-glucose` lexes to `!'glucos'`, a tsquery `@@` satisfies for every row
    and GIN cannot probe - so the four full-text branches would each be a
    sequential scan over the whole table. `backend/tests/
    test_search_ranking.py` asserts the *answer* is not the whole catalogue;
    this asserts the *cost*, which is the half a functional test cannot see:
    a guard applied as a per-row filter would return the same empty result
    while still reading every row.

    `NOT ('' :: tsvector @@ nptc_search_query(:q))` depends only on `:q`, so
    the planner resolves it once and prunes the branch outright. Four
    `One-Time Filter: false` nodes is that pruning, one per full-text branch,
    and no `to_tsvector` index condition surviving is the same statement from
    the other side. No fixture rows are needed - a one-time filter is decided
    at plan time, not from statistics."""
    from nptc.catalogue import search

    plan = _explain(
        db,
        str(search._SEARCH_SQL),
        {
            "q": "-glucose",
            "q_exact": "-glucose",
            "statuses": ["active"],
            "threshold": search.SIMILARITY_THRESHOLD,
            "exact_code_score": search.EXACT_CODE_SCORE,
            "exact_preferred_term_score": search.EXACT_PREFERRED_TERM_SCORE,
            "exact_label_score": search.EXACT_LABEL_SCORE,
            "preferred_term_weight": search.PREFERRED_TERM_WEIGHT,
            "designation_weight": search.DESIGNATION_WEIGHT,
            "binding_label_weight": search.BINDING_LABEL_WEIGHT,
            "after_score": None,
            "after_key": None,
            "limit": 51,
        },
    )

    assert plan.count("One-Time Filter: false") == 4, plan
    assert "Index Cond: (to_tsvector(" not in plan, plan
    # The trigram branches are deliberately untouched: `%` has no negation,
    # so the query is still searched for the literal text the user typed.
    assert "Index Cond: (nptc_search_text(" in plan, plan


#: Deliberately not `nptc.catalogue.search.SIMILARITY_THRESHOLD`. It stands
#: in for "whatever the previous user of this pooled connection left behind",
#: and it has to differ from the module's own threshold for the assertion
#: below to be able to fail: a leak of 0.3 onto a connection whose threshold
#: was already `pg_trgm`'s 0.3 default is invisible.
_FOREIGN_THRESHOLD = "0.91"


@pytest.mark.req("FR-20")
@pytest.mark.integration
def test_the_similarity_threshold_reverts_when_the_transaction_ends(
    app_engine: Engine,
) -> None:
    """`pg_trgm.similarity_threshold` must be set at *transaction* scope, not
    session scope.

    This is why `nptc.catalogue.search` uses `set_config(..., is_local =>
    true)` rather than `set_limit()`. `set_limit` sets the GUC for the
    session, so the value survives the commit and follows the connection back
    into the pool - and the next, unrelated request handed that connection
    then runs its own `%` scans against a threshold it never asked for.

    Written against a connection of its own with real commits, because that
    is the only place the distinction is observable: inside the shared
    `app_db` transaction nothing ever commits, so a session-scoped and a
    transaction-scoped setting behave identically. The connection is
    invalidated rather than returned to the pool, so this test's own sentinel
    cannot become the leak it is testing for.
    """
    connection = app_engine.connect()
    try:
        # Session scope on purpose - the state a leak would look like.
        connection.execute(text(f"SET pg_trgm.similarity_threshold = {_FOREIGN_THRESHOLD}"))
        connection.commit()

        with Session(bind=connection) as session:
            from nptc.catalogue.search import search_entries

            search_entries(session, q="a query that need not match anything", limit=1)
            session.commit()

        after = connection.execute(
            text("SELECT current_setting('pg_trgm.similarity_threshold')")
        ).scalar_one()
    finally:
        connection.invalidate()
        connection.close()

    assert after == _FOREIGN_THRESHOLD, (
        "the search threshold outlived its transaction - it is being set at session "
        f"scope, and every later request on this connection now sees {after}"
    )
