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
and parameterised (`:statuses`), because the entry-side indexes are deliberately *not*
partial - `PUBLIC_STATUSES` on the public route, and issue #266's `MAINTENANCE_STATUSES`
on the maintenance one (see [Maintenance search](#maintenance-search-issue-266) below).

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
| code, equality | `ix_code_binding_code` | `code = :q_exact` |

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

The query takes the better of the two per entry rather than layering one behind the other -
but only after the full-text score is rescaled onto the trigram range. The two do not
produce comparable numbers on their own: `similarity()` is a ratio that uses all of
`0.0 … 1.0`, while `ts_rank_cd(..., 32)` measures a *complete* match in this catalogue's
text at `0.0909`. Taking `MAX` of the two unscaled would mean "the trigram score if there
was one, otherwise a near-zero floor", and an entry found only by an inflected form would
sort below every barely-admissible typo in the catalogue. Each full-text contribution is
therefore mapped onto `[0.3, 1.0]` before its weight is applied, anchored on the
similarity threshold rather than on a tuned constant, so the weakest admissible match of
either kind enters at the same rank.

What that does **not** buy is a well-spread full-text ranking: `ts_rank_cd`'s output is
dense near zero, so in practice a full-text contribution sits near its floor and orders
only within the full-text branch. Calibrating it properly needs a production query log.

**Negation is guarded.** `websearch_to_tsquery` reads `-` as NOT, and a tsquery that can be
satisfied by *absence alone* matches every row with nothing for GIN to probe. Each
full-text branch therefore carries `NOT ('' :: tsvector @@ nptc_search_query(:q))`, a
direct test of that condition, which the planner resolves once and uses to prune the
branch. Trigram is unaffected and still runs, so such a query is searched for the literal
text typed rather than refused.

The condition is wider than "every word was excluded", deliberately:

| Query | tsquery | Full-text branches |
|---|---|---|
| `-glucose` | `!'glucos'` | pruned |
| `a -b` (positive half is a stopword) | `!'b'` | pruned |
| `zymogen or -kinase` | `'zymogen' \| !'kinas'` | pruned - the negated disjunct alone would return the catalogue |
| `vitamin -d` | `'vitamin' & !'d'` | kept - the conjunction still requires a lexeme |

The third row costs `zymogen` its full-text route even though it was probeable. That is a
degradation rather than a fault: trigram is unguarded, so the word is still matched by
similarity.

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

`nptc_search_text` folds case and diacritics but does **not** trim. All five exact-match
comparisons therefore read `:q_exact`, which is `q` stripped in Python: a preferred term
pasted with surrounding whitespace has a `similarity()` of `1.0` but is not `=` to the
stored value, so without it a pasted term is scored as fuzzy and can be outranked by an
exact synonym hit on a different entry. Python's `str.strip()` rather than SQL's
`btrim(text)`, which trims spaces only - a cell copied out of a spreadsheet ends in a
carriage return and a newline. Trimming the query rather than changing `nptc_search_text`
avoids rebuilding four trigram indexes for a difference `similarity()` does not notice,
and only the query side is trimmed: whitespace on a *stored* label is a data defect to fix
where it is written.

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

**The threshold governs trigram only, and full-text has no equivalent.** `@@` admits a row
on a single shared lexeme after stemming, so a common domain word - `test`, `level`,
`measurement` - matches a large fraction of the catalogue at a score near the floor. Page
one is still the best matches, so this is a recall and plan-cost consequence rather than a
wrong answer, and it is accepted deliberately: a minimum-rank floor would be an invented
constant, and this platform has no production query log to justify one. ADR-0029 records
it as an open consequence.

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

where the raw score is `similarity()` for a trigram branch, and for a full-text one
`ts_rank_cd(..., 32)` rescaled onto `[0.3, 1.0]` as described above. Both are in
`0.0 … 1.0`, which is what bounds every fuzzy band below.

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

## How facets are derived (FR-16)

The facet list is not written down anywhere. It is a `SELECT` against
`property_definition` on every request: every active property an administrator has marked
`filterable`, plus the one declared core-column facet (entry status, which lives on
`catalogue_entry` and has no property definition to be discovered from). Flipping
`filterable` on a property changes the answer on the very next request — no deployment, no
restart, no cache to invalidate (FR-09). That is the point of the requirement; a
hard-coded facet list would defeat the reason the property registry exists.

Nothing in `nptc.catalogue.facets` names a datatype. Each facet's grouping expression, the
operators it accepts and the predicate it renders all come from the property's
`DatatypeHandler` — `facet_expression()`, `supported_filter_ops()` and `filter_clause()`,
the three members [ADR-0013](../adr/0013-datatype-handler-registry.md) added for this
caller (FR-77). Entry status reaches the same code through a descriptor of the same shape,
so no line of the query builder has a special case for it.

**The query surface.** `?filter.<key>=<value>`, repeated once per value. Within one facet
values are OR-ed; across facets they are AND-ed. An operator other than the default
`equals` is named after the key — `?filter.assay_name:prefix=glu`,
`?filter.volume_ml:range=1..5` — and which operators a facet accepts follows from the
property's own handler, not from a list in the router.
[ADR-0032](../adr/0032-faceted-filter-query-surface.md) records why the dotted prefix, why
the repeated form, and what was rejected.

**Both collection endpoints accept filters; only `/catalogue/search` returns facets.**
Computing counts on every page of a browse is a cost nobody has asked for, and adding them
later is additive while removing them would not be (ADR-0032).

**Counts.** A bucket's count is the number of entries in the whole result set carrying
that value — not the number on this page. Two properties of it are worth stating because
neither is obvious and both are asserted as tests:

- *A multi-valued property counts an entry once per value.* An entry with seven specimens
  contributes one to each of seven buckets, never seven to one. Filter predicates are
  `EXISTS` subqueries over `property_value` and counts are `COUNT(DISTINCT entry_id)`,
  which is what makes that true; a join gets it wrong and passes every other test here.
- *A facet's own selection is excluded from its own counts.* Choosing "Chemistry" does not
  collapse the discipline facet to one bucket — a user has to be able to see what
  switching to "Haematology" would give them. Every *other* facet does narrow, which is
  what makes the counts worth reading.

**Bucket cap.** At most 20 buckets per facet, ordered by count descending, and the facet
says `truncated: true` when the cap bit. There is no way to page through the remainder;
narrow the search instead. The number is invented, in the same category as the similarity
threshold above, and is named once in code (`FACET_BUCKET_CAP`) and argued in ADR-0032.

**Labels.** A coded facet groups on the code alone and is labelled from the `display`
stored beside it when the value was recorded. No terminology call happens on the search
path (FR-54) — a `$lookup` per bucket would be slow on the happy path and would break
whenever the terminology server was unreachable.

**A filterable property that cannot be grouped.** A `decimal` property's handler returns
`None` from `facet_expression()`: every value of a continuous quantity is its own bucket,
so grouping says nothing. Such a property is still a usable *filter*, and it appears in
the facet list with `facetable: false` and no buckets rather than vanishing —
[ADR-0013](../adr/0013-datatype-handler-registry.md) §8's "stated cost". A facet that
disappeared silently is indistinguishable, to a client, from one whose values happen to
match nothing.

**Refusals.** An unknown filter key, a key naming a property that is not `filterable`, an
operator the property's handler does not support, and a value the property cannot hold are
all 422s. None of them is ignored: a dropped filter serves the caller a page that looks
like an answer to the question they asked and is an answer to a different one, with
nothing in the response to tell them apart.

**Cursors.** The search cursor is bound to the filter set as well as to `q`. Narrowing the
filters changes which entries exist to be scored, so a replayed cursor names a window that
is the next page of neither request, and it is refused. `/catalogue/entries`' cursor is a
`business_key` and is *not* filter-bound: the ordering is on that column alone and stays
total whatever the filters are.

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
| A query that excludes every word does not return the catalogue - as an answer and as a plan | `test_search_ranking.py`, `test_db_search_index.py` |
| A padded code and a padded preferred term both still reach the exact band | `test_search_ranking.py` |
| An inflected form reaches its entry, and is not ranked beneath a typo | `test_search_ranking.py` |
| A cursor carrying a non-finite score is refused | `test_api_public_search.py` |
| All nine branches plan as index scans | `test_db_search_index.py` |
| Every index exists over the expression the query actually uses | `test_db_search_index.py` |
| The document and query functions agree, and stem as expected | `test_db_search_index.py` |
| The threshold reverts when the transaction ends | `test_db_search_index.py` |
| No search SQL is built by string concatenation (NFR-22) | `test_sql_parameterisation.py` |
| A facet count equals the rows that bucket returns, per bucket | `test_api_public_search.py` |
| An entry with seven specimens counts once under each of them | `test_api_public_search.py` |
| Flipping `filterable` adds a facet with no restart | `test_api_public_search.py` |
| An unknown, non-filterable, badly-operated or badly-valued filter is refused | `test_api_public_search.py`, `test_catalogue_facets.py` |
| A cursor replayed under a different filter set is refused | `test_api_public_search.py` |
| The filter predicate reaches #54's generated index; the count reads one property, not the table | `test_db_property_index_plan.py` |
| A hidden entry is found by `GET /catalogue/admin/search` and still absent from `GET /catalogue/search` | `test_api_catalogue_admin_listing.py` |
| The admin status facet has more than one bucket, and `?filter.status=draft` narrows rather than 422s | `test_api_catalogue_admin_listing.py` |

## Maintenance search (issue #266)

`GET /catalogue/admin/search` runs the identical query, ranking and paging this document
describes - `nptc.catalogue.search.search_entries`/`search_facets` take a `statuses`
argument, and the admin route is the one caller that passes
`nptc.catalogue.maintenance.MAINTENANCE_STATUSES` (every `CatalogueEntryStatus`) instead
of the default `PUBLIC_STATUSES`. Nothing else about the query changes: the
`designation`/`code_binding` branches still carry their literal `status = 'active'`
predicates regardless of caller, because those are about a synonym or a binding being
published, not about the entry's own status - a draft entry is still found through its
own preferred term even though only its *active* synonyms and bindings are searchable,
exactly as an active entry's retired synonyms are not.

This is why the substrate this section describes was built non-partial in the first
place: `ix_catalogue_entry_preferred_term_trgm` and `ix_catalogue_entry_preferred_term_fts`
carry no `WHERE status = 'active'`, unlike every `designation`/`code_binding` index, so
widening `:statuses` at the call site is all this issue needed - no index changed and no
new branch was added to `_SCORED_SQL`.

The status facet is the other half of this: [How facets are derived](#how-facets-are-derived-fr-16)'s
core-column descriptor takes its `allowed_values` from the caller's own `status_values`,
so the public route's facet has exactly one bucket (`active`) while the admin route's has
one per status actually present in the matched result - `?filter.status=draft` is a
real, narrowing filter there rather than the public route's refusal (a status this surface
can never show is a 422, not a silently empty page - see this section's own **Refusals**
paragraph above).

Gated on `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44), same as `GET
/catalogue/admin/entries` and the #228/#219/#224 write routes - see
[catalogue-write-api.md](catalogue-write-api.md#all-status-listing-and-search-issue-266)
for the request/response contract and error table.

## Not here

- **Exact code lookup as its own route** (FR-17, issue #140) lives outside this document -
  see [public-api.md](public-api.md#exact-code-lookup-fr-17). Typing a code into `q` still
  works, as FR-14 requires; the dedicated addressable URL is a different, non-scored
  lookup.
- **Draft and other non-active entries** are never served by the *public* route. Issue
  #266's maintenance search above is where they are found, using the same query this
  document describes.
- **The NFR-32 performance measurement** (500 ms at the 95th percentile over 20,000
  entries) is epic #57's, in phase P5.
