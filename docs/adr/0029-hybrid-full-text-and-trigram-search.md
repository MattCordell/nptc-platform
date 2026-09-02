# ADR-0029: Hybrid full-text and trigram search over all five FR-14 fields

**Status:** Accepted
**Date:** 2026-09-02

Supersedes the "`tsvector`/`tsquery` full-text search" rejected alternative in
[ADR-0024](0024-catalogue-search-and-pagination.md). Everything else in ADR-0024 - the
keyset cursor, the threshold discipline, the refusal of a second datastore - stands
unchanged.

## Context

ADR-0024 landed search for issue #142 and left FR-14 deliberately half-served. Two of
the requirement's five named fields were searched (the catalogue's own preferred term
and its active designations); the stored `fsn`, the stored `au_preferred_term` and the
SNOMED code were not. It also rejected a full-text/trigram hybrid, in these words:

> A hybrid - `tsvector` for the primary match, trigram as a fallback when it returns
> nothing - was also rejected, for now: it doubles the index footprint and the query
> surface to improve the ranking of queries that already succeed, and there is no
> production query log yet to say whether that ranking is a problem. This stays
> available as a later, evidence-driven change.

Issue #138 is the change that deferral anticipated, and three things have moved since.

**The deferral's stated condition is met, in the only way it can be.** ADR-0024 tied the
FSN and AU preferred term to "#140 settling whether code lookup subsumes the need".
It does not. FR-17's code lookup (#140) is a *route* - a caller who already has a code
and wants the entry. FR-14 is about the single search box, and its own worked example
requires `49466006` to reach the same entry as `ACTH` typed into that same box. A
dedicated route cannot serve a user who does not know which of the four things they are
holding.

**The hybrid's cost/benefit is different once the FSN is in scope.** ADR-0024 assessed
the hybrid against a corpus of short curated terms, where it improves "the ranking of
queries that already succeed". That is a fair description of `preferred_term` and
`designation.term`. It is not a fair description of `code_binding.fsn`, which is the
longest text in the catalogue and is written in a register nobody types:
`Adrenocorticotropic hormone measurement (procedure)`. Trigram similarity is a Jaccard
ratio over the whole string, so it penalises a short query against a long FSN by the
length difference alone, independently of how well the query matches. Adding the FSN
under trigram alone would add a field that is technically searched and practically hard
to reach.

**The two mechanisms fail in opposite directions, and neither is a superset.** This is
the substance of the reversal, and ADR-0024 states half of it correctly. A `tsvector`
match is lexeme equality after stemming, so it scores a transposition at exactly zero -
`haemglobin` and `haemoglobin` share no lexeme, and no ranking function recovers a match
the query never made. That is why trigram stays. What ADR-0024 did not weigh is the
converse: trigram scores an inflected or pluralised form as a near-miss rather than a
match, and it has no notion of "this query word appears in this document" at all.

## Decision

### Both mechanisms, per column, unioned rather than layered

`nptc.catalogue.search._SEARCH_SQL` is nine index-supported scans:

| Source | Trigram | Full-text |
|---|---|---|
| `catalogue_entry.preferred_term` | `%` | `@@` |
| `designation.term` (active) | `%` | `@@` |
| `code_binding.fsn` (active) | `%` | `@@` |
| `code_binding.au_preferred_term` (active) | `%` | `@@` |
| `code_binding.code` (active) | equality | — |

Each is a separate `UNION ALL` branch, and that is not stylistic. `a % q OR a @@ q` over
two different indexes on one column plans as a sequential scan with both tests applied
as filters - byte-identical answers, and exactly the defect class ADR-0024 recorded for
`similarity(...) >= 0.3`. An entry matched several ways is collapsed by `GROUP BY` and
keeps its **best** score, so the mechanisms compose rather than average.

Not "full-text first, trigram as a fallback", which is the shape ADR-0024 rejected. A
fallback only runs when the primary returns nothing, so a query that full-text answers
badly is never improved by trigram; taking the maximum of both costs the same scans and
has no such blind spot.

### The text search configuration is `english`, pinned in a function pair

`nptc_search_document(text) -> tsvector` and `nptc_search_query(text) -> tsquery`, in
`nptc.db.functions`, created by migration `0015`. Both wrap ADR-0024's own
`nptc_search_text` normalisation, so the two halves fold case and diacritics identically.

