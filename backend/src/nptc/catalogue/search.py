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

**What an `@@` match is worth, and why its score is rescaled.** The two
mechanisms do not produce comparable numbers. `similarity()` is a ratio in
`[0, 1]` that uses its whole range; `ts_rank_cd(..., 32)` is a cover-density
figure squashed through `rank / (rank + 1)`, and in this catalogue's text a
*complete* three-lexeme match measures 0.0909 - so a raw weighted `ts_rank_cd`
tops out around 0.07. Left unscaled, `MAX` would not be "the better of the
two" at all: it would be "the trigram score if there was one, otherwise a
near-zero floor", and an entry found only by an inflected form would sort
below every barely-admissible typo match in the catalogue. Each full-text
contribution is therefore mapped onto `[threshold, 1]` before its weight is
applied. The anchor is not a tuned number: `SIMILARITY_THRESHOLD` is the
similarity at which a trigram match is admitted at all, so the weakest
admissible match of either kind now enters at exactly the same rank and
neither mechanism is systematically preferred. What this does *not* claim is
a well-spread full-text ranking - `ts_rank_cd`'s output is dense near zero,
so in practice a full-text contribution sits near its floor and orders only
*within* the full-text branch. Fixing that properly needs a production query
log to calibrate against (ADR-0029), which does not exist yet.

**Negation, and why every full-text branch is guarded.** `websearch_to_tsquery`
gives callers `-` for NOT, and a tsquery that can be satisfied by *absence
alone* matches every row: `@@` is true wherever the excluded lexeme is
missing, GIN has no positive key to probe, and the branch degrades to a
sequential scan returning the entire catalogue at a floor score. That is the
"matching everything" failure this module's threshold discipline exists to
prevent, reachable from one character on an unauthenticated endpoint, and no
nonsense-query test catches it because a nonsense *word* still has a positive
lexeme.

Each full-text branch therefore carries
`NOT (CAST('' AS tsvector) @@ nptc_search_query(:q))`. A tsquery matches the
empty document exactly when nothing positive is *required* of a row, which is
the condition itself rather than a scan for `-`, and it is deliberately wider
than "every word was excluded": `-glucose` lexes to `!'glucos'` and so does
`a -b`, whose positive half is a stopword, but so is `zymogen or -kinase`
(`'zymogen' | !'kinas'`) caught - a disjunction only one branch of which is
positive is still satisfied by absence, so the `!'kinas'` half alone would
return the catalogue. `vitamin -d` (`'vitamin' & !'d'`) is *not* caught,
correctly: the conjunction still requires a lexeme. The predicate depends only
on `:q`, so the planner resolves it to `One-Time Filter: false` and skips the
branch rather than evaluating it per row.

The trigram branches need no guard - `%` has no negation - so any of these
queries still searches, by similarity, for the string the user actually typed.
That is what makes over-pruning a degradation rather than a failure: the
positive word in `zymogen or -kinase` is still matched, by the other
mechanism.

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

**The scoring half is a module-level literal; the shell around it is
Core.** `_SCORED_SQL` below is plain text with bound parameters only - no
f-string, no concatenation, no identifier interpolation (NFR-22, statically
enforced by `backend/tests/test_sql_parameterisation.py`). It is spelled as
SQL rather than assembled with the ORM because the shape that makes the
trigram indexes usable - two separately-indexed `%` scans unioned, then
aggregated per entry - is considerably clearer written out than expressed as
a Core construct.

