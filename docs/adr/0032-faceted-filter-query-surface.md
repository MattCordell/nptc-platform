# ADR-0032: The faceted-filter query surface — dotted repeated parameters, a fixed bucket cap, and facets on search only

**Status:** Accepted
**Date:** 2026-09-07

## Context

FR-16 requires catalogue results to be filterable by any property marked `filterable`,
presented as facets with result counts, and names discipline, subgroup, specimen and entry
status as the minimum. The point of the requirement is not the filtering — it is the
*derivation*. [ADR-0013](0013-datatype-handler-registry.md) built the property registry so
an administrator could add or change a property without a deployment (FR-09), and a
hard-coded facet list would defeat that in the one place a user would actually see it.

Everything below the query surface already existed and was unused. `DatatypeHandler`
carries `supported_filter_ops()`, `filter_clause()` and `facet_expression()` — three
members added by issue #53 specifically for this caller, implemented on all five
datatypes, returning SQLAlchemy `ColumnElement`s so NFR-22 holds by construction rather
than by review. Issue #54 generates a partial index per filterable property. What was
missing was the HTTP-to-SQL path between them: `GET /catalogue/search` took only `q`, `GET
/catalogue/entries` only `limit`/`after`, and nothing counted anything.

Three questions had to be settled before the router could be written, because all three
are public contract (FR-20) and the OpenAPI `breaking` CI job polices every later change
to them.

## Decision

### Filters are dotted, prefixed query parameters, repeated once per value

```http
GET /api/v1/catalogue/search?q=glucose
    &filter.discipline=Chemistry
    &filter.discipline=Haematology
    &filter.specimen=119297000
```

Within one facet, repeated values are **OR**-ed; across facets they are **AND**-ed. That
is the only composition under which a facet panel behaves the way every user of one
expects: ticking a second box in one facet broadens the result, ticking a box in a second
facet narrows it.

An operator other than the default `equals` is named after the key, separated by a colon:
`?filter.assay_name:prefix=glu`, `?filter.volume_ml:range=1..5`. Which operators a facet
accepts is not a list in the router — it is the property handler's own
`supported_filter_ops()`, so `prefix` against a coded property (whose stored value is an
object, with no prefix to take) is refused by the handler's own contract.

The `filter.` prefix is the load-bearing part. A bare `?discipline=chemistry` collides
with `q`, `limit` and `after` today and with every parameter this API grows later — and
the collision is silent, because a property key is administrator-supplied and could one
day *be* `limit`. The prefix carves out a namespace that is by construction unable to
collide.