A pair, not two inline expressions, because a document lexed as `english` and a query
lexed as `simple` share no stems and match nothing - a failure invisible in every index
definition, showing up only as a search that silently returns less than it should. The
configuration is named once.

`english` rather than `simple`: `simple` lexes to lowercased words with no stemming and
no stopword list, which would make the full-text half a strictly worse trigram, finding
nothing trigram does not already find and buying nothing for the index footprint.
Stemming is the whole reason the second index family earns its place.

The two-argument `to_tsvector`/`websearch_to_tsquery` with a constant `regconfig`, for
precisely ADR-0024's reason for the two-argument `unaccent`: the one-argument forms
resolve the configuration through `default_text_search_config`, a GUC, and are therefore
only `STABLE`, which PostgreSQL refuses in an index expression. Every object is
`public.`- or `pg_catalog.`-qualified, because an inlined index expression is evaluated
under a secure `search_path` of `pg_catalog, pg_temp`.

`websearch_to_tsquery` rather than `to_tsquery`: `q` is free text from a URL, and
`to_tsquery` raises a syntax error on input it cannot parse, so every stray `&` or
unbalanced quote would be a 500. `websearch_to_tsquery` never raises.

### Ranking is disjoint score bands, not a tuned formula

| Band | Score |
|---|---|
| Exact match on the SNOMED code | `1.00` |
| Exact match on the entry's preferred term | `0.99` |
| Exact match on a synonym, the FSN or the AU preferred term | `0.95` |
| Fuzzy, `catalogue_entry.preferred_term` | `similarity` or `ts_rank_cd` × `0.90` |
| Fuzzy, `designation.term` | × `0.80` |
| Fuzzy, `code_binding.fsn` / `au_preferred_term` | × `0.75` |

Every fuzzy contribution is a `similarity()` or a `ts_rank_cd(..., 32)` result, both at
most 1.0, multiplied by its source's weight. No fuzzy match from any source can therefore
exceed the largest weight, and both exact bands sit strictly above it. FR-14's
"an exact code match and an exact preferred-term match outrank a fuzzy synonym hit" is
consequently true **for every possible input**, not for the corpus a fixture happens to
hold, and `test_search_ranking.py::test_the_score_bands_cannot_overlap` asserts the
inequality between the constants directly rather than inferring it from an example.

The weights express relative source trust and nothing finer is claimed for the specific
figures - ADR-0024's "no production query log yet" is still true, and these are the kind
of number that should move once there is one. What must not move is the *ordering* of
the bands, which is a requirement rather than a tuning parameter.

**The full-text score is rescaled onto the trigram range before its weight is applied.**
Review of PR #237 established that the two raw scores are not comparable: `similarity()`
uses all of `0.0 … 1.0`, while `ts_rank_cd(..., 32)` measures a *complete* three-lexeme
match in this catalogue's text at `0.0909`, so a weighted full-text contribution topped
out around `0.07`. Taking `MAX` of the two unscaled did not mean "the better of the two"
at all - it meant "the trigram score if there was one, otherwise a near-zero floor", and
an entry found only by an inflected form sorted below every barely-admissible typo in the
catalogue. Each full-text contribution is now mapped onto `[SIMILARITY_THRESHOLD, 1]`
first. The anchor is deliberately not a new tuned constant: the similarity threshold is
the point at which a trigram match is admitted at all, so the weakest admissible match of
either mechanism now enters at exactly the same rank and neither is systematically
preferred. The band ceiling is untouched, because the rescaled value is still at most 1.0.

This is a floor, not a calibration. `ts_rank_cd`'s output is dense near zero, so a
full-text contribution sits near its floor in practice and orders only *within* the
full-text branch. Spreading it properly is the same "needs a query log" problem as the
weights above.

### The threshold restatement moves from `HAVING` to the raw similarity

ADR-0024 stated the similarity threshold twice: once as the GUC the `%` operator reads,
once as `HAVING MAX(score) >= :threshold`. The second statement cannot survive weighting.
A genuine trigram match at 0.35 from a source weighted 0.75 scores 0.26, so a `HAVING` on
the weighted value would silently raise the effective threshold for every source except
the highest-weighted one - a per-source threshold nobody chose.

It is applied to the unweighted `similarity()` instead, which is the value the GUC
actually governs. This preserves ADR-0024's argument exactly, including its limits: it
defends against a threshold left **lower** by another code path (the extra weak matches
are filtered back out) and cannot defend against one left **higher** (which narrows the
index scans themselves). The full-text and code branches carry `NULL` there, because
`pg_trgm.similarity_threshold` has no meaning for an `@@` or an `=` test.

