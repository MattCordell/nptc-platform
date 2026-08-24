# ADR-0024: Catalogue search with `pg_trgm`, and keyset pagination throughout

**Status:** Accepted
**Date:** 2026-08-24

## Context

Issue #142 builds FR-20's public read API - a documented, versioned, unauthenticated
JSON API over the approved catalogue. Two design questions had to be settled before any
route could be written, and neither is reversible cheaply once vendors are integrating
against the result.

**How search matches.** FR-14 (MUST) requires searching "across preferred terms,
synonyms, the FSN, the AU preferred term and the SNOMED code in one query field", and
FR-15 (MUST) requires that search "tolerate typographical error and word-order
variation". Those are two different demands: the first is about *coverage*, the second
about *fuzziness*. The second is the hard one - a pathologist typing `haemglobin` must
still find `Haemoglobin electrophoresis`, and a vendor's user typing `muller` must find
`Müller cell antibody`.

**How results are paged.** The catalogue is a few thousand entries today and there is no
paging convention anywhere in the platform yet, so #142's choice becomes the house
convention for every later collection endpoint (#143's authenticated surfaces, #141's
release lists). The two candidate shapes have materially different correctness, not
merely different ergonomics.

ADR-0001 already fixed the datastore: PostgreSQL 16+ with `pg_trgm` and `unaccent`, one
datastore, explicitly no Elasticsearch and no vector store. So this ADR is not choosing
a search *technology* - it is choosing which PostgreSQL mechanism, and accepting the
consequences of the one that was already ruled in.

## Decision

### Search is trigram similarity over a normalised expression

`pg_trgm`'s `%` operator over `nptc_search_text(<column>)`, where
`nptc_search_text(text)` (in `nptc.db.functions`, created by migration `0012`) is
`lower(public.unaccent('public.unaccent'::regdictionary, value))` - `IMMUTABLE STRICT
PARALLEL SAFE`. Two GIN trigram indexes are built over it: one on
`catalogue_entry.preferred_term`, one on `designation.term`, the latter partial on
`status = 'active'`.

`nptc.catalogue.search._SEARCH_SQL` unions two `%` scans - the entry's own preferred
term and its active designations - and aggregates per entry with
`MAX(similarity(...))`, so an entry matched through several of its synonyms is one
result scored by its best match, not five results.

The similarity threshold is `pg_trgm`'s own default, `0.3`, kept rather than invented.
It is stated **twice**: the GUC is set for the `%` operator, and the statement's own
`HAVING` re-asserts `MAX(score) >= :threshold` directly.

The GUC is set with `set_config('pg_trgm.similarity_threshold', ..., true)` - the
`is_local => true` matters. `set_limit()` sets it at *session* scope, and since
connections come from a pool and outlive a request, that would leave the threshold in
force for the next caller handed that connection. `set_config` with `is_local` reverts on
commit. It is used rather than the more direct `SET LOCAL` only because `SET` accepts no
bound parameter and NFR-22 forbids interpolating the value into the statement text.

The `HAVING` is then a genuine but *partial* second line of defence, and the distinction
is worth recording because the loose claim ("correct independently of session state") is
false. `HAVING` can only discard rows the inner `%` scans already returned. So it
protects against a threshold left **lower** than this module's - the extra weak matches
are filtered back out - and cannot protect against one left **higher**, which narrows the
index scans themselves so that no `HAVING` can recover a row that was never scanned. The
transaction scoping is what keeps the second case from arising; the `HAVING` is what stops
a future code path setting the GUC lower from silently broadening this query.

**`nptc_search_text` is not the stored business logic PRD §14.1 bans**, and it is not
even the narrow exception ADR-0023 argued for. It encodes no catalogue rule: it
lowercases and strips diacritics. It has no side effects, decides nothing, and nothing
reads it but the two index expressions and the matching predicate. ADR-0023's three
tests apply unchanged - a pure function, defined in a versioned `backend/src` module
and a migration rather than typed into a psql session, and covered by
`backend/tests/test_db_search_index.py`. It exists as a function only because an
expression index's expression must be `IMMUTABLE`.

