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

**Why the threshold is stated twice, and what the second statement does
*not* buy.** `%` compares against `pg_trgm.similarity_threshold`, a GUC.
`set_limit()` would set it at *session* scope, which is a hazard here and
not a small one: connections come from a pool and outlive a request, so a
threshold set for one request stays in force for the next caller handed
that connection. It is therefore set with `set_config(..., is_local =>
true)` instead, which scopes the value to the current *transaction* - it
reverts on commit, and the connection goes back to the pool as it was
found. `SET LOCAL` says the same thing more directly but takes no bound
parameter, and NFR-22 rules out interpolating the value into the statement
text, so `set_config` is the parameterised spelling of `SET LOCAL`.

The `HAVING` clause then re-asserts `>= :threshold` on the score directly.
Stating precisely what that protects against matters, because the loose
version of the claim - "correct regardless of session state" - is false.
`HAVING` can only *discard* rows the inner `%` scans already returned. So
it does defend against a threshold left **lower** than this module's: the
extra, weaker matches such a threshold admits are filtered back out, and
the answer is unchanged. It cannot defend against one left **higher**: a
raised threshold narrows the inner `%` index scans themselves, and no
`HAVING` recovers a row that was never scanned. The transaction scoping
above is what keeps that second case from arising at all; the `HAVING` is
the belt to its braces, and specifically the reason a future code path
setting the GUC lower cannot silently *broaden* this query.

**Relevance keyset.** The cursor is `"<score>:<query digest>:<business_key>"`
- the score and key are both values the client just received, neither an
internal id, and the digest binds the cursor to the `q` that minted it.
`business_key` is the tie-break, and it is not optional decoration:
trigram scores are floats over a small catalogue and tie constantly, so
ordering by score alone is not a total order and a page boundary landing
inside a tie would drop or repeat rows. The digest exists because a score
is only meaningful against the query it was computed for: replaying a
cursor under a different `q` would otherwise be served a window with no
defined meaning, silently, which is worse than a refusal.
`backend/tests/test_api_public_search.py` pages across a deliberate tie,
and replays a cursor under a second query, for exactly these reasons.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.catalogue.queries import PUBLIC_STATUSES

