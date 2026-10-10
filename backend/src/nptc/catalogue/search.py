"""Hybrid full-text and trigram search over the public catalogue (FR-14, FR-15).

Decisions: `docs/adr/0024-catalogue-search-and-pagination.md` (the cursor, the
threshold discipline, why not a search engine) and
`docs/adr/0029-hybrid-full-text-and-trigram-search.md` (the full-text half, which
supersedes ADR-0024's rejection of it). `docs/architecture/search.md` describes
the whole. The invariants below are the ones an editor must not break.

- **Both mechanisms run, and the better score per entry wins.** Trigram scores a
  transposition well and an inflected form poorly; full-text does the reverse.
  Each full-text contribution is rescaled onto `[SIMILARITY_THRESHOLD, 1]` before
  its weight applies, because raw `ts_rank_cd` tops out near 0.07 and `MAX` would
  otherwise mean "the trigram score if there was one" (ADR-0029).
- **Every branch is its own index scan**, nine in all (see `_SCORED_SQL`).
  `test_db_search_index.py` `EXPLAIN`s the composed statement and asserts an
  `Index Cond` on each.
- **`%`, not `similarity(...) > threshold`,** with `nptc_search_text(...)` applied
  to the column exactly as the index expression applies it. The GIN trigram index
  serves neither a bare `similarity()` comparison nor another spelling of the
  expression, so those plan a sequential scan and return the same answers.
- **The threshold is transaction-scoped, and restated on the raw similarity.**
  `set_config(..., is_local => true)` stops a pooled connection carrying the
  value into the next request. `scored`'s `WHERE trigram_score >= :threshold`
  then discards the weak rows a threshold left lower would admit. It cannot help
  against one left higher, which narrows the `%` scans themselves; the
  transaction scoping is what prevents that (ADR-0024, ADR-0029).
- **Every full-text branch is guarded against negation.** A `tsquery` that
  absence alone satisfies (`-glucose`, `zymogen or -kinase`) matches every row by
  sequential scan. `NOT (CAST('' AS tsvector) @@ nptc_search_query(:q))` prunes
  exactly that condition. It depends only on `:q`, so the planner skips the
  branch. The trigram branches need no guard, so the query still searches for
  what the user typed (ADR-0029).
- **Exact comparisons read `:q_exact`,** the stripped query, so pasted
  whitespace does not lose the exact band (ADR-0029).
- **Both SNOMED labels are searched tag-intact, exactly as stored** (FR-82). A
  SQL-side tag stripper would be a second copy of the semantic-tag regex
  (ADR-0029, ADR-0006).
- **All five of FR-14's fields are searched:** the preferred term, active
  synonyms, `fsn`, `au_preferred_term` and the SNOMED code
  (`test_search_ranking.py`).
- **`business_key` is the tie-break.** Scores tie constantly, so score alone is
  not a total order and a page boundary inside a tie would drop or repeat rows.
  The cursor is `"<score>:<request digest>:<business_key>"`. The digest binds it
  to the `q`, filter set and status scope that minted it, because a score means
  something only against that request (ADR-0024, ADR-0032).
- **`_SCORED_SQL` is a literal; the statement around it is Core.** The literal
  binds parameters only (NFR-22, `test_sql_parameterisation.py`). It is used as a
  CTE because FR-16's filter predicates are derived per request, and a fixed
  literal cannot grow a clause without becoming string-built SQL. The result page
  and every facet count share one predicate list, so they answer the same
  question about the same population.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final

from sqlalchemy import ColumnElement, Select, Uuid, and_, cast, column, or_, select, text
from sqlalchemy.dialects.postgresql import REAL
from sqlalchemy.orm import Session

from nptc.catalogue.entries import BUSINESS_KEY_PATTERN
from nptc.catalogue.facets import (
    Facet,
    FacetContext,
    FilterSelection,
    compute_facets,
    filter_digest_material,
    filter_predicates,
)
from nptc.catalogue.queries import PUBLIC_STATUSES
from nptc.db.models.catalogue_entry import CatalogueEntry

__all__ = [
    "BINDING_LABEL_WEIGHT",
    "DESIGNATION_WEIGHT",
    "EXACT_CODE_SCORE",
    "EXACT_LABEL_SCORE",
    "EXACT_PREFERRED_TERM_SCORE",
    "PREFERRED_TERM_WEIGHT",
    "SIMILARITY_THRESHOLD",
    "EmptySearchQueryError",
    "MalformedSearchCursorError",
    "SearchCursorQueryMismatchError",
    "SearchHit",
    "SearchPage",
    "apply_similarity_threshold",
    "search_entries",
    "search_facets",
]

#: `pg_trgm`'s own default, kept rather than inventing a number. Raise it in
#: response to noise, never lower it: matching everything is the principal
#: failure mode of a text search (ADR-0024).
SIMILARITY_THRESHOLD: Final[float] = 0.3

#: The score tiers and per-source weights that make FR-14's ranking true by
#: arithmetic: an exact code match and an exact preferred-term match outrank a
#: fuzzy synonym hit.
#:
#: Every fuzzy contribution is a `similarity()` or `ts_rank_cd()` result (at most
#: 1.0) times its source's weight, so none can exceed `PREFERRED_TERM_WEIGHT`.
#: Both exact tiers sit above that ceiling, and the exact code tier above them,
#: for every possible input.
#: `test_search_ranking.py::test_the_score_bands_cannot_overlap` asserts the
#: inequality directly.
#:
#: Weights are relative source trust, not tuned constants, and nothing finer is
#: claimed for the specific figures: there is no production query log yet
#: (ADR-0029).
EXACT_CODE_SCORE: Final[float] = 1.0
EXACT_PREFERRED_TERM_SCORE: Final[float] = 0.99
EXACT_LABEL_SCORE: Final[float] = 0.95
PREFERRED_TERM_WEIGHT: Final[float] = 0.90
DESIGNATION_WEIGHT: Final[float] = 0.80
BINDING_LABEL_WEIGHT: Final[float] = 0.75

#: `:` cannot occur in a score, a hex digest or a `business_key`
#: (`^NPTC-[0-9]{6,}$`, FR-03), so the split needs no escaping.
_CURSOR_SEPARATOR: Final[str] = ":"

#: 8 bytes (16 hex characters) of BLAKE2s. Not a MAC and not keyed: a cursor is
#: not a capability, and a key would only add a secret to manage (NFR-26). It
#: detects a cursor replayed under a different request, which is a client bug
#: (ADR-0024).
_CURSOR_QUERY_DIGEST_BYTES: Final[int] = 8

#: Transaction-scoped (`is_local => true`), so the value reverts on commit
#: instead of following the connection back into the pool. `set_config`, not
#: `SET LOCAL`, because `SET` takes no bound parameter and NFR-22 forbids
#: interpolating one. The `text` cast is needed because a bound Python float
#: arrives as `double precision` and `set_config` takes `text`. The module
#: docstring covers the restatement in `scored`'s `WHERE`.
_SET_THRESHOLD_SQL = text(
    "SELECT set_config('pg_trgm.similarity_threshold', CAST(:threshold AS text), true)"
)


def apply_similarity_threshold(session: Session) -> None:
    """Sets `SIMILARITY_THRESHOLD` for the `%` operator on this transaction, so every trigram
    match in the platform agrees on what "similar" means. It reverts on commit."""
    session.execute(_SET_THRESHOLD_SQL, {"threshold": SIMILARITY_THRESHOLD})


#: The scoring half, and only the scoring half: `(entry_id, score)`, typed
#: through `.columns(...)` so it works as a CTE (see the module docstring).
#: Reading it inside out, the `matches` subquery is nine index-supported scans
#: (a trigram `%` and a full-text `@@` over each of the four searchable text
#: columns, plus one equality scan on the SNOMED code), and the outer aggregate
#: keeps an entry's best score.
#:
#: Each scan is a separate `UNION ALL` branch, because `a % q OR a @@ q` over two
#: indexes on one column plans as a sequential scan (ADR-0024, ADR-0029).
#:
#: Full-text recall has no threshold, so a common domain word (`test`, `level`)
#: matches much of the catalogue at a score near the floor. That is accepted
#: until a production query log exists to tune a rank floor against (ADR-0029).
#:
#: `:q_exact` is `q.strip()`. The exact comparisons cannot use SQL `btrim`, which
#: trims spaces only, and cannot change `nptc_search_text`, which is the trigram
#: indexes' own expression (ADR-0029). Only the query side is trimmed: a stored
#: label with stray whitespace is a defect to fix where it is written.
#:
#: The `d.status = 'active'` and `cb.status = 'active'` literals match the
#: partial-index predicates exactly, and a bound parameter would stop the
#: planner proving the index covers the query. The entry status filter is
#: parameterised (`:statuses`) because the entry-side indexes are not partial on
#: status.
#:
#: The threshold restatement is `trigram_score >= :threshold` on the raw
#: similarity, not a `HAVING` over the weighted `MAX`, which would raise the
#: effective threshold for every source but the highest-weighted (ADR-0029). The
#: full-text and code branches carry `NULL` there, because the `%` threshold has
#: no meaning for `@@` or `=`.
#:
#: `build_search_statement` adds the keyset predicate.
_SCORED_SQL = text("""
WITH matches AS (
    SELECT
        entry.id AS entry_id,
        similarity(nptc_search_text(entry.preferred_term), nptc_search_text(:q))
            AS trigram_score,
        CASE
            WHEN nptc_search_text(entry.preferred_term) = nptc_search_text(:q_exact)
                THEN CAST(:exact_preferred_term_score AS real)
            ELSE similarity(nptc_search_text(entry.preferred_term), nptc_search_text(:q))
                 * CAST(:preferred_term_weight AS real)
        END AS score
    FROM catalogue_entry AS entry
    WHERE entry.status = ANY(:statuses)
      AND nptc_search_text(entry.preferred_term) % nptc_search_text(:q)
    UNION ALL
    SELECT
        entry.id,
        CAST(NULL AS real),
        (CAST(:threshold AS real) + (1 - CAST(:threshold AS real))
         * ts_rank_cd(nptc_search_document(entry.preferred_term), nptc_search_query(:q), 32))
            * CAST(:preferred_term_weight AS real)
    FROM catalogue_entry AS entry
    WHERE entry.status = ANY(:statuses)
      AND NOT (CAST('' AS tsvector) @@ nptc_search_query(:q))
      AND nptc_search_document(entry.preferred_term) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        d.entry_id,
        similarity(nptc_search_text(d.term), nptc_search_text(:q)),
        CASE
            WHEN nptc_search_text(d.term) = nptc_search_text(:q_exact)
                THEN CAST(:exact_label_score AS real)
            ELSE similarity(nptc_search_text(d.term), nptc_search_text(:q))
                 * CAST(:designation_weight AS real)
        END
    FROM designation AS d
    WHERE d.status = 'active'
      AND nptc_search_text(d.term) % nptc_search_text(:q)
    UNION ALL
    SELECT
        d.entry_id,
        CAST(NULL AS real),
        (CAST(:threshold AS real) + (1 - CAST(:threshold AS real))
         * ts_rank_cd(nptc_search_document(d.term), nptc_search_query(:q), 32))
            * CAST(:designation_weight AS real)
    FROM designation AS d
    WHERE d.status = 'active'
      AND NOT (CAST('' AS tsvector) @@ nptc_search_query(:q))
      AND nptc_search_document(d.term) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        similarity(nptc_search_text(cb.fsn), nptc_search_text(:q)),
        CASE
            WHEN nptc_search_text(cb.fsn) = nptc_search_text(:q_exact)
                THEN CAST(:exact_label_score AS real)
            ELSE similarity(nptc_search_text(cb.fsn), nptc_search_text(:q))
                 * CAST(:binding_label_weight AS real)
        END
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND nptc_search_text(cb.fsn) % nptc_search_text(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        CAST(NULL AS real),
        (CAST(:threshold AS real) + (1 - CAST(:threshold AS real))
         * ts_rank_cd(nptc_search_document(cb.fsn), nptc_search_query(:q), 32))
            * CAST(:binding_label_weight AS real)
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND NOT (CAST('' AS tsvector) @@ nptc_search_query(:q))
      AND nptc_search_document(cb.fsn) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        similarity(nptc_search_text(cb.au_preferred_term), nptc_search_text(:q)),
        CASE
            WHEN nptc_search_text(cb.au_preferred_term) = nptc_search_text(:q_exact)
                THEN CAST(:exact_label_score AS real)
            ELSE similarity(nptc_search_text(cb.au_preferred_term), nptc_search_text(:q))
                 * CAST(:binding_label_weight AS real)
        END
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND nptc_search_text(cb.au_preferred_term) % nptc_search_text(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        CAST(NULL AS real),
        (CAST(:threshold AS real) + (1 - CAST(:threshold AS real))
         * ts_rank_cd(nptc_search_document(cb.au_preferred_term), nptc_search_query(:q), 32))
            * CAST(:binding_label_weight AS real)
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND NOT (CAST('' AS tsvector) @@ nptc_search_query(:q))
      AND nptc_search_document(cb.au_preferred_term) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        CAST(NULL AS real),
        CAST(:exact_code_score AS real)
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND cb.code = :q_exact
)
SELECT
    m.entry_id AS entry_id,
    MAX(m.score) AS score
FROM matches AS m
WHERE m.trigram_score IS NULL
   OR m.trigram_score >= :threshold
GROUP BY m.entry_id
""").columns(column("entry_id", Uuid), column("score", REAL))


class EmptySearchQueryError(ValueError):
    """Raised for a `q` that is empty or only whitespace.

    A refusal, not an empty result and not "every entry": returning either for a
    query the user never typed hides a broken client. 422 rather than 400,
    because the request is well formed and its parameter value is unprocessable.
    """

    http_status: ClassVar[int] = 422


class MalformedSearchCursorError(ValueError):
    """Raised for an `after` cursor this module did not mint.

    Refused rather than ignored: falling back to page one would make a client's
    paging loop restart forever, which looks like a slow catalogue rather than an
    error.
    """

    http_status: ClassVar[int] = 422


class SearchCursorQueryMismatchError(MalformedSearchCursorError):
    """Raised for a well-formed cursor minted for a different request (`q`, filter
    set or status scope).

    A subclass, so `nptc.api.errors` maps it to the same 422 without a second
    handler; the distinction only matters in the log. Refused rather than served,
    because a score is meaningful only against the request that produced it
    (ADR-0024).
    """


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One entry, with the score that got it here.

    The same fields as an entry summary plus `score`, and not an entry id: a
    search result points to `/catalogue/entries/{business_key}`, a public
    identifier (PRD SS6.2).

    `row_version` is carried unconditionally. Withholding it from the public
    surface is the HTTP response model's job (`catalogue_shared.SearchHit` never
    reads it), and `catalogue_admin.py`'s `AdminSearchHit` is the caller that
    does.
    """

    business_key: str
    preferred_term: str
    status: str
    updated_at: datetime
    row_version: int
    score: float


@dataclass(frozen=True, slots=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    next_cursor: str | None


def _query_digest(q: str) -> str:
    """The cursor's `q` fingerprint (`_CURSOR_QUERY_DIGEST_BYTES` explains why a
    plain digest).

    `q` is fingerprinted exactly as sent, without normalisation: the digest asks
    "is this the same request", and normalising would accept a cursor under a
    query that differs by more than the folded whitespace (`nptc_search_text`
    also strips diacritics, which changes scores).
    """
    return hashlib.blake2s(q.encode("utf-8"), digest_size=_CURSOR_QUERY_DIGEST_BYTES).hexdigest()


def _status_digest_material(statuses: Sequence[str]) -> str:
    """The cursor digest's fingerprint of the status scope.

    A cursor minted under one scope is a fact about that population only, so a
    cursor from `GET /catalogue/search` must not resume the keyset on
    `GET /catalogue/admin/search` (ADR-0024).

    Sorted and netstring-encoded like `filter_digest_material`, so order does not
    matter and a value cannot run together with its neighbour.
    """
    values = "".join(f"{len(status.encode())}:{status}" for status in sorted(statuses))
    return f"{len(values.encode())}:{values}"


def _request_digest(
    q: str, filters: Sequence[FilterSelection], statuses: Sequence[str] = PUBLIC_STATUSES
) -> str:
    """The cursor's fingerprint of the whole request: `q`, the filter set and the
    status scope (`_status_digest_material`).

    `q` is length-prefixed (`<byte length>:<q>`, as in `filter_digest_material`)
    because `q` is arbitrary text and could end in something that parses as a
    prefix of the filter material, letting two different requests concatenate to
    one digest input. A separator alone does not rule that out (ADR-0032).
    """
    return _query_digest(
        f"{len(q.encode())}:{q}{filter_digest_material(filters)}{_status_digest_material(statuses)}"
    )


def _format_cursor(
    hit: SearchHit,
    *,
    q: str,
    filters: Sequence[FilterSelection],
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> str:
    return _CURSOR_SEPARATOR.join(
        (repr(hit.score), _request_digest(q, filters, statuses), hit.business_key)
    )


def _parse_cursor(
    cursor: str,
    *,
    q: str,
    filters: Sequence[FilterSelection],
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> tuple[float, str]:
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
    # `float()` also accepts `inf`, `-inf` and `nan`, which defeat the keyset:
    # `score < 'inf'` is true for every row (page one again, forever) and `nan`
    # makes every comparison false (a permanently empty page). The digest is no
    # defence, because it is unkeyed and anyone can compute it for their own `q`.
    if not math.isfinite(score):
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} does not begin with a finite score"
        )
    # The same pattern `/catalogue/entries` validates its own `after` against.
    if not BUSINESS_KEY_PATTERN.fullmatch(business_key):
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} does not end with a well-formed business key"
        )
    # `compare_digest` rather than `==`, not because this is a secret, but so that
    # nobody later "optimises" a digest comparison into a prefix check.
    if not hmac.compare_digest(digest, _request_digest(q, filters, statuses)):
        raise SearchCursorQueryMismatchError(
            f"search cursor {cursor!r} was issued for a different query, filter set, or "
            "status scope"
        )
    return score, business_key


def _text_parameters(q: str, *, statuses: Sequence[str] = PUBLIC_STATUSES) -> dict[str, Any]:
    """Every value `_SCORED_SQL` binds.

    One function because two statements use the CTE (the result page and the
    combined facet aggregation), and two copies of this dict would be two places
    for a weight to go stale.

    `statuses` defaults to `PUBLIC_STATUSES`; the maintenance search passes
    `MAINTENANCE_STATUSES`. Only the entry-side branches read it (see
    `_SCORED_SQL`, on the status literals).
    """
    return {
        "q": q,
        # The exact-match half of `q`, trimmed with Python's `str.strip()`, not SQL
        # `btrim` (see `_SCORED_SQL`). Bound separately because the trigram and
        # full-text branches and the cursor digest use `q` as sent.
        "q_exact": q.strip(),
        "statuses": list(statuses),
        "threshold": SIMILARITY_THRESHOLD,
        # Bound, not interpolated (NFR-22), so tests can import the constants and
        # assert the band inequality against the numbers the query uses.
        "exact_code_score": EXACT_CODE_SCORE,
        "exact_preferred_term_score": EXACT_PREFERRED_TERM_SCORE,
        "exact_label_score": EXACT_LABEL_SCORE,
        "preferred_term_weight": PREFERRED_TERM_WEIGHT,
        "designation_weight": DESIGNATION_WEIGHT,
        "binding_label_weight": BINDING_LABEL_WEIGHT,
    }


def _select_from_scored(
    scored: Any,
    columns: Sequence[Any],
    predicates: Sequence[ColumnElement[bool]],
    *,
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> Select[*tuple[Any, ...]]:
    """The one join between `catalogue_entry` and the scored CTE, filtered to
    `statuses` and this request's filter predicates.

    `_matching_entry_ids` and `build_search_statement` both call this, so a change
    to the join or the status filter cannot reach one and miss the other and let
    the page and a facet count answer different questions. `columns` is the only
    thing that varies.
    """
    return (
        select(*columns)
        .select_from(CatalogueEntry)
        .join(scored, scored.c.entry_id == CatalogueEntry.id)
        .where(CatalogueEntry.status.in_(statuses))
        .where(*predicates)
    )


def _matching_entry_ids(
    scored: Any,
    predicates: Sequence[ColumnElement[bool]],
    *,
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> Select[uuid.UUID]:
    """The entry ids `q` and `predicates` between them select, so the result page
    and every facet count answer the same question about the same population
    (`test_api_public_search.py` has the count/result parity test).
    """
    return _select_from_scored(scored, [CatalogueEntry.id], predicates, statuses=statuses)


def build_search_statement(
    *,
    filters: Sequence[FilterSelection] = (),
    after_score: float | None = None,
    after_key: str | None = None,
    limit: int,
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> Select[str, str, str, bool, datetime, int, float]:
    """The composed result statement `search_entries` runs.

    Public because `test_db_search_index.py` `EXPLAIN`s the statement the module
    actually runs, not a hand-copied approximation. `q` is not an argument: the
    plan depends on the statement's shape, and `q` arrives as a bound parameter
    (`_text_parameters`).

    The keyset predicate is omitted on the first page: an untyped `$n IS NULL`
    gives PostgreSQL nothing to infer a parameter type from.

    `REAL`, not `double precision`, on the cursor comparison. `similarity()`
    returns `real`, so `MAX(m.score)` is `real`, and the cursor carries it through
    a Python float and back. Comparing in double precision would make the tie
    branch exact only while float4 to float8 round-trips invariantly, and the tie
    branch keeps a page boundary inside a tie from dropping or repeating a row.
    """
    scored = _SCORED_SQL.cte("scored")
    statement = (
        _select_from_scored(
            scored,
            [
                CatalogueEntry.business_key,
                CatalogueEntry.preferred_term,
                CatalogueEntry.status,
                CatalogueEntry.updated_at,
                CatalogueEntry.row_version,
                scored.c.score.label("score"),
            ],
            filter_predicates(filters),
            statuses=statuses,
        )
        .order_by(scored.c.score.desc(), CatalogueEntry.business_key.asc())
        # One more row than asked for, exactly as `list_entries` does: its
        # existence is what decides `next_cursor`.
        .limit(limit + 1)
    )
    if after_score is not None and after_key is not None:
        after = cast(after_score, REAL)
        statement = statement.where(
            or_(
                scored.c.score < after,
                and_(scored.c.score == after, CatalogueEntry.business_key > after_key),
            )
        )
    return statement


def search_entries(
    session: Session,
    *,
    q: str,
    limit: int,
    after: str | None = None,
    filters: Sequence[FilterSelection] = (),
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> SearchPage:
    """One keyset page of entries matching `q` and `filters`, best first.

    Raises `EmptySearchQueryError` for a blank query before any SQL runs,
    `MalformedSearchCursorError` for an `after` this module did not produce, and
    its `SearchCursorQueryMismatchError` subclass for one produced for a different
    `q`, filter set or status scope (`_request_digest`).

    `statuses` defaults to `PUBLIC_STATUSES`. `/catalogue/admin/search` passes
    `MAINTENANCE_STATUSES`, so one ranking and paging implementation serves both
    surfaces.
    """
    if not q.strip():
        raise EmptySearchQueryError(
            "a search query must contain at least one non-whitespace character"
        )

    after_score: float | None = None
    after_key: str | None = None
    if after is not None:
        after_score, after_key = _parse_cursor(after, q=q, filters=filters, statuses=statuses)

    # Transaction-scoped, and restated in `scored`'s WHERE (see the module
    # docstring).
    apply_similarity_threshold(session)

    statement = build_search_statement(
        filters=filters,
        after_score=after_score,
        after_key=after_key,
        limit=limit,
        statuses=statuses,
    )
    rows = session.execute(statement, _text_parameters(q, statuses=statuses)).all()

    hits = tuple(
        SearchHit(
            business_key=row.business_key,
            preferred_term=row.preferred_term,
            status=row.status,
            updated_at=row.updated_at,
            row_version=row.row_version,
            score=float(row.score),
        )
        for row in rows
    )
    if len(hits) > limit:
        page = hits[:limit]
        cursor = _format_cursor(page[-1], q=q, filters=filters, statuses=statuses)
        return SearchPage(hits=page, next_cursor=cursor)
    return SearchPage(hits=hits, next_cursor=None)


def search_facets(
    session: Session,
    *,
    q: str,
    context: FacetContext,
    filters: Sequence[FilterSelection] = (),
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> tuple[Facet, ...]:
    """Every facet's buckets, counted over the entries `q` matches.

    Separate from `search_entries` because a page is one slice of the keyset and a
    facet count is over all of it. They share the scored CTE, which keeps them
    consistent (`_matching_entry_ids`).

    The threshold is set here too: these are separate statements over the same `%`
    scans, and a facet count at a different threshold from its page would be
    quietly wrong.

    `statuses` must match what `search_entries` got for the same request.
    """
    if not q.strip():
        raise EmptySearchQueryError(
            "a search query must contain at least one non-whitespace character"
        )
    apply_similarity_threshold(session)
    scored = _SCORED_SQL.cte("scored")
    return compute_facets(
        session,
        context=context,
        selections=filters,
        base_entry_ids=lambda predicates: _matching_entry_ids(
            scored, predicates, statuses=statuses
        ),
        params=_text_parameters(q, statuses=statuses),
    )
