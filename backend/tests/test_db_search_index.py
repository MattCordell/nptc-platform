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


@pytest.mark.req("FR-15")
@pytest.mark.integration
def test_both_trigram_indexes_exist_over_the_normalised_expression(db: Connection) -> None:
    """Index *definitions*, not merely names: an index created over the raw
    column instead of `nptc_search_text(...)` would still be present under
    the expected name while accelerating nothing the search predicate asks
    for."""
    entry_def = db.execute(
        _INDEX_DEFINITION_SQL, {"name": "ix_catalogue_entry_preferred_term_trgm"}
    ).scalar_one()
    designation_def = db.execute(
        _INDEX_DEFINITION_SQL, {"name": "ix_designation_term_trgm"}
    ).scalar_one()

    assert "USING gin" in entry_def
    assert "nptc_search_text(preferred_term)" in entry_def
    assert "gin_trgm_ops" in entry_def

    assert "USING gin" in designation_def
    assert "nptc_search_text(term)" in designation_def
    assert "gin_trgm_ops" in designation_def
    # Partial: search never matches a retired synonym, so the index covers
    # only the rows a result can come from.
    assert "WHERE (status = 'active'::text)" in designation_def


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
    from nptc.catalogue.search import _SEARCH_SQL, SIMILARITY_THRESHOLD

    db.execute(_BULK_ENTRIES_SQL, {"count": _ROW_COUNT})
    db.execute(_BULK_DESIGNATIONS_SQL)
    # Statistics, or the planner is working from defaults that have nothing
    # to do with the table in front of it. ANALYZE inside a transaction sees
    # this transaction's own uncommitted rows.
    db.execute(text("ANALYZE catalogue_entry"))
    db.execute(text("ANALYZE designation"))
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
        str(_SEARCH_SQL),
        {
            "q": term,
            "statuses": ["active"],
            "threshold": SIMILARITY_THRESHOLD,
            "after_score": None,
            "after_key": None,
            "limit": 51,
        },
    )

    assert "Bitmap Index Scan on ix_catalogue_entry_preferred_term_trgm" in plan, plan
    assert "Bitmap Index Scan on ix_designation_term_trgm" in plan, plan
    # `Index Cond`, not `Filter`: the distinction between the index
    # answering the predicate and the index merely being read before the
    # predicate is applied by hand.
    assert plan.count("Index Cond: (nptc_search_text(") == 2, plan
    assert "Filter: (nptc_search_text(" not in plan, plan


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