__all__ = [
    "SIMILARITY_THRESHOLD",
    "EmptySearchQueryError",
    "MalformedSearchCursorError",
    "SearchCursorQueryMismatchError",
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

#: The cursor separator. `:` cannot occur in any of the three parts - a
#: score is a float, the query digest is hex, and a `business_key` matches
#: `^NPTC-[0-9]{6,}$` (FR-03) - so the split is unambiguous without
#: escaping.
_CURSOR_SEPARATOR: Final[str] = ":"

#: 8 bytes (16 hex characters) of BLAKE2s over `q`. Deliberately *not* a
#: MAC and deliberately not keyed: a cursor is not a capability - it grants
#: nothing a caller could not ask for directly with `?q=` - so there is
#: nothing here to authenticate, and a keyed digest would only add a secret
#: to manage (NFR-26). What this detects is a cursor *replayed under a
#: different query*, which is a client bug; 64 bits is far more than enough
#: to make an accidental collision impossible in practice.
_CURSOR_QUERY_DIGEST_BYTES: Final[int] = 8

#: Transaction-scoped, not session-scoped: `is_local => true` is what makes
#: the value revert on commit rather than following the connection back into
#: the pool. `set_config` rather than `SET LOCAL` because `SET` accepts no
#: bound parameter and NFR-22 forbids interpolating one into the text; the
#: `text` cast is needed because `set_config`'s second argument is declared
#: `text` and a bound Python float arrives as `double precision`. See the
#: module docstring on why the statement's own `HAVING` restates this too.
_SET_THRESHOLD_SQL = text(
    "SELECT set_config('pg_trgm.similarity_threshold', CAST(:threshold AS text), true)"
)

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
#:
#: The `CAST(...)` around the two keyset parameters is required rather than
#: decorative: on the first page both are `NULL`, and `$n IS NULL` gives
#: PostgreSQL nothing at all to infer a parameter type from, so the server
#: refuses the statement outright ("could not determine data type of
#: parameter"). Naming the type is what makes the same statement serve both
#: the first page and every page after it, rather than needing two.
#:
#: `:after_score` is cast to `real`, not `double precision`, and the choice
#: is load-bearing. `similarity()` returns `real`, so `MAX(m.score)` is a
#: `real`; the cursor carries that value through a Python `float` (a
#: double) and back. Casting the parameter to `double precision` would make
#: the tie branch's `=` a comparison in double precision, exact only for as
#: long as float4 -> text -> float8 happens to round-trip invariantly for
#: this driver and result format. Casting to `real` puts the comparison
#: back in the type the column actually is, so the tie branch is exact by
#: construction - and it is the tie branch that keeps a page boundary
#: inside a score tie from dropping or repeating a row.
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
        CAST(:after_score AS real) IS NULL
        OR MAX(m.score) < CAST(:after_score AS real)
        OR (
            MAX(m.score) = CAST(:after_score AS real)
            AND e.business_key > CAST(:after_key AS text)
        )
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


class SearchCursorQueryMismatchError(MalformedSearchCursorError):
    """Raised for a well-formed cursor minted for a *different* `q`.

    A subclass, so `nptc.api.errors` maps it to the same 422 and the same
    client-facing sentence without a second handler - from a caller's point
    of view it is one fault ("this cursor is not usable on this request"),
    and the distinction is only useful in the log, which records the
    exception class.

    Refused rather than served, because a score is only meaningful against
    the query that produced it: `score < :after_score` under a different
    `q` selects a window that is neither the next page of the new query nor
    of the old one. An empty-looking or arbitrarily-truncated result set is
    the kind of silently wrong answer a client has no way to detect.
    """


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


def _query_digest(q: str) -> str:
    """The cursor's `q` fingerprint. See `_CURSOR_QUERY_DIGEST_BYTES` on why
    a plain digest and not a MAC.

    `q` is fingerprinted exactly as the caller sent it, with no
    normalisation: the digest's job is "is this the same request", and a
    normalising digest would accept a cursor under a query that differs by
    more than the whitespace it folded - `nptc_search_text` normalisation
    also strips diacritics, which changes the scores.
    """
    return hashlib.blake2s(q.encode("utf-8"), digest_size=_CURSOR_QUERY_DIGEST_BYTES).hexdigest()


def _format_cursor(hit: SearchHit, *, q: str) -> str:
    return _CURSOR_SEPARATOR.join((repr(hit.score), _query_digest(q), hit.business_key))


def _parse_cursor(cursor: str, *, q: str) -> tuple[float, str]:
    score_text, separator, remainder = cursor.partition(_CURSOR_SEPARATOR)
    digest, key_separator, business_key = remainder.partition(_CURSOR_SEPARATOR)
    if not separator or not key_separator:
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} is not '<score>:<query digest>:<business_key>'"
        )
    try:
        score = float(score_text)
    except ValueError:
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} does not begin with a numeric score"
        ) from None
    # The same pattern `/catalogue/entries` validates its own `after`
    # against: a cursor's key half is a `business_key` (FR-03), and an
    # endpoint that instead paged from "whatever this sorts after" would
    # give a client corrupting its own cursor no way to notice.
    if not BUSINESS_KEY_PATTERN.fullmatch(business_key):
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} does not end with a well-formed business key"
        )
    # `compare_digest` rather than `==`: not because this is a secret, but
    # because it is the spelling that does not invite someone to later
    # "optimise" a digest comparison into a prefix check.
    if not hmac.compare_digest(digest, _query_digest(q)):
        raise SearchCursorQueryMismatchError(
            f"search cursor {cursor!r} was issued for a different query"
        )
    return score, business_key


def search_entries(session: Session, *, q: str, limit: int, after: str | None = None) -> SearchPage:
    """One keyset page of active entries matching `q`, best first.

    Raises `EmptySearchQueryError` before any SQL runs for a blank query,
    `MalformedSearchCursorError` for an `after` value this module did not
    produce, and its `SearchCursorQueryMismatchError` subclass for one it
    produced for a different `q`.
    """
    if not q.strip():
        raise EmptySearchQueryError(
            "a search query must contain at least one non-whitespace character"
        )

    after_score: float | None = None
    after_key: str | None = None
    if after is not None:
        after_score, after_key = _parse_cursor(after, q=q)

    # Transaction-scoped, and re-asserted in the statement's own HAVING -
    # see the module docstring on why both, and on what the HAVING does and
    # does not protect against.
    session.execute(_SET_THRESHOLD_SQL, {"threshold": SIMILARITY_THRESHOLD})

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
        return SearchPage(hits=page, next_cursor=_format_cursor(page[-1], q=q))
    return SearchPage(hits=hits, next_cursor=None)
