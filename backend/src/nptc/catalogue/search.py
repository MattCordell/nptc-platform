"""Trigram search over the public catalogue (issue #142, FR-14, FR-15).

See `docs/adr/0024-catalogue-search-and-pagination.md` for the decision
record, including why `pg_trgm` rather than `tsvector` (a lexeme match
scores a transposition at zero, and FR-15 requires tolerating exactly that)
and why not a search engine (ADR-0001 already ruled one out).

**One raw statement, and it is a module-level literal.** `_SEARCH_SQL` below
is plain text with bound parameters only - no f-string, no concatenation, no
identifier interpolation (NFR-22, statically enforced by `backend/tests/
test_sql_parameterisation.py`). It is spelled as SQL rather than assembled
with the ORM because the shape that makes the trigram indexes usable - two
separately-indexed `%` scans unioned, then aggregated per entry - is
considerably clearer written out than expressed as a Core construct, and
because the exact text is what `backend/tests/test_db_search_index.py`
`EXPLAIN`s.

**Why `%` and not `similarity(...) > threshold`.** The GIN trigram index
supports the `%` operator; it cannot accelerate a bare comparison of a
`similarity()` result, so writing the predicate that way plans a sequential
scan over the whole catalogue while returning byte-identical answers. That
is the failure this module's `EXPLAIN` test exists for, and the reason the
predicate applies `nptc_search_text(...)` to the *column* exactly as the
index expression does: any other spelling (`lower(unaccent(term))`, say) is
an expression the planner has no reason to match against the index.

**Why the threshold is stated twice.** `%` compares against
`pg_trgm.similarity_threshold`, which is set per *connection* by
`set_limit()`. Connections here come from a pool and outlive a request, so a
threshold set for one request is a threshold in force for the next caller to
be handed that connection - and a future code path calling `set_limit` with
a different value would silently change this query's meaning. The `HAVING`
clause re-asserts `>= :threshold` on the score directly, which makes the
result set correct regardless of the connection's session state; the
`set_limit` call is then purely an index-selectivity hint, not the
definition of a match.

**Relevance keyset.** The cursor is `"<score>:<business_key>"` - both values
the client just received, neither an internal id. `business_key` is the
tie-break, and it is not optional decoration: trigram scores are floats over
a small catalogue and tie constantly, so ordering by score alone is not a
total order and a page boundary landing inside a tie would drop or repeat
rows. `backend/tests/test_api_public_search.py` pages across a deliberate
tie for exactly this reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from nptc.catalogue.queries import PUBLIC_STATUSES

__all__ = [
    "SIMILARITY_THRESHOLD",
    "EmptySearchQueryError",
    "MalformedSearchCursorError",
    "SearchHit",
    "SearchPage",
    "search_entries",
]

#: `pg_trgm`'s own default is 0.3, and this keeps it rather than inventing a
#: number. The tuning direction that matters is documented in ADR-0024: the
#: principal failure mode of a text search is matching everything (a user
#: cannot tell a bad result from a broken catalogue), which is what lowering
#: this produces - so it is raised, never lowered, in response to noise.
SIMILARITY_THRESHOLD: Final[float] = 0.3

#: The cursor separator. `:` cannot occur in either half - a score is a
#: float and a `business_key` matches `^NPTC-[0-9]{6,}$` (FR-03) - so the
#: split is unambiguous without escaping.
_CURSOR_SEPARATOR: Final[str] = ":"

_SET_LIMIT_SQL = text("SELECT set_limit(:threshold)")

#: One statement. Reading it from the inside out: the `UNION ALL` subquery is
#: two index-supported `%` scans, one per searchable column; the outer
#: `GROUP BY` collapses an entry matched by several of its own synonyms into
#: a single row scored by its best match; the `HAVING` applies both the
#: real threshold and the keyset predicate.
#:
#: `d.status = 'active'` is written as a literal, matching
#: `ix_designation_term_trgm`'s own partial-index predicate exactly - a
#: bound parameter there would leave the planner unable to prove the partial
#: index covers the query. The entry status filter *is* parameterised
#: (`:statuses`), because `PUBLIC_STATUSES` is a Python constant the tests
#: import and the entry-side index is not partial on status anyway.
_SEARCH_SQL = text("""
SELECT
    e.business_key,
    e.preferred_term,
    e.status,
    e.specimen_unconstrained,
    e.updated_at,
    MAX(m.score) AS score