FastAPI cannot generate this shape from a typed signature: the parameter *name* is
`filter.<whatever an administrator marked filterable>`, which is not knowable when the
router is written. The dependency therefore reads `request.query_params.multi_items()`
directly — `multi_items()`, never `items()`, which keeps only the last value of a repeated
key and would quietly turn a two-value selection into a one-value one. And because
FastAPI cannot generate the parameter, the route declares it by hand through
`openapi_extra` (which *appends* to the generated parameter list rather than replacing it,
via FastAPI's `deep_dict_update`). Without that declaration `docs/api/openapi.json` would
omit a parameter the API accepts, and FR-20 makes that document the contract vendors build
against — a parameter absent from it is a parameter nobody is protecting.

### Every unusable filter parameter is a 422, never a silent drop

An unknown key, a key naming a property that is not `filterable` (or is deprecated), an
operator outside the handler's `supported_filter_ops()`, and a value that cannot be
interpreted as the property's values are, are all refused.

The alternative — ignore what we do not understand — is the worst outcome available here,
worse than refusing something we could have served. The caller is handed a page that looks
exactly like an answer to the question they asked and is an answer to a different one,
with nothing in the response to tell the two apart. This is the same discipline
[ADR-0024](0024-catalogue-search-and-pagination.md) applies to a cursor the API did not
mint.

The response body is one fixed sentence for all four cases. It names no property key and
no value: the parameter is caller-supplied text on a public, unauthenticated endpoint
(NFR-26/NFR-35), and *which* properties exist but are not offered as filters is editorial
state this surface has no business disclosing. The four exception classes are distinct
only in the log.

### The per-facet bucket cap is 20, ordered by count descending, and truncation is stated

A `string` property with thousands of distinct values would otherwise return thousands of
buckets from one unauthenticated request. Twenty is an invented number — no facet panel is
usable past a couple of dozen entries, and there is no production usage log to calibrate
against — which is exactly why it is named here and declared once as
`nptc.catalogue.facets.FACET_BUCKET_CAP` rather than buried at a call site. It is in the
same category as ADR-0024's threshold discipline: a figure to be revisited against
observed behaviour, not tuned by taste.

When the cap bites, the facet says so (`truncated: true`). There is deliberately no way to
page through the remainder: facet-value paging is a second pagination scheme on a surface
that already has one, and the useful answer to "there are more than twenty values" is to
narrow the search, not to scroll.

### `/catalogue/search` returns facets; `/catalogue/entries` accepts filters and returns none

Both endpoints take the identical `filter.*` parameters, composed by the identical
builder. Only the search response carries a `facets` array.

Returning facets from both would be more consistent, and computing counts on every page of
a browse is a cost nobody has asked for: a browse is a caller walking the catalogue in
`business_key` order, and the facet panel belongs to the search screen. Adding facets to
`/catalogue/entries` later is an additive change to that response; removing them would not
be, which is the asymmetry that makes starting narrow the cheaper mistake.

The two cursors also differ, and deliberately. A search cursor carries a relevance score,
so it is bound to the filter set as well as to `q` — narrowing the filters changes which
entries exist to be scored at all, so a replayed cursor would name a window that is the
next page of neither request, and it is refused with a 422 exactly as a changed `q`
already is. `/catalogue/entries`' cursor is a `business_key`: the ordering is on that
column alone and is total whatever the filters are, so a replay under different filters
still names an unambiguous position, and is served.

### Entry status is a declared core-column facet, not a property

FR-16 names entry status alongside discipline, subgroup and specimen. The other three are
registry properties and enumerate themselves; status lives on `catalogue_entry` and has no
`PropertyDefinition` to be discovered from.

It is therefore *declared* — one entry in a frozen tuple of descriptors, in exactly the
shape the registry-derived ones produce, consumed by the identical predicate and
aggregation code. No line of the query builder branches on it. The only difference is
which `FacetSource` a descriptor holds, which is a fact about where a value is stored,
never about its datatype (FR-77).

On the public API this facet is degenerate: `PUBLIC_STATUSES` restricts that surface to
`active`, so it has exactly one bucket. It earns its keep on the administrative listing
(issue #266); #139 ships the mechanism rather than waiting for the surface.

### Coded facets group on the code alone, labelled from the stored `display`

This resolves [ADR-0013](0013-datatype-handler-registry.md)'s open question 2, which #139
was named to settle. `CodeHandler.facet_expression()` already returned `value['code']`; it
is now that by decision rather than by deferral.

Not `(system, code)`: the catalogue binds one value set per property, so a code within one
property is unambiguous, and a compound bucket value would have to be escaped and parsed
to be sent back as a filter.

The bucket's label is the `display` stored beside the code when the value was recorded —
never a live terminology lookup on the search path (FR-54). A facet panel that made one
`$lookup` per bucket would be slow on the happy path and would degrade the moment the
terminology server was unreachable, which is precisely the coupling FR-54 exists to
prevent. The label expression is `jsonb_extract_path_text(value, 'display')`, generic JSON
handling that yields `NULL` for any value which is not an object carrying that member; the
bucket then falls back to its own grouping value. Nothing in the facet code knows that
`code` is the datatype which happens to have a `display`.

### Filter predicates are `EXISTS` subqueries; counts are `COUNT(DISTINCT entry_id)`

A property may be multi-valued (`specimen` is `0..*`), so one entry can own several
`property_value` rows for one key. A join returns that entry once per matching row. An
entry with seven specimens must match a filter once and must contribute **one** to each of
seven buckets — never seven to one. `EXISTS` on the filter side and
`COUNT(DISTINCT entry_id)` on the count side are what make that true, and a test asserts
it directly, because an implementation that gets it wrong passes every other test in the
suite.

Facet counts exclude their own facet's selection: computing `discipline`'s buckets applies
`q` and every *other* facet's filters. Applying a facet's own filter to its own counts
gives the degenerate answer — one bucket, the one already chosen — and makes the panel a
dead end.

### `property_key` is rendered as a literal, and that is not string-built SQL

Issue #54's generated index is partial on `property_key = '<literal>'`. A *bound* key
cannot use it under a generic plan: the planner has no way to prove `$1` will equal the
literal the index is partial on
(`backend/tests/test_db_property_index_plan.py` proves both halves). The key is therefore
bound with SQLAlchemy's `literal_execute=True`, which renders it inline at execution
through SQLAlchemy's own escaping. The value never touches statement text in Python, and
`property_key` is constrained by the database to `^[a-z][a-z0-9_]{0,62}$` besides — so
NFR-22's concern, runtime data reaching a query as text, does not arise.

### The search statement becomes a Core select over a scored CTE

`nptc.catalogue.search`'s nine index-supported scans and their per-entry `MAX` remain a
module-level `text()` literal, unchanged, exposed as a CTE through `.columns(...)`. The
shell around it — served columns, status filter, filter predicates, keyset predicate — is
now SQLAlchemy Core.

It has to be. The filter list is derived per request, so a statement that grows a clause
per request cannot stay a fixed literal without being assembled from strings, which is
exactly what NFR-22 forbids. Composing it as Core keeps every value bound by construction,
and it keeps the result page and the facet counts answering the same question about the
same population, because they share one predicate list rather than two copies kept in step
by hand.

## Consequences

- FR-16 is served on both public collection endpoints, and the facet list is a `SELECT`
  against `property_definition` on every request. An administrator flipping `filterable`
  changes the answer on the next request — asserted end to end by flipping the flag
  through the real registry `PATCH` route on a running app, which is the only form in
  which FR-09's "no restart" claim is falsifiable.
- A `filterable` property whose handler returns `None` from `facet_expression()` — a
  `decimal`, where every value is its own bucket — is a usable *filter* with no buckets,
  and is reported as `facetable: false` rather than omitted. ADR-0013 §8 calls this the
  stated cost; a facet that vanished silently would be indistinguishable, to a client,
  from one whose values happen to match nothing.
- `nptc.catalogue.queries`' ban on `COUNT(*)` over the catalogue now has one documented
  exception, amended into that module's own docstring. The ban is on a *page total*: a
  number the client did not ask for, costing a scan of everything the page did not serve.
  A facet count is the answer to the question, is bounded by the bucket cap, and reads one
  property's rows rather than the table's.
- A facet count's `GROUP BY` cannot use #54's generated index, and the plan test says so
  rather than asserting something weaker and calling it a proof. That index holds the
  value expression only; a facet count also needs `entry_id` for its `COUNT(DISTINCT ...)`
  , so no index-only scan serves both. The filter predicate *does* reach it, which is the
  claim #139 inherits from #54, and the aggregation is asserted to cost the size of the
  property rather than of the whole table.
- The frontend is untouched. There is no public search screen yet — the route tree still
  mounts a placeholder — so the facet panel is a separate follow-up issue that #139
  blocks. Note for whoever picks it up: the existing `CatalogueSearch` params (`q`,
  `page`, `sort`) use a page *number*, which ADR-0024 says keyset paging cannot serve;
  reconciling that belongs to the UI issue.
- Dispatching `reconcile_property_indexes()` from the registry write path stays out of
  scope, in its own follow-up issue (#274). Facets are correct without the generated index,
  only slower, and the operator runbook remains the trigger.
- Self-review after the first pass found three cases the "every unusable filter is a 422"
  decision above committed to but the implementation had not yet closed: an unrecognised
  `status` value (the core-column facet has no `PropertyDefinition` handler to validate
  against, so it must check itself against `CatalogueEntryStatus`), the same facet key sent
  with two different operators (grouping selections by `(key, op)` let this through as two
  selections ANDed into a predicate no row can satisfy, rather than one refusal), and a
  cursor-digest collision (`filter_digest_material` joined multiple raw values on a plain
  `,`, so a value containing a literal comma could digest identically to two separate
  repeated values — length-prefixing every value closes it regardless of content). All
  three are now refused with a 422 and covered by a test at both the unit and HTTP layer.
- Computing every facet's bucket counts used to cost one query per facet, and each one
  re-scanned and re-scored the whole matched set independently — Postgres does not share a
  CTE's result across separately-submitted statements. That cost was bounded by the number
  of `filterable` properties, not by `FACET_BUCKET_CAP`, and grew as an administrator marked
  more properties filterable (FR-09). Fixed in issue #275, after landing as a tracked
  follow-up here: it was a latency question, not a correctness one, so the fix is
  independent of the query surface this ADR settles.

  `nptc.catalogue.facets.build_facet_counts_statement` unions every facetable descriptor's
  `build_facet_count_statement` — reused verbatim, unchanged — into one statement, wrapping
  each branch in its own subquery so its own `LIMIT FACET_BUCKET_CAP + 1` and grouping
  expression survive the union untouched. Every branch closes over the *same* scored-CTE
  object the caller supplies, and a CTE referenced more than once is what Postgres
  materialises rather than re-plans per reference — so the expensive scan happens once per
  request regardless of facet count. `compute_facets` executes that one statement and splits
  the rows back into each facet's own buckets in Python, ordered by a `bucket_rank` window
  function computed per branch, before the union, over each branch's own still-typed `value`
  column (`row_number() OVER (ORDER BY bucket_count DESC, value ASC)`) — not by the `value`
  column the union itself exposes, which is cast to `TEXT` so `UNION ALL` can share one
  column across handlers whose native value types differ (`text`, `numeric`). A first version
  of this statement ordered on the cast column directly; a PR #315 review caught that this
  reorders a `positiveInt` facet's ties (`'10' < '100' < '2'` as text, `2 < 10 < 100` as
  numbers) and, worse, changes which row the bucket cap truncates away, since the inner
  `LIMIT` keeps the correct top 21 by numeric order but the Python-side slice then drops
  whichever arrives 21st in the outer statement's own order. `test_db_property_index_plan.py`
  now asserts the served order for a numeric facet directly.

  **Why not `GROUPING SETS`.** Each facet's population differs (a facet's own selection is
  excluded from its own counts, above), and each facet's grouping expression comes from a
  different property's handler, so there is no single `GROUP BY` base a `GROUPING SETS`
  formulation could share across facets — a `UNION ALL` of independently-based aggregations
  was the shape that fit, not a stylistic preference.

  **What this trades away.** The old N separate statements gave the planner N separate,
  smaller planning problems; the combined statement gives it one planning problem sized by
  every facet at once — up to `FILTER_VALUE_CAP` (50) `EXISTS` branches per facet, times the
  facet count, in a single planner invocation. Total query *work* is no worse (each branch
  still costs what it always cost), but the planner's *search space* for that one invocation
  is larger. Not judged a blocker: planning cost for this shape of query is not the
  bottleneck NFR-32 is aimed at, and nothing here has shown otherwise.

  **Why the threshold round trip (`_SET_THRESHOLD_SQL`) was considered and kept.**
  `search_facets` re-issues it even though `search_entries` already set it earlier in the
  same request/transaction. Folding it away would save one `set_config` round trip on a
  local socket, at the cost of `search_facets` trusting that an earlier, unrelated call set
  the GUC it depends on rather than asserting its own precondition — not a trade this ADR's
  threshold-discipline (`search.py`'s own docstring) is willing to make for one round trip.

  Proof: `test_db_search_index.py`'s `EXPLAIN` test shows the scoring scan's own `Index
  Cond` count fixed across 2 and 5 synthetic facets, and `test_api_public_search.py`'s
  statement-count test shows `compute_facets` issuing exactly one statement whatever the
  seeded catalogue's real facet count is.
- A maintainer review round found four more gaps, all now closed. `_request_digest` bound
  the cursor to `q` and the filter set by concatenating them, and only the filter side was
  length-prefixed — `q` is arbitrary caller-supplied text too, so it could in principle end
  in something that reads as a well-formed continuation of the filter material that
  follows it, and two different `(q, filters)` pairs could then concatenate to an identical
  digest input. `q` is now length-prefixed the same way. Repeated values within one facet
  were not order-canonicalised in the digest, so `?filter.discipline=a&filter.discipline=b`
  and the same request with the two values swapped — the same query, since OR is
  commutative — minted different cursors; `filter_digest_material` now sorts a selection's
  `raw_values` before joining them. Nothing capped how many values one facet's selection
  could carry, so an operator other than `IN` could be handed an unbounded `OR` chain of
  correlated `EXISTS` subqueries on an unauthenticated endpoint; `FILTER_VALUE_CAP` (50,
  named for the same reason `FACET_BUCKET_CAP` is) now refuses a selection larger than that
  with a 422. And `?filter.status=<a real but non-public status>` was accepted and matched
  nothing, forever, on a public route restricted to `active` — the core `status` facet
  validated against the whole `CatalogueEntryStatus` enum rather than the calling surface's
  own permitted set; `load_facet_context` now takes `status_values` from the caller
  (`queries.PUBLIC_STATUSES` on the public routes) and threads it into the descriptor,
  leaving room for a future admin listing (#266) to pass the whole enum instead.
- The same review also found, and this ADR now records rather than leaving to be
  rediscovered: **a client generated from `docs/api/openapi.json` cannot express a filter
  parameter as a typed field.** OpenAPI has no syntax for a templated parameter name, and
  `FILTER_PARAMETER`'s name is `filter.{property_key}` — a placeholder, not a literal one —
  so `openapi-typescript` emits a field named literally `"filter.{property_key}"`. Filling
  that field in and sending it produces a 422 (`{property_key}` is not a filter this
  endpoint offers), because the literal placeholder text is not a real facet key. This is
  not a defect in the generated client; it is OpenAPI's own limit on what a *dynamically
  named* parameter can look like in a schema. `FILTER_PARAMETER`'s description says so
  explicitly for a human reading the spec by hand. Filed as its own follow-up (#276) rather
  than decided here, since there is no consumer yet to decide it against: whoever builds
  the facet UI (blocked on this PR, not yet opened — see "Out of scope" below) either
  builds the parameter name by hand outside the generated client's type, as this ADR's own
  router does, or reopens the wire-syntax question with `style: deepObject`
  (`filter[discipline]=chem`), which *is* representable in a generated client's types at
  the cost of relitigating the decision this ADR already settled.
- A second, smaller maintainer review round found three more things worth closing before
  merge. `FILTER_VALUE_CAP` was invisible to the caller who tripped it — argued in this ADR
  but absent from every client-facing surface — so `FILTER_PARAMETER`'s description,
  `docs/architecture/public-api.md`, and `_DETAIL_FILTER_REFUSED` now all say a facet's
  selection has a limit, without naming the number in the 422 body itself (consistent with
  how every other member of that refusal family stays generic). A duplicate value within
  one facet's selection — `?filter.x=a&filter.x=a` — sorted correctly but was never
  deduplicated, so it still minted a different cursor than `?filter.x=a` alone despite OR
  being idempotent as well as commutative, and still built a redundant `EXISTS` clause;
  `parse_filters` now dedupes `raw_values` with `dict.fromkeys` (order-preserving), which
  fixes both at the source rather than patching the digest and the predicate builder
  separately. And `CORE_FACET_KEYS` had drifted from a value *derived from* `_core_facets`
  into an independently-restated literal when `_core_facets` became a function — it is now
  `frozenset(d.key for d in _core_facets(()))` again, so a second core facet is one edit,
  not two that can silently fall out of step.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Bare query parameters (`?discipline=chemistry`) | Collides with `q`/`limit`/`after` and with every parameter added later — silently, since a property key is administrator-supplied and could one day *be* one of those names. |
| One structured parameter (`?filter={"discipline":["chem"]}`) | A second encoding inside a query string: unreadable in a log, awkward to build by hand, and it makes an OpenAPI description of the shape strictly less useful than the repeated form's. |
| Generating the parameter from a typed FastAPI signature | Impossible by construction: the parameter name depends on registry state, so a typed signature could only ever name the facets that existed when the file was written — the hard-coded list FR-16 exists to prevent. |
| No bucket cap | One unauthenticated request over a `string` property with thousands of distinct values returns thousands of buckets. |
| Paging through facet values | A second pagination scheme on an endpoint that already has one, for a case whose real answer is "narrow the search". |
| Facets on `/catalogue/entries` too | Counts on every page of a browse, for a screen with no facet panel. Additive to add later; not additive to remove. |
| Grouping coded facets on `(system, code)` | One value set per property makes the code unambiguous, and a compound bucket value would need escaping and parsing to be sent back as a filter. |
| Resolving facet labels through the terminology server | A `$lookup` per bucket on the search path — slow on the happy path, broken when the server is unreachable, and precisely the coupling FR-54 forbids. |
| A join instead of `EXISTS`/`COUNT(DISTINCT ...)` | Counts a multi-valued entry once per stored value. Passes every functional test that does not specifically look for it. |
| Silently ignoring an unrecognised filter | Serves a page that answers a different question than the caller asked, undetectably. |
| A bound `property_key` in the value subqueries | Defeats #54's partial index under a generic plan — the exact defect `test_db_property_index_plan.py` was written to demonstrate. |
| Special-casing entry status in the query builder | Two code paths for one concept; the second one drifts. Declaring it as a descriptor costs a five-line tuple. |
