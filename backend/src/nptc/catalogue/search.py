"""Hybrid full-text and trigram search over the public catalogue
(issues #142 and #138, FR-14, FR-15).

See `docs/adr/0024-catalogue-search-and-pagination.md` for the original
decision record - the cursor shape, the threshold discipline, and why not a
search engine (ADR-0001 already ruled one out) - and
`docs/adr/0029-hybrid-full-text-and-trigram-search.md`, which supersedes
ADR-0024's rejection of full-text search and records what changed.

The short version of that change: ADR-0024 chose trigram alone because a
`tsvector` match is lexeme equality after stemming and so scores a
transposition at exactly zero, which is precisely what FR-15 requires
tolerating. That remains true, and it is why the trigram scans are still
here. What ADR-0024 deferred, and #138 settles, is that the converse gap is
real too: trigram scores an inflected form as a near-miss and penalises a
short query against a long FSN by the length ratio alone. The two mechanisms
fail in opposite directions, so the query runs both and keeps the better
score per entry rather than choosing between them.

**All five of FR-14's fields, in one query field.** The catalogue's own
preferred term, its active synonyms, the stored `fsn`, the stored
`au_preferred_term`, and the SNOMED code. A user typing `49466006`, `ACTH`,
`Adrenocorticotropic hormone` or `Corticotropin` reaches the same entry -
the PRD's own worked example, asserted as a test in
`backend/tests/test_search_ranking.py`.

The two SNOMED labels are searched **tag-intact, exactly as stored**
(FR-82). There is no stripped second copy and no SQL-side tag stripper:
the semantic tag is extra text to trigram and its own lexeme to full-text,
so an FSN searched with its tag typed in full and the same FSN with the tag
omitted both reach the entry through the one index. ADR-0029 records why the
alternative was refused - a `nptc_strip_semantic_tag(text)` in the database
would be a second copy of the semantic-tag regex, which ADR-0006 identifies
as the defect class FR-83 exists to prevent.

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

`scored`'s own `WHERE` then re-asserts `trigram_score >= :threshold`
directly. Stating precisely what that protects against matters, because the
loose version of the claim - "correct regardless of session state" - is
false. That filter can only *discard* rows the inner `%` scans already
returned. So it does defend against a threshold left **lower** than this
module's: the extra, weaker matches such a threshold admits are filtered
back out, and the answer is unchanged. It cannot defend against one left
**higher**: a raised threshold narrows the inner `%` index scans themselves,
and no later filter recovers a row that was never scanned. The transaction
scoping above is what keeps that second case from arising at all; the
restatement is the belt to its braces, and specifically the reason a future
code path setting the GUC lower cannot silently *broaden* this query.

ADR-0024 put that restatement in a `HAVING` over `MAX(score)`. It cannot
live there now that scores are weighted per source - a genuine trigram match
at 0.35 from a source weighted 0.75 scores 0.26, and a `HAVING` on the
weighted value would silently raise the effective threshold for every source
except the highest-weighted one. It is applied to the raw `similarity()`
instead, which is the value the GUC actually governs, and the full-text and
code branches carry `NULL` there because `%`'s threshold has no meaning for
an `@@` or an `=` test.

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
    "search_entries",
]

#: `pg_trgm`'s own default is 0.3, and this keeps it rather than inventing a
#: number. The tuning direction that matters is documented in ADR-0024: the
#: principal failure mode of a text search is matching everything (a user
#: cannot tell a bad result from a broken catalogue), which is what lowering
#: this produces - so it is raised, never lowered, in response to noise.
SIMILARITY_THRESHOLD: Final[float] = 0.3

#: The score tiers and per-source weights that make FR-14's ranking
#: requirement - "an exact code match and an exact preferred-term match
#: outrank a fuzzy synonym hit" - true by arithmetic rather than by hope.
#:
#: The mechanism is a set of disjoint bands. Every fuzzy contribution is a
#: `similarity()` or a `ts_rank_cd()` result, both of which are at most 1.0,
#: multiplied by its source's weight - so no fuzzy match from any source can
#: exceed `PREFERRED_TERM_WEIGHT`. Both exact tiers sit strictly above that
#: ceiling, and the exact code tier strictly above them. The ordering the
#: acceptance criterion asks for is therefore not a tuning outcome that could
#: regress under a different corpus; it holds for every possible input, and
#: `test_search_ranking.py::test_the_score_bands_cannot_overlap` asserts the
#: inequality directly rather than inferring it from one worked example.
#:
#: Weights are *relative source trust*, not tuned constants. The catalogue's
#: own preferred term is the label RCPA curates and is what an entry is
#: called; a synonym is a real but secondary way in; the two stored SNOMED
#: labels are what a terminology server served for the bound concept, which
#: is authoritative about SNOMED and only indirectly about this entry. The
#: gaps between them are what stop a long FSN's incidental word overlap from
#: outranking a genuine synonym match, and nothing finer is claimed for the
#: specific figures - there is no production query log yet (ADR-0029).
EXACT_CODE_SCORE: Final[float] = 1.0
EXACT_PREFERRED_TERM_SCORE: Final[float] = 0.99
EXACT_LABEL_SCORE: Final[float] = 0.95
PREFERRED_TERM_WEIGHT: Final[float] = 0.90
DESIGNATION_WEIGHT: Final[float] = 0.80
BINDING_LABEL_WEIGHT: Final[float] = 0.75

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

#: One statement. Reading it from the inside out: the `matches` subquery is
#: nine index-supported scans - a trigram `%` scan and a full-text `@@` scan
#: over each of the four searchable text columns, plus one equality scan on
#: the SNOMED code; the `scored` subquery collapses an entry matched several
#: ways into a single row carrying its best score; the outer query joins back
#: for the served columns and applies the keyset predicate.
#:
#: **Why nine branches and not one predicate with `OR`.** Every branch is a
#: separate index scan because that is the only shape in which each one *is*
#: an index scan. `a % q OR a @@ q` over two different indexes on the same
#: column plans as a sequential scan with both tests as filters - the same
#: class of defect ADR-0024 recorded for `similarity(...) >= 0.3`, invisible
#: to every functional test and visible only as a slow catalogue.
#: `backend/tests/test_db_search_index.py` `EXPLAIN`s this statement and
#: asserts an `Index Cond` on each of the nine.
#:
#: **Why both a trigram and a full-text scan per column.** They fail in
#: opposite directions and neither is a superset of the other. A `tsvector`
#: match is lexeme equality after stemming, so it scores a transposition at
#: exactly zero - FR-15's typo half is entirely trigram's. A trigram set is
#: unordered, so it handles word-order variation well, but it scores an
#: inflected or pluralised form as a near-miss and it penalises a short query
#: against a long FSN by the length ratio alone - those are full-text's.
#: `MAX` over the branches means an entry found both ways keeps whichever
#: score is better rather than being averaged into the middle.
#:
#: **The status literals.** `d.status = 'active'` and `cb.status = 'active'`
#: are written as literals, matching the partial-index predicates on
#: `ix_designation_term_*` and all five `ix_code_binding_*` exactly - a bound
#: parameter there would leave the planner unable to prove the partial index
#: covers the query. The entry status filter *is* parameterised
#: (`:statuses`), because `PUBLIC_STATUSES` is a Python constant the tests
#: import and the entry-side indexes are not partial on status anyway.
#:
#: **Where the threshold restatement went, and why it is still the same
#: defence.** ADR-0024 put `MAX(score) >= :threshold` in a `HAVING`. It
#: cannot stay there now that scores are weighted: a genuine trigram match at
#: 0.35 from a source weighted 0.75 scores 0.26, and a `HAVING` on the
#: weighted value would silently raise the effective threshold per source.
#: The restatement is instead `trigram_score >= :threshold` on the *raw*
#: similarity in `scored`'s `WHERE`, which is exactly the value the GUC
#: governs. That preserves the property ADR-0024 actually argued for - a
#: threshold left **lower** by another code path admits extra weak matches to
#: the `%` scans and they are filtered back out here - and it still cannot
#: defend against one left **higher**, which narrows the index scans
#: themselves. The transaction scoping is what keeps that case from arising.
#: Full-text and code branches carry `NULL` here rather than a number,
#: because `pg_trgm.similarity_threshold` has no meaning for them: `@@` and
#: `=` are exact tests with no threshold to restate.
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
WITH matches AS (
    SELECT
        entry.id AS entry_id,
        similarity(nptc_search_text(entry.preferred_term), nptc_search_text(:q))
            AS trigram_score,
        CASE
            WHEN nptc_search_text(entry.preferred_term) = nptc_search_text(:q)
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
        ts_rank_cd(nptc_search_document(entry.preferred_term), nptc_search_query(:q), 32)
            * CAST(:preferred_term_weight AS real)
    FROM catalogue_entry AS entry
    WHERE entry.status = ANY(:statuses)
      AND nptc_search_document(entry.preferred_term) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        d.entry_id,
        similarity(nptc_search_text(d.term), nptc_search_text(:q)),
        CASE
            WHEN nptc_search_text(d.term) = nptc_search_text(:q)
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
        ts_rank_cd(nptc_search_document(d.term), nptc_search_query(:q), 32)
            * CAST(:designation_weight AS real)
    FROM designation AS d
    WHERE d.status = 'active'
      AND nptc_search_document(d.term) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        similarity(nptc_search_text(cb.fsn), nptc_search_text(:q)),
        CASE
            WHEN nptc_search_text(cb.fsn) = nptc_search_text(:q)
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
        ts_rank_cd(nptc_search_document(cb.fsn), nptc_search_query(:q), 32)
            * CAST(:binding_label_weight AS real)
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND nptc_search_document(cb.fsn) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        similarity(nptc_search_text(cb.au_preferred_term), nptc_search_text(:q)),
        CASE
            WHEN nptc_search_text(cb.au_preferred_term) = nptc_search_text(:q)
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
        ts_rank_cd(nptc_search_document(cb.au_preferred_term), nptc_search_query(:q), 32)
            * CAST(:binding_label_weight AS real)
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND nptc_search_document(cb.au_preferred_term) @@ nptc_search_query(:q)
    UNION ALL
    SELECT
        cb.entry_id,
        CAST(NULL AS real),
        CAST(:exact_code_score AS real)
    FROM code_binding AS cb
    WHERE cb.status = 'active'
      AND cb.code = btrim(:q)
), scored AS (
    SELECT
        m.entry_id AS entry_id,
        MAX(m.score) AS score
    FROM matches AS m
    WHERE m.trigram_score IS NULL
       OR m.trigram_score >= :threshold
    GROUP BY m.entry_id
)
SELECT
    e.business_key,
    e.preferred_term,
    e.status,
    e.specimen_unconstrained,
    e.updated_at,
    s.score AS score
FROM scored AS s
JOIN catalogue_entry AS e ON e.id = s.entry_id
WHERE e.status = ANY(:statuses)
  AND (
       CAST(:after_score AS real) IS NULL
       OR s.score < CAST(:after_score AS real)
       OR (
           s.score = CAST(:after_score AS real)
           AND e.business_key > CAST(:after_key AS text)
       )
  )
ORDER BY s.score DESC, e.business_key ASC
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
            # Bound, not interpolated, for the same reason every other value
            # here is (NFR-22) - and bound rather than written into the
            # statement text so the tests can import the constants and assert
            # the band inequality against the same numbers the query uses.
            "exact_code_score": EXACT_CODE_SCORE,
            "exact_preferred_term_score": EXACT_PREFERRED_TERM_SCORE,
            "exact_label_score": EXACT_LABEL_SCORE,
            "preferred_term_weight": PREFERRED_TERM_WEIGHT,
            "designation_weight": DESIGNATION_WEIGHT,
            "binding_label_weight": BINDING_LABEL_WEIGHT,
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
