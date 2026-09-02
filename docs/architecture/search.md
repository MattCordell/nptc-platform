# Catalogue search

How `GET /api/v1/catalogue/search` finds and ranks entries (FR-14, FR-15), landed by
issues #142 and #138. This document describes what the search *does* and which fields
and indexes it uses; the decisions behind it - and the alternatives refused - are in
[ADR-0024](../adr/0024-catalogue-search-and-pagination.md) and
[ADR-0029](../adr/0029-hybrid-full-text-and-trigram-search.md).

The endpoint's request and response contract (parameters, the keyset cursor, error
statuses) is in [public-api.md](public-api.md). This file is about the query.

## What is searched

FR-14 requires one query field covering five things. All five are reached from `q`:

| Field | Where it lives | Matched by |
|---|---|---|
| RCPA preferred term | `catalogue_entry.preferred_term` | trigram + full-text |
| Synonyms | `designation.term`, `status = 'active'` | trigram + full-text |
| SNOMED fully specified name | `code_binding.fsn`, `status = 'active'` | trigram + full-text |
| SNOMED AU preferred term | `code_binding.au_preferred_term`, `status = 'active'` | trigram + full-text |
| SNOMED code | `code_binding.code`, `status = 'active'` | exact equality |

**Retired and non-active rows are not a way in.** A retired designation and a retired
binding are history, not a route to the entry, so every `designation` and `code_binding`
index is partial on `status = 'active'` and the query spells that predicate as a literal
so the planner can prove the partial index covers it. Entry status is filtered separately
and parameterised (`PUBLIC_STATUSES`), because the entry-side indexes are deliberately
*not* partial - the maintenance UI (issue #149) searches drafts.

**The two SNOMED labels are searched exactly as served, semantic tag intact** (FR-82).
There is no stripped second copy of either column. FR-98's requirement that both tag
forms reach the entry is met by the one index: the tag is extra text to trigram and its
own lexeme to full-text, so `Full blood count` and `Full blood count (procedure)` both
find the entry. ADR-0029 records why a SQL-side tag stripper was refused, and the cost
this choice accepts.

## How a match is found

Nine scans, each its own `UNION ALL` branch in `nptc.catalogue.search._SEARCH_SQL`, each
supported by its own index:

| Branch | Index | Predicate |
|---|---|---|
| preferred term, trigram | `ix_catalogue_entry_preferred_term_trgm` | `nptc_search_text(col) % nptc_search_text(:q)` |
| preferred term, full-text | `ix_catalogue_entry_preferred_term_fts` | `nptc_search_document(col) @@ nptc_search_query(:q)` |
| synonym, trigram | `ix_designation_term_trgm` | as above |
| synonym, full-text | `ix_designation_term_fts` | as above |
| FSN, trigram | `ix_code_binding_fsn_trgm` | as above |
| FSN, full-text | `ix_code_binding_fsn_fts` | as above |
| AU preferred term, trigram | `ix_code_binding_au_preferred_term_trgm` | as above |
| AU preferred term, full-text | `ix_code_binding_au_preferred_term_fts` | as above |
| code, equality | `ix_code_binding_code` | `code = btrim(:q)` |

An entry matched several ways is collapsed to one row by `GROUP BY` and keeps its **best**
score, so a well-bound entry is one result rather than six.

**Separate branches, not one predicate with `OR`.** `a % q OR a @@ q` over two indexes on
one column plans as a sequential scan with both tests applied as filters. The answers are
byte-identical, so no functional test can see it; only the plan can.
`backend/tests/test_db_search_index.py` `EXPLAIN`s the real statement and asserts an
`Index Cond` on each of the nine.

### Why both mechanisms

They fail in opposite directions and neither is a superset of the other.

- **Trigram** (`pg_trgm`) survives typographical error. `haemglobin` still scores well
  against `haemoglobin`. It also handles word-order variation essentially for free,
  because a trigram set is unordered. It is weak on inflected forms, and it penalises a
  short query against a long string by the length ratio alone - which matters for FSNs,
  the longest text in the catalogue.
- **Full-text** (`tsvector`/`tsquery`, `english`) matches a stemmed or pluralised form
  and cares only whether a query word appears, not how much of the document it accounts
  for. It scores a transposition at exactly zero: `haemglobin` and `haemoglobin` share no
  lexeme, so no ranking function recovers a match the query never made.

The query takes the better of the two per entry rather than layering one behind the other.

### Normalisation

Both halves normalise through the same function, so the two agree on case and diacritics:

- `nptc_search_text(text) -> text` — `lower(unaccent(...))`. Backs the trigram indexes
  and every trigram predicate.
- `nptc_search_document(text) -> tsvector` — `to_tsvector('english', nptc_search_text(...))`.
  Backs the full-text indexes.
- `nptc_search_query(text) -> tsquery` — `websearch_to_tsquery('english', nptc_search_text(...))`.
  The query-side half; never indexed, but paired with the document function so both use
  the same configuration.

All three are `IMMUTABLE STRICT PARALLEL SAFE` and fully schema-qualified, which is
required rather than tidy: an index expression is evaluated under a secure `search_path`
of `pg_catalog, pg_temp`. `STRICT` matters for correctness too - a `NULL`
`au_preferred_term` must index as `NULL`, not as an empty value shared with every other
unbound row.

A query that lexes to nothing (`the`, say - `english` drops stopwords) yields an empty
`tsquery`, which matches nothing rather than everything. The full-text branches simply
contribute no rows and the trigram branches still answer.

### The similarity threshold

`pg_trgm.similarity_threshold` is `0.3` - the extension's own default, kept rather than
invented. It is set per **transaction** (`set_config(..., is_local => true)`), never per
session: connections come from a pool and outlive a request, so a session-scoped value
would follow the connection to the next caller.

It is restated inside the query, on the raw `similarity()` value, where it filters out
weak matches that a lower threshold left by another code path would have admitted. It
cannot defend against a threshold left *higher* - that narrows the index scans
themselves. Full-text and code branches carry `NULL` there; a threshold has no meaning
for an `@@` or an `=` test.

**The threshold moves up, never down.** The principal failure mode of a text search is
matching everything: a caller cannot distinguish a page of noise from a working search
over a catalogue that has nothing to offer, so they trust the noise.

## How results are ranked

Scores fall into disjoint bands:

| Band | Score |
|---|---|
| Exact match on the SNOMED code | `1.00` |
| Exact match on the entry's preferred term | `0.99` |
| Exact match on a synonym, the FSN or the AU preferred term | `0.95` |
| Fuzzy match on the preferred term | raw score × `0.90` |
| Fuzzy match on a synonym | raw score × `0.80` |
| Fuzzy match on the FSN or AU preferred term | raw score × `0.75` |

where the raw score is `similarity()` for a trigram branch and `ts_rank_cd(..., 32)` for a
full-text one, both in `0.0 … 1.0`.

Because every fuzzy contribution is multiplied by a weight below 1.0, **no fuzzy match
from any source can reach an exact band**. FR-14's requirement that an exact code or
preferred-term match outranks a fuzzy synonym hit therefore holds for every possible
input, not merely for the cases a test happens to cover -
`test_search_ranking.py::test_the_score_bands_cannot_overlap` asserts the inequality
between the constants directly.

The weights express relative source trust: the catalogue's own curated preferred term
above a synonym, and a synonym above a label a terminology server served for the bound
concept. The band *ordering* is a requirement; the particular weights are not, and are
expected to move once there is a production query log to tune against.

Results are ordered `score DESC, business_key ASC`. The tie-break is load-bearing, not
decoration: scores tie constantly over a catalogue of similar short terms, so score alone
is not a total order and a page boundary inside a tie would drop or repeat rows.

## What enforces this

| Claim | Test |
|---|---|
| The PRD's worked example (`49466006`, `ACTH`, `Adrenocorticotropic hormone`, `Corticotropin`) reaches one entry, top | `test_search_ranking.py` |
| Both FSN tag forms reach the entry; a bare tag stays a weak match | `test_search_ranking.py` |
| A typo and a reversed word order still reach the entry | `test_search_ranking.py`, `test_api_public_search.py` |
| An exact hit outranks a fuzzy one, for every possible input | `test_search_ranking.py` |
| A retired binding, a retired synonym and a non-active entry are unreachable | `test_search_ranking.py`, `test_api_public_search.py` |
| A near-miss code finds nothing | `test_search_ranking.py` |
| A nonsense query returns an empty page | `test_search_ranking.py`, `test_api_public_search.py` |
| All nine branches plan as index scans | `test_db_search_index.py` |
| Every index exists over the expression the query actually uses | `test_db_search_index.py` |
| The document and query functions agree, and stem as expected | `test_db_search_index.py` |
| The threshold reverts when the transaction ends | `test_db_search_index.py` |
| No search SQL is built by string concatenation (NFR-22) | `test_sql_parameterisation.py` |

## Not here

- **Exact code lookup as its own route** (FR-17) is issue #140. Typing a code into `q`
  works, as FR-14 requires; a dedicated addressable URL for a code is separate.
- **Faceted filtering over `filterable` properties** (FR-16) is a separate child of epic
  #57, not this work.
- **Draft and other non-active entries** are never served here. The maintenance UI's own
  search over drafts is issue #149; the entry-side indexes are already non-partial so
  that it can use them.
- **The NFR-32 performance measurement** (500 ms at the 95th percentile over 20,000
  entries) is epic #57's, in phase P5.
