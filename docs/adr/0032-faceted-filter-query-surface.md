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

```
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
  scope, in its own follow-up issue. Facets are correct without the generated index, only
  slower, and the operator runbook remains the trigger.

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