### A full-text branch is only entered when the query has a positive lexeme

`websearch_to_tsquery` gives callers `-` for NOT, and a query with no surviving positive
lexeme lexes to a pure negation: `-glucose` becomes `!'glucos'`, and so does `a -b`, whose
positive half is an `english` stopword. `@@` is satisfied by the *absence* of a lexeme, so
such a query matches every row with nothing for GIN to probe - four sequential scans
returning the whole catalogue at a floor score, from one character, on an unauthenticated
endpoint. This is exactly the "matching everything" failure ADR-0024's threshold
discipline exists to prevent, arriving through the half of the query that has no
threshold, and no nonsense-query test detects it because a nonsense *word* still lexes to
a positive lexeme.

Each full-text branch therefore carries `NOT ('' :: tsvector @@ nptc_search_query(:q))`. A
tsquery matches the empty document exactly when it has no required positive lexeme, so
this tests the condition itself rather than scanning the input for `-`; the predicate
depends only on `:q`, so the planner resolves it once and prunes the branch
(`One-Time Filter: false`) rather than evaluating it per row.

Rewriting the query inside `nptc_search_query` was rejected: the function would then
return a `tsquery` that is not what the caller asked for, and the guard belongs where the
consequence is. The trigram branches need no guard - `%` has no negation - so the query is
still searched, by similarity, for the literal text typed. Refusing such a query outright
was also rejected; it is a well-formed search for a string, and only its full-text
interpretation is degenerate.

### The SNOMED code is matched by equality