**The dictionary is pinned, and both objects are schema-qualified.** The one-argument
`unaccent(text)` is only `STABLE` - it resolves its dictionary through `search_path` -
so it cannot appear in an index expression at all; the two-argument form with a
constant `regdictionary` can. Separately, PostgreSQL evaluates an index expression with
a secure `search_path` of `pg_catalog, pg_temp`, so an unqualified `unaccent(...)` or
`'unaccent'::regdictionary` inside the inlined expression resolves against nothing and
`CREATE INDEX` fails outright. Both are therefore written `public.`-qualified.
Qualification is used rather than a `SET search_path` clause on the function, because a
function carrying a `SET` clause is not inlinable and so is useless as an indexed
expression.

### Pagination is keyset, with no offsets and no opaque cursors

`GET /catalogue/entries` orders by `business_key` and takes `?limit=&after=<business_key>`.
`GET /catalogue/search` orders by `(score DESC, business_key ASC)` and takes
`?after=<score>:<query digest>:<business_key>`. Both return `{items, next_cursor}`, with
`next_cursor` `null` exactly on the last page - decided by fetching `limit + 1` rows and
discarding the extra, never by a `COUNT(*)`.

The cursor is derived from values the client has just been served. It carries no
internal id, no row offset and no encoded server state, so it needs no signing, no
expiry and no server-side storage, and a client can inspect it and see nothing it did
not already know.

Both cursors' key half is validated against `BUSINESS_KEY_PATTERN` before it reaches a
query - the search cursor in `_parse_cursor`, the entries cursor by the query parameter's
own `pattern`. A malformed cursor is a 422, not a page starting from "whatever this sorts
after", because the latter leaves a client corrupting its own cursor with no way to
notice.

**The search cursor is bound to `q`.** Its middle part is a 64-bit BLAKE2s digest of the
query that minted it, and a mismatch is a 422. This is not a signature and is not keyed:
a cursor grants nothing a caller could not ask for with `?q=` directly, so there is
nothing to authenticate and no secret to manage. It detects a client bug. Without it,
replaying a cursor under a different `q` compares the new query's scores against the old
query's boundary and serves a window that is the next page of neither - silently, and in a
shape (a short or empty page) a client cannot tell from a real result. Documenting the
hazard in `public-api.md` instead was considered and rejected: the codebase refuses
malformed cursors, blank queries and out-of-range limits rather than guessing, and a
silently wrong result set is a worse outcome than any of those.

`business_key` is the tie-break on the search ordering, and that is load-bearing rather
than tidy: trigram scores are floats over a catalogue with many similar terms and tie
constantly, so `ORDER BY score DESC` alone is not a total order. A page boundary landing
inside a tie then either drops rows (`score < :after_score`) or repeats them
(`score <= :after_score`). `backend/tests/test_api_public_search.py` pages one row at a
time through a deliberate tie so that every boundary is a tie boundary.

## Rejected alternatives

### `tsvector`/`tsquery` full-text search

PostgreSQL's own full-text search, with a `tsvector` column or expression index and
`websearch_to_tsquery`.

Rejected: it cannot satisfy FR-15 at all. A `tsvector` match is equality between
lexemes after stemming, so `haemglobin` and `haemoglobin` stem to different lexemes and
score exactly zero - no ranking function recovers a match that the query never made.
Full-text search is genuinely better than trigram at word-order variation (FR-15's other
half) and at long-document relevance, but the catalogue's searchable text is a short
term, not a document, and typo tolerance is the half that a user notices on every
mistyped query. Trigram matching handles word-order variation acceptably for short
strings because a trigram set is unordered to begin with.

A hybrid - `tsvector` for the primary match, trigram as a fallback when it returns
nothing - was also rejected, for now: it doubles the index footprint and the query
surface to improve the ranking of queries that already succeed, and there is no
production query log yet to say whether that ranking is a problem. This stays available
as a later, evidence-driven change.

### Elasticsearch, OpenSearch, or a vector store

Rejected by ADR-0001 before this issue existed - one datastore, deliberately. Recorded
here only because "add a search engine" is the reflex answer to a search requirement,
and the reason it is refused (a second datastore to deploy, back up, secure and keep
consistent, for a few thousand short strings) has not changed.

### `similarity(a, b) >= 0.3` instead of the `%` operator