FROM (
    SELECT
        entry.id AS entry_id,
        similarity(nptc_search_text(entry.preferred_term), nptc_search_text(:q)) AS score
    FROM catalogue_entry AS entry
    WHERE entry.status = ANY(:statuses)
      AND nptc_search_text(entry.preferred_term) % nptc_search_text(:q)
    UNION ALL
    SELECT
        d.entry_id AS entry_id,
        similarity(nptc_search_text(d.term), nptc_search_text(:q)) AS score
    FROM designation AS d
    WHERE d.status = 'active'
      AND nptc_search_text(d.term) % nptc_search_text(:q)
) AS m
JOIN catalogue_entry AS e ON e.id = m.entry_id
WHERE e.status = ANY(:statuses)
GROUP BY
    e.business_key,
    e.preferred_term,
    e.status,
    e.specimen_unconstrained,
    e.updated_at
HAVING MAX(m.score) >= :threshold
   AND (
        :after_score IS NULL
        OR MAX(m.score) < :after_score
        OR (MAX(m.score) = :after_score AND e.business_key > :after_key)
   )
ORDER BY MAX(m.score) DESC, e.business_key ASC
LIMIT :limit
""")


class EmptySearchQueryError(ValueError):
    """Raised for a `q` that is empty or only whitespace.

    A refusal, not an empty result and not "every entry": a query that
    matches nothing is a legitimate answer to a real question, and returning
    it for a query the user never actually typed hides a broken client from
    both of them. 422 rather than 400 - it is a well-formed request whose
    parameter value is unprocessable.
    """

    http_status: ClassVar[int] = 422


class MalformedSearchCursorError(ValueError):
    """Raised for an `after` cursor this module did not mint.

    Refused rather than ignored. Silently falling back to page one would
    make a client's paging loop restart forever - a bug that looks like a
    slow catalogue rather than an error - and silently treating an
    unparseable score as "no cursor" would do the same.
    """

    http_status: ClassVar[int] = 422


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One entry, with the score that got it here.

    Deliberately the same field set as an entry summary plus `score`, and
    deliberately not an entry id: a search result is a pointer to
    `/catalogue/entries/{business_key}`, which is a public identifier
    (PRD SS6.2).
    """

    business_key: str
    preferred_term: str
    status: str
    specimen_unconstrained: bool
    updated_at: datetime
    score: float


@dataclass(frozen=True, slots=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    next_cursor: str | None


def _format_cursor(hit: SearchHit) -> str:
    return f"{hit.score!r}{_CURSOR_SEPARATOR}{hit.business_key}"


def _parse_cursor(cursor: str) -> tuple[float, str]:
    score_text, separator, business_key = cursor.partition(_CURSOR_SEPARATOR)
    if not separator or not business_key:
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} is not '<score>:<business_key>'"
        )
    try:
        score = float(score_text)
    except ValueError:
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} does not begin with a numeric score"
        ) from None
    return score, business_key


def search_entries(session: Session, *, q: str, limit: int, after: str | None = None) -> SearchPage:
    """One keyset page of active entries matching `q`, best first.

    Raises `EmptySearchQueryError` before any SQL runs for a blank query,
    and `MalformedSearchCursorError` for an `after` value this module did
    not produce.
    """
    if not q.strip():
        raise EmptySearchQueryError(
            "a search query must contain at least one non-whitespace character"
        )

    after_score: float | None = None
    after_key: str | None = None
    if after is not None:
        after_score, after_key = _parse_cursor(after)

    # Connection-scoped, and re-asserted in the statement's own HAVING - see
    # the module docstring on why both.
    session.execute(_SET_LIMIT_SQL, {"threshold": SIMILARITY_THRESHOLD})

    rows = session.execute(
        _SEARCH_SQL,
        {
            "q": q,
            "statuses": list(PUBLIC_STATUSES),
            "threshold": SIMILARITY_THRESHOLD,
            "after_score": after_score,
            "after_key": after_key,
            # One more row than asked for, exactly as `list_entries` does:
            # its existence is what decides `next_cursor`.
            "limit": limit + 1,
        },
    ).all()

    hits = tuple(
        SearchHit(
            business_key=row.business_key,
            preferred_term=row.preferred_term,
            status=row.status,
            specimen_unconstrained=row.specimen_unconstrained,
            updated_at=row.updated_at,
            score=float(row.score),
        )
        for row in rows
    )
    if len(hits) > limit:
        page = hits[:limit]
        return SearchPage(hits=page, next_cursor=_format_cursor(page[-1]))
    return SearchPage(hits=hits, next_cursor=None)