A btree index on `code`, partial on `status = 'active'`, and `cb.code = btrim(:q)`. The
four label equality comparisons `btrim` for the same reason (PR #237 review):
`nptc_search_text` folds case and diacritics but does not trim, so a term pasted with
surrounding whitespace has a `similarity()` of `1.0` yet is not `=` to the stored value,
and would be scored as fuzzy - beneath an exact synonym hit on a different entry.
Trimming in the comparison rather than inside `nptc_search_text` avoids rebuilding the
four trigram indexes for a difference `similarity()` cannot see.

`ix_code_binding_one_active_entry_per_code` cannot serve this despite indexing the same
column: `code` is its second column behind `system`, and the search box has no `system`
to supply as a leading equality qualifier.

### Both SNOMED labels are indexed tag-intact, with no stripped second copy

FR-98's search-index row asks that "both forms" be indexed so that any label ever
published reaches the entry. That is satisfied by the tag-intact index alone: the
semantic tag is extra text to trigram and its own lexeme to full-text, so
`Full blood count` and `Full blood count (procedure)` both reach the entry through one
index. `test_search_ranking.py` asserts both forms.

## Rejected alternatives

### Extending trigram to the remaining three fields, without full-text

The smallest possible change, and it satisfies every one of #138's stated acceptance
criteria - word-order reversal in particular scores near 1.0 under trigram, because a
trigram set is unordered to begin with.

Rejected on the FSN, and only on the FSN. For short curated terms this option is
genuinely sufficient and ADR-0024's assessment of it was right. For a long FSN the
Jaccard length penalty makes a short query a poor match however well it corresponds, so
the field would be searched in name more than in practice. Adding a field that is hard
to reach is a worse outcome than not adding it, because it looks done.

### A SQL-side `nptc_strip_semantic_tag(text)` and a second index over the stripped form

The literal reading of FR-98's "both forms indexed", and the obvious way to guarantee a
tag-omitted query matches at full strength rather than merely well enough.

Rejected. The semantic-tag regex exists exactly once in this codebase, in
`nptc_shared.terminology.snomed`, and `test_catalogue_bindings.py` enforces by AST scan
that `strip_semantic_tag` has one legitimate call site. A SQL copy would put a second
implementation of that regex in the database, which
[ADR-0006](0006-designation-reconciliation-strategy.md)'s rejected-alternatives table
names directly: *"A private regex-based tag-stripper local to … recreates the exact
defect class FR-83 exists to prevent."* Buying a ranking improvement with a duplicated
parser for SNOMED's own label grammar is not a trade this codebase makes. A stored
generated column was considered as a way to keep the single Python call site, and refused
for the same reason plus a second: `render_display_term` raises on an FSN with no
trailing group, so a generated column would make a legitimate-but-untagged served label
un-storable.

**The accepted cost, recorded rather than glossed.** Indexing the tag intact makes the
tag itself searchable, so a bare `procedure` reaches every procedure-tagged entry. This
is real and it is the "matching everything" failure mode ADR-0024 warns about, in
miniature. It is bounded by the ranking: a bare tag is a weak fuzzy match against a long
string and can never enter an exact band, so it can never outrank a query a user actually
meant. `test_a_bare_semantic_tag_is_only_ever_a_weak_match` pins that bound. If a query
log later shows users typing bare tags, the answer is a stopword-style exclusion in the
text search configuration, not a second index.

### Trigram over the SNOMED code

So that a mistyped code still finds its entry.

Rejected. A trigram set over eight digits is dense and unselective: codes sharing digit
runs clear the 0.3 threshold in bulk, so every code query would carry a tail of unrelated
entries. A code is right or wrong, and the honest answer to a mistyped one is nothing at
all. `test_the_code_is_matched_exactly_and_not_by_similarity` asserts a one-digit
near-miss finds nothing.

### `word_similarity` (`<%`) instead of full-text for the long-FSN case

`pg_trgm`'s own answer to a short query against a long document, and it needs no second
index type - the existing `gin_trgm_ops` index serves it.

Genuinely attractive, and the closest call here. Rejected because it solves only the
length-ratio half of the problem and leaves the stemming half untouched, while adding a
second operator whose threshold is a *separate* GUC (`pg_trgm.word_similarity_threshold`)
with its own pooled-connection leakage hazard - a second copy of ADR-0024's
`set_config` discipline to get right and to keep right. Worth revisiting if the full-text
indexes prove expensive to maintain in practice; it is a strictly smaller change than
this one, not a larger.

## Consequences

- **The index footprint roughly doubles, as ADR-0024 predicted it would.** Seven new
  indexes: four `tsvector` GIN, two trigram GIN, one btree. This is the cost that
  deferral was protecting, and it is now paid deliberately rather than by default.
- **A second "immutable for a fixed definition" caveat now applies.** ADR-0024 records
  that changing the `unaccent` rule file requires `REINDEX`ing the trigram indexes. The
  same is true of the four full-text indexes if the `english` configuration, its stemmer
  or its stopword list changes underneath a running database - a PostgreSQL upgrade being
  the realistic trigger. Both are in `docs/operations/upgrade.md`.
- **`nptc_search_document` and `nptc_search_query` are the third and fourth database
  functions.** ADR-0023's bar is met the same way `nptc_search_text` met it: pure,
  side-effect-free, defined in a versioned module and created by a migration, with their
  own tests. They normalise; they decide nothing.
- **The `EXPLAIN` guard grew from two assertions to nine.** It is now the only thing
  standing between this query and a branch that silently stops being index-supported,
  and its cost went up accordingly - `test_db_search_index.py` seeds bindings as well as
  entries and designations.
- **FR-16's faceted filtering is not this issue's.** ADR-0024's Consequences and
  `docs/requirements/requirements.yaml` both attributed it to #138; issue #138 scopes
  itself to FR-14/FR-15, and epic #57 splits facets into a separate child. Both notes are
  corrected in this PR.
- **Full-text recall has no threshold, and this is accepted.** `%` compares against
  `pg_trgm.similarity_threshold`, so trigram recall is bounded; `@@` has no analogue and
  admits a row on a single shared lexeme after stemming. A common domain word - `test`,
  `level`, `measurement` - will therefore match a large fraction of the catalogue at a
  score near the floor. Page one is still the best matches, so this is a recall and
  plan-cost consequence rather than a wrong answer. A minimum-rank floor is deliberately
  *not* added: it would be an invented constant, and ADR-0024's discipline of moving a
  threshold only against observed noise needs a production query log that does not exist
  yet. The remedy, when it does, is a rank floor on the full-text branches, tuned the same
  way and in the same direction as the similarity threshold.
- **Callers now have query syntax they did not have before.** `websearch_to_tsquery`
  brings quoted phrases, `or`, and `-` negation with it, which is part of the public
  contract whether or not it was asked for - documented in the `q` parameter description
  and in `docs/architecture/public-api.md`. It applies to the full-text half only; the
  trigram half sees the literal text.
- **The exact-match bands are a contract, the weights are not.** A future change may
  retune `PREFERRED_TERM_WEIGHT` and friends against a real query log. Reordering the
  bands, or letting a weight reach 1.0, breaks FR-14 and fails a test that says so.
