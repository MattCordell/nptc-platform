"""The search normalisation function and its two trigram indexes
(issue #142, migration 0011, FR-14, FR-15).

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
from sqlalchemy.engine import Connection

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