Functionally identical results, and it reads more explicitly.

Rejected: a GIN trigram index can serve the `%` operator and cannot serve a comparison
against a `similarity()` result, so this spelling plans a sequential scan over the whole
catalogue while returning byte-identical answers. That is the worst class of defect
available here - invisible to every functional test, visible only as a slow catalogue
months later. `backend/tests/test_db_search_index.py` `EXPLAIN`s the real statement and
asserts an `Index Cond` on each trigram index specifically to catch a future edit that
reintroduces it.

### `LIMIT`/`OFFSET` pagination

The obvious shape, and the one every client library already understands.

Rejected on correctness, not performance. `OFFSET n` re-reads and discards `n` rows on
every page, so cost grows with page number - but worse, the window is defined by
position in a result set that is being concurrently written. An insert or a status
change landing before the current offset shifts every later row backwards, so a client
walking pages silently *skips* an entry; a deletion or withdrawal shifts them forwards
and the client sees one twice. For an API whose entire purpose is letting a vendor keep
a local copy of the catalogue in step, silently dropping an entry is the defect that
matters most, and it is undetectable from the client's side. A keyset cursor over a
`UNIQUE` column has no such window.

### An opaque, signed or encrypted cursor

Base64 or HMAC the cursor so clients cannot construct or depend on its contents.

Rejected: it buys nothing here and costs a key to manage plus a rotation story. The
cursor's contents are a `business_key` and a similarity score - both values the client
was just served in the same response, neither an internal identifier, neither
privileged. Opacity would be worth paying for if the cursor encoded server state or a
row offset; it encodes neither. `nptc.catalogue.search.MalformedSearchCursorError`
still refuses a cursor the API did not mint, so a hand-built cursor gets a 422 rather
than an unpredictable page.

### Serving `deprecated` entries with a status flag

Return every non-draft entry and let clients filter on `status`.

Rejected: the FR-20 surface is what a vendor builds a request form from, and a
deprecated entry is precisely one they must stop offering. "Present in the API with a
field they may or may not read" is a weaker guarantee than "absent", and the difference
shows up as a test that is still orderable in somebody's LIS. `nptc.catalogue.queries.
PUBLIC_STATUSES` is `("active",)`, and deprecation history is the release/history
surface's job (#141), where it is the subject rather than a footnote.

## Consequences

- **`nptc_search_text` is the second database function in the repository**, and the
  first one that is not a validity predicate. The bar ADR-0023 set still applies: a
  pure, side-effect-free function in `nptc.db.functions`, created by a migration, with
  its own test. A future function that decides *behaviour* rather than normalising a
  value is a different question and needs its own ADR.
- **If the `unaccent` dictionary's rule file is ever changed** - a PostgreSQL upgrade
  that revises `unaccent.rules`, or a deployment substituting its own - both trigram
  indexes must be `REINDEX`ed, because `IMMUTABLE` is honest only for a fixed
  dictionary definition. Noted in `docs/operations/upgrade.md`.
- **Keyset pagination is now the house convention.** Later collection endpoints should
  page this way rather than each choosing; anything that genuinely needs random access
  to page *n* (a UI page-number control, say) needs its own decision, because keyset
  cannot serve it and reintroducing `OFFSET` for one endpoint reintroduces its skip/
  repeat behaviour for that endpoint.
- **FR-14 is only partly served.** Search covers preferred terms and active
  designations. The FSN, the AU preferred term and the SNOMED code are not searched
  here: exact-code lookup is FR-17's own endpoint (#140), and extending the union to
  `code_binding.fsn`/`au_preferred_term` is a third and fourth `%` scan plus two more
  indexes - deliberately deferred until #140 settles whether code lookup subsumes the
  need. FR-16's faceted filtering over `filterable` properties is untouched by this
  issue and remains #138's.
- **The threshold moves up, never down.** The principal failure mode of a text search
  is matching everything: a caller cannot distinguish a page of noise from a working
  search over a catalogue that has nothing to offer, so they trust the noise. Lowering
  `SIMILARITY_THRESHOLD` to make some query match is therefore the change to be most
  suspicious of, and `test_a_query_below_the_threshold_matches_nothing` exists to make
  it fail loudly.