What is *not* a literal any more (issue #139) is the query built around it.
`_SCORED_SQL` is exposed as a CTE through `.columns(...)`, and the result
page and every facet count are Core selects over that one CTE. They have to
be: FR-16's filters are derived from the property registry at request time,
so the predicate list is not knowable when this file is written, and a
statement that has to grow a clause per request cannot be a fixed literal
without becoming string-built SQL - the exact thing NFR-22 forbids.
Composing it as Core keeps every value bound by construction. It also keeps
the result page and the facet counts answering the same question about the
same population, because they share one predicate list rather than two
hand-kept-in-step copies. `backend/tests/test_db_search_index.py` `EXPLAIN`s
the *composed* statement, via `build_search_statement`, for the same reason
it used to explain the raw one: an approximation is a test of the copy.

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

**Relevance keyset.** The cursor is
`"<score>:<request digest>:<business_key>"` - the score and key are both
values the client just received, neither an internal id, and the digest
binds the cursor to the request that minted it: the `q`, (issue #139) the
filter set, and (issue #266) the status scope, all alongside it.
`business_key` is the tie-break, and it is not optional decoration:
trigram scores are floats over a small catalogue and tie constantly, so
ordering by score alone is not a total order and a page boundary landing
inside a tie would drop or repeat rows. The digest exists because a score
is only meaningful against the request it was computed for:
replaying a cursor under a different `q`, a different filter set, or a
different status scope - each changes which entries exist to be scored at
all - would otherwise be served a window with no defined meaning, silently,
which is worse than a refusal. The status scope matters for the identical
reason: `GET /catalogue/search`'s cursor and `GET /catalogue/admin/search`'s
score the same `q` against different populations (`PUBLIC_STATUSES` vs
`MAINTENANCE_STATUSES`), so a cursor minted by one accepted by the other
would silently resume the keyset over the wrong population rather than
being refused. `backend/tests/test_api_public_search.py` pages across a
deliberate tie and replays a cursor under a second query and under a
second filter set; `backend/tests/test_api_catalogue_admin_listing.py`
replays one across the public/admin status-scope boundary, both directions.
"""

from __future__ import annotations

import hashlib
import hmac
import math
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
    "search_entries",
    "search_facets",
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
#: module docstring on why `scored`'s own `WHERE` restates this too.
_SET_THRESHOLD_SQL = text(
    "SELECT set_config('pg_trgm.similarity_threshold', CAST(:threshold AS text), true)"
)

#: The scoring half, and only the scoring half. Reading it from the inside
#: out: the `matches` subquery is nine index-supported scans - a trigram `%`
#: scan and a full-text `@@` scan over each of the four searchable text
#: columns, plus one equality scan on the SNOMED code; the outer aggregate
#: collapses an entry matched several ways into a single row carrying its
#: best score.
#:
#: It yields `(entry_id, score)` and stops there. `.columns(...)` gives those
#: two an explicit type so the statement can be used as a CTE, which is what
#: `build_search_statement` and `search_facets` each compose over: the served
#: columns, the status filter, the FR-16 filter predicates and the keyset
#: predicate are all Core, because the filter list is derived per request and
#: cannot be a fixed literal (see the module docstring).
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
#: score is better rather than being averaged into the middle - which is only
#: meaningful because the full-text contribution is rescaled onto the trigram
#: range first. See the module docstring: raw `ts_rank_cd` is an order of
#: magnitude smaller and `MAX` over it would silently mean "trigram if there
#: was one".
#:
#: **What `@@` does not have, and trigram does.** `%` compares against a
#: threshold, so trigram recall is bounded; `@@` has no analogue, and any
#: single shared lexeme after stemming admits a row. A common domain word -
#: `test`, `level`, `measurement` - therefore matches a large fraction of the
#: catalogue at a score near the floor. That is a recall and plan-cost
#: consequence rather than a wrong answer (page one is still the best
#: matches, and the ordering above is what decides it), and it is accepted
#: deliberately: a minimum-rank floor would be an invented constant, and
#: ADR-0024's discipline of raising a threshold only against observed noise
#: needs a production query log this platform does not have yet. ADR-0029
#: records it as an open consequence rather than leaving it implied.
#:
#: **`:q_exact`, and why the exact comparisons do not use `:q`.**
#: `nptc_search_text` lowercases and unaccents but does not trim, so a
#: preferred term pasted with surrounding whitespace is not equal to the
#: stored one while its `similarity()` is 1.0 - the exact band would be
#: missed and the hit would score as fuzzy, beneath an exact synonym match
#: on a *different* entry. All five exact comparisons therefore read
#: `:q_exact`, which `search_entries` strips in Python.
#:
#: Not `btrim(:q)` in SQL, which is what the code branch used to do:
#: `btrim(text)` trims **spaces only**, and a single cell copied out of a
#: spreadsheet ends in a carriage return and a newline (PR #237 review).
#: Not inside `nptc_search_text` either - that function is the four trigram
#: indexes' own expression, so changing it would mean rebuilding them for a
#: difference `similarity()` cannot see.
#:
#: Only the query side is trimmed. A *stored* label with surrounding
#: whitespace is a data defect to fix where it is written, not something to
#: mask on every read, and masking it here would hide it from the export
#: and the ValueSet as well.
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
#: The keyset predicate no longer lives here at all: it is added by
#: `build_search_statement`, which also records why the old
#: `CAST(:after_score AS real) IS NULL OR ...` spelling is gone and why the
#: `real` cast on the comparison is still load-bearing.
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

    `row_version` (issue #267) is a domain fact about the row, carried here
    unconditionally - the withholding of it from the *public* surface is the
    HTTP response model's job (`catalogue_shared.SearchHit` never reads this
    field), not this module's. `catalogue_admin.py`'s `AdminSearchHit` is
    the one caller that does.
    """

    business_key: str
    preferred_term: str
    status: str
    specimen_unconstrained: bool
    updated_at: datetime
    row_version: int
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


def _status_digest_material(statuses: Sequence[str]) -> str:
    """The cursor digest's fingerprint of the status scope (issue #266
    review).

    `search_entries`/`search_facets` take `statuses` so one surface's
    default (`PUBLIC_STATUSES`) and another's (`MAINTENANCE_STATUSES`) share
    every other line of ranking and paging code - but a cursor minted under
    one scope is a fact about *that* population only. Without this, a
    `next_cursor` from `GET /catalogue/search` is accepted verbatim by
    `GET /catalogue/admin/search` for the same `q` and filter set (and vice
    versa) and resumes the `(score, business_key)` keyset over a *different*
    population - an administrator paging from a public cursor would silently
    skip every draft/deprecated/withdrawn entry scoring above it, rather than
    getting the refusal a mismatched `q` already earns.

    Sorted and netstring-encoded exactly like `filter_digest_material`'s own
    values, for the same two reasons: order must not matter (`statuses` is a
    set of permitted values, not a meaningful sequence) and a value must not
    be able to run together with its neighbour.
    """
    values = "".join(f"{len(status.encode())}:{status}" for status in sorted(statuses))
    return f"{len(values.encode())}:{values}"


def _request_digest(
    q: str, filters: Sequence[FilterSelection], statuses: Sequence[str] = PUBLIC_STATUSES
) -> str:
    """The cursor's fingerprint of the *whole* request, not just `q`
    (issue #139), including the status scope it was scored under (issue
    #266 - see `_status_digest_material`).

    A score is meaningful only against the request that produced it, and
    the filter set is as much a part of that request as `q` is: narrowing
    the filters changes which entries exist to be scored, so `score <
    :after_score` under a different filter set selects a window that is
    neither the next page of the new request nor of the old one. That is
    the silently-wrong answer `SearchCursorQueryMismatchError` already
    exists to refuse for a changed `q`; a changed filter set - or a changed
    status scope - is the same fault and earns the same refusal.

    `q` is length-prefixed (`<byte length>:<q>`, matching
    `filter_digest_material`'s own netstring encoding) rather than
    concatenated directly in front of the filter material. Nothing stops
    `q` from ending in text that happens to parse as a well-formed prefix
    of whatever filter material follows it, in which case a different
    (`q`, `filters`) pair could concatenate to an identical digest input -
    a separator character alone does not rule this out, since `q` is
    arbitrary caller-supplied text and can contain it. Length-prefixing `q`
    closes that the same way `filter_digest_material` closes the
    equivalent collision for a filter value: an unambiguous length in place
    of a separator or a framing invariant that has to be trusted to hold.
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
    # `float()` also accepts `inf`, `-inf` and `nan`, none of which this
    # module ever mints, and each of which defeats the keyset rather than
    # advancing it: `score < 'inf'` is true for every row, so a client is
    # handed page one again and pages forever - the exact failure
    # `MalformedSearchCursorError` exists to refuse - while `nan` makes every
    # comparison false and yields a permanently empty page. Neither is
    # reachable by accident, but the digest is no defence here: it is
    # unkeyed by design (see `_CURSOR_QUERY_DIGEST_BYTES`), so anyone
    # replaying their own `q` can compute it. A cheap finiteness check is.
    if not math.isfinite(score):
        raise MalformedSearchCursorError(
            f"search cursor {cursor!r} does not begin with a finite score"
        )
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
    if not hmac.compare_digest(digest, _request_digest(q, filters, statuses)):
        raise SearchCursorQueryMismatchError(
            f"search cursor {cursor!r} was issued for a different query, filter set, or "
            "status scope"
        )
    return score, business_key


def _text_parameters(q: str, *, statuses: Sequence[str] = PUBLIC_STATUSES) -> dict[str, Any]:
    """Every value `_SCORED_SQL` binds.

    Kept in one function because the CTE is now used by several statements
    - the result page, and one aggregation per facet - and several copies
    of this dict would be several places for a weight to go stale.

    `statuses` defaults to `PUBLIC_STATUSES` for every existing caller
    (`/catalogue/search`); issue #266's maintenance search passes
    `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` instead. Only the
    entry-side branches ever read it - see `_SCORED_SQL`'s own docstring on
    why the `designation`/`code_binding` status literals stay `'active'`
    regardless.
    """
    return {
        "q": q,
        # The exact-match half of `q`, trimmed. Python's `str.strip()`
        # rather than SQL's `btrim(text)`, which trims spaces *only*: a
        # cell copied out of a spreadsheet ends in a carriage return and
        # a newline, and either would defeat every exact band while
        # `similarity()` went on scoring the same value 1.0 (PR #237
        # review). Bound separately rather than replacing `q` - the
        # trigram and full-text branches, and the cursor digest, all use
        # the string the caller actually sent.
        "q_exact": q.strip(),
        "statuses": list(statuses),
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
    }


def _select_from_scored(
    scored: Any,
    columns: Sequence[Any],
    predicates: Sequence[ColumnElement[bool]],
    *,
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> Select[Any]:
    """The one join between `catalogue_entry` and the scored CTE, filtered to
    `statuses` and this request's filter predicates.

    Both `_matching_entry_ids` and `build_search_statement` call this rather
    than each writing their own `.join(...).where(...)` - the earlier
    version had the result page re-derive the same join and status filter
    independently, which meant a future change to either applied to one
    call site and not the other would let the result page and a facet count
    silently answer different questions about different populations, with
    nothing to catch it but the count/result parity test noticing after the
    fact. `columns` is the only thing that varies between the two callers.

    `statuses` defaults to `PUBLIC_STATUSES`, matching `_text_parameters`'s
    own default - issue #266's maintenance search passes
    `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` instead.
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
) -> Select[Any]:
    """The entry ids `q` and `predicates` between them select.

    So the result page and every facet count are answering the same
    question about the same population - a facet count computed against a
    differently-composed base is exactly the drift
    `test_api_public_search.py`'s count/result parity test exists to catch.
    """
    return _select_from_scored(scored, [CatalogueEntry.id], predicates, statuses=statuses)


def build_search_statement(
    *,
    filters: Sequence[FilterSelection] = (),
    after_score: float | None = None,
    after_key: str | None = None,
    limit: int,
    statuses: Sequence[str] = PUBLIC_STATUSES,
) -> Select[Any]:
    """The composed result statement `search_entries` runs.

    `statuses` defaults to `PUBLIC_STATUSES`; issue #266's maintenance
    search passes `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` so a
    draft, deprecated or withdrawn entry can be found by an administrator
    without loosening the public default.

    Public because `backend/tests/test_db_search_index.py` `EXPLAIN`s the
    statement the module actually runs, and the whole point of that test is
    that it cannot be a hand-copied approximation - it used to import
    `_SEARCH_SQL` for the same reason. `q` is not an argument: the plan is a
    fact about the statement's *shape*, and `q` reaches it as a bound
    parameter (`_text_parameters`) at execution.

    The keyset predicate is *omitted* on the first page rather than written
    as `CAST(:after_score AS real) IS NULL OR ...`. The old raw statement
    needed that spelling because one text literal had to serve both cases,
    and an untyped `$n IS NULL` gives PostgreSQL nothing to infer a
    parameter type from; a composed statement simply does not add the
    clause, which is clearer and one less thing for the planner to prove
    away.

    `REAL`, not `double precision`, on the cursor comparison, and the choice
    is load-bearing: `similarity()` returns `real`, so `MAX(m.score)` is a
    `real`, and the cursor carries that value through a Python float and
    back. Comparing in double precision would make the tie branch exact only
    for as long as float4 -> text -> float8 round-trips invariantly for this
    driver - and it is the tie branch that keeps a page boundary inside a
    score tie from dropping or repeating a row.
    """
    scored = _SCORED_SQL.cte("scored")
    statement = (
        _select_from_scored(
            scored,
            [
                CatalogueEntry.business_key,
                CatalogueEntry.preferred_term,
                CatalogueEntry.status,
                CatalogueEntry.specimen_unconstrained,
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

    Raises `EmptySearchQueryError` before any SQL runs for a blank query,
    `MalformedSearchCursorError` for an `after` value this module did not
    produce, and its `SearchCursorQueryMismatchError` subclass for one it
    produced for a different `q`, a different filter set (issue #139), or a
    different status scope (issue #266 - see `_request_digest`).

    `statuses` defaults to `PUBLIC_STATUSES` - what `/catalogue/search`
    passes. Issue #266's `/catalogue/admin/search` passes
    `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` instead, so the same
    ranking and paging machinery serves both surfaces rather than a forked
    implementation.
    """
    if not q.strip():
        raise EmptySearchQueryError(
            "a search query must contain at least one non-whitespace character"
        )

    after_score: float | None = None
    after_key: str | None = None
    if after is not None:
        after_score, after_key = _parse_cursor(after, q=q, filters=filters, statuses=statuses)

    # Transaction-scoped, and re-asserted in `scored`'s own WHERE - see the
    # module docstring on why both, and on what that restatement does and
    # does not protect against.
    session.execute(_SET_THRESHOLD_SQL, {"threshold": SIMILARITY_THRESHOLD})

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
            specimen_unconstrained=row.specimen_unconstrained,
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

    Separate from `search_entries` rather than folded into it, because the
    two answer different questions over the same population: a page is one
    slice of the keyset, a facet count is over all of it. Sharing the scored
    CTE is what keeps them consistent - see `_matching_entry_ids`.

    The threshold GUC is set here too, and not merely because
    `search_entries` happens to have run first: these are separate
    statements over the same `%` scans, and a facet count computed at a
    different threshold from the page it describes would be quietly,
    unfalsifiably wrong.

    `statuses` must match whatever `search_entries` was called with for the
    same request - see `search_entries`' own docstring.
    """
    if not q.strip():
        raise EmptySearchQueryError(
            "a search query must contain at least one non-whitespace character"
        )
    session.execute(_SET_THRESHOLD_SQL, {"threshold": SIMILARITY_THRESHOLD})
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
