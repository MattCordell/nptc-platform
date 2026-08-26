# The public catalogue API (FR-20)

The read-only, unauthenticated JSON API over the approved catalogue, landed with issue #142.
Its audience is LIS and PMS vendors, not this platform's own SPA - so this
document is the contract, and `docs/api/openapi.json` (generated from the app and
committed, drift-tested by `backend/tests/test_openapi_document.py`) is the machine-
readable form of it.

Design decisions behind the search and paging shapes are recorded in
[ADR-0024](../adr/0024-catalogue-search-and-pagination.md). This document describes what
the API *does*.

## Endpoints

Every path is under `/api/v1`. Every one is `GET`; there is no write surface here.

| Path | Query parameters | Response |
|---|---|---|
| `/catalogue/entries` | `limit` (1-200, default 50), `after` | `{items: [EntrySummary], next_cursor}` |
| `/catalogue/entries/{business_key}` | — | `EntryDetail` |
| `/catalogue/entries/{business_key}/designations` | — | `{items: [Designation]}` |
| `/catalogue/entries/{business_key}/bindings` | — | `{items: [Binding]}` |
| `/catalogue/entries/{business_key}/properties` | — | `{items: [PropertyValue]}` |
| `/catalogue/search` | `q` (required), `limit`, `after` | `{items: [SearchHit], next_cursor}` |

`EntryDetail` is an `EntrySummary` plus `designations`, `bindings` and `properties`, so
one request renders an entry page. The sub-resources are also served individually, for
a client refreshing one panel.

## Authentication

None required. `Role.ANON` holds `Permission.CATALOGUE_BROWSE`, and every route above
depends on that permission (FR-44: the check is against a permission, never a role
name), so an anonymous request is served a 200.

Presenting a *bad* credential is a different thing from presenting none, and is refused:
an unparseable `Authorization` header or an unverifiable token is a 401, never a silent
downgrade to the public view. A client that could not tell a forged token from no token
could not detect its own expired session.

## What is published, and what is not

**Only `active` entries, on every endpoint.** `draft`, `deprecated` and `withdrawn`
entries are absent from the list, absent from search results, and a 404 on the detail
routes. `nptc.catalogue.queries.PUBLIC_STATUSES` is the single filter every query
imports.

The 404 for a hidden entry is byte-identical to the 404 for a `business_key` that was
never minted, deliberately: a distinguishable response would confirm the key exists,
which for a `draft` entry discloses unpublished editorial work. `backend/tests/
test_api_public_status_filter.py` asserts this across every endpoint and every hidden
status.

**Retired code bindings *are* published** (FR-08). An implementer holding a code that
has since been inactivated needs to learn that here, with `retirement_reason` and -
where PRD FR-08's replacement case applies - `replaced_by_code`, the code that
superseded it. Retired *designations* are not published: a retired synonym carries no
forward pointer and no obligation, and is editorial history rather than a term the entry
is known by.

**No internal identifier appears in any response.** `business_key` is the only
identifier a caller ever sees (PRD §6.2). `code_binding.replaced_by_binding_id` is a
UUID in the database and is resolved to the successor's *code* in the read layer, so no
route ever holds an id it could serialise by accident. A `business_key` path parameter
is validated against `^NPTC-[0-9]{6,}$` before any query runs, so a UUID in the path is
a 422 - not a 404, which would imply a UUID is a kind of identifier this API accepts.

**Every SNOMED CT code is a JSON string** (FR-06). This is the defect class the platform
exists to eliminate: an SCTID that reached a client as a JSON number would have passed
through a JavaScript `number` before anyone noticed. The only numbers these endpoints
serve are `length`, `ordinal`, `score` and numeric property values.

Both no-leak invariants are asserted whole-body against raw response text - not field by
field against a parsed model - for every route under the prefix, in
`backend/tests/test_api_public_response_hygiene.py`. The endpoint list there is derived
from the app's own OpenAPI document, so a route added later is covered on the day it is
added, and both regexes ship with positive controls so neither can rot into a pattern
that matches nothing.

FR-06 is also checked at the schema level, not just against live response bodies:
`backend/tests/test_openapi_document.py` derives every `code`/`*_code` property from
`docs/api/openapi.json` itself and asserts each is `type: string` (or nullable string),
so a code field that is declared but never populated in a test fixture is still caught.
The same test module validates the document against the OpenAPI 3.1 meta-schema and
checks the running app serves exactly the committed bytes -
[`docs/api/README.md`](../api/README.md) has the regeneration command and the CI gate
(issue #143).

## Pagination

Keyset, with no offsets. Send `limit`; read `next_cursor` from the response; send it
back as `after` for the next page. `next_cursor` is `null` exactly on the last page.

**Do not infer the end of the collection from a short page** - read `next_cursor`. And
do not construct a cursor: `/catalogue/search` refuses one it did not issue with a 422,
rather than silently restarting from the first page, which would turn a client bug into
an endless paging loop.

**A search cursor belongs to the `q` it was issued for.** Send it back with the same `q`.
Sending it with a different one is a 422: a relevance score is only meaningful against
the query that produced it, so the alternative would be a page that is the next page of
neither query - wrong in a way no client could detect.

`limit` above 200 or below 1 is a 422, not a silent clamp: a clamped limit makes the
response a lie about what was asked for, and a client that pages by "did I receive
`limit` items?" then stops early.

- `/catalogue/entries` orders by `business_key`, ascending. Because that column is
  `UNIQUE`, the ordering is total, so no entry can be skipped or served twice across a
  page boundary even while the catalogue is being edited.
- `/catalogue/search` orders by relevance descending, `business_key` ascending within a
  tie, and its cursor is `<score>:<query digest>:<business_key>`. The digest is what binds
  it to `q`; it is not a signature, and the cursor is not a credential.

## Search

`GET /catalogue/search?q=...` matches against each entry's own preferred term and its
active designations, and returns one result per entry scored by its best match.

- **Insensitive to case and to diacritics** (FR-14): `muller` finds `Müller cell
  antibody`.
- **Tolerant of typographical error** (FR-15): `Haemoglobni electrophoresis` finds
  `Haemoglobin electrophoresis`. Matching is trigram similarity, so a transposition or a
  dropped letter still scores.
- **A query below the similarity threshold returns an empty page**, not a broadened
  match. That is intended: a search that quietly matches everything cannot be told apart
  from a working search over a catalogue with nothing to offer.
- **`q` must contain a non-whitespace character.** A blank query is a 422, not the whole
  catalogue.

`score` is a trigram similarity between 0 and 1. It is comparable *within* one response
(it is what the ordering is), and is not a quality rating of the entry.

**Not searched here:** the FSN, the AU preferred term, and the SNOMED code itself.
Exact-code lookup is FR-17's own endpoint, owned by #140; faceted filtering over
`filterable` properties (FR-16) is #138's. See ADR-0024's Consequences.

## Errors

Every refusal is `{"detail": "<one sentence>"}`. Detail strings are fixed, client-facing
sentences: they never name a role, a permission, an internal identifier, or echo back
user-supplied text (FR-44, NFR-04, NFR-26).

| Status | When |
|---|---|
| 401 | A credential was presented and could not be verified. Sending none is not an error. |
| 404 | No published entry has this business key - including one that exists but is not published. Not produced by `/catalogue/entries` or `/catalogue/search`: an unmatched query is an empty page, not a missing resource. |
| 422 | A malformed `business_key`, a blank `q`, a cursor this API did not issue (including one issued for a different `q`), or a `limit` out of range. |
| 500 | A published code binding's stored FSN is not renderable (below). Only `/catalogue/entries/{business_key}` and its `/bindings` sub-resource can produce it. |

Every status each endpoint can produce is declared in `docs/api/openapi.json`, and only
the ones it can actually produce - so a generated client (#147) has no branch for a
response that never arrives.

The 500 is worth calling out, because it is the one error here that is not a caller
mistake at all. `display_term` is derived from the stored FSN by FR-83's single sanctioned
strip, which refuses a value carrying no semantic tag - by FR-82 every stored `fsn` came
from the terminology server, and a served FSN always has one. The API fails loudly for
that entry rather than blanking the label, because a blanked label hides a corrupted
binding indefinitely.

It is deliberately a 5xx and not a 422 even though the underlying check is a validation
refusal: the request was well-formed and the fault is entirely in the platform's own
stored data. A 422 would tell a vendor's client that *it* sent something wrong, so the
client would neither retry nor escalate - and getting an administrator to look at the
binding is the entire purpose of failing loudly. Retrying will not clear it. It is logged
at `ERROR`, unlike every other refusal here.

## Rate limiting and caching

Neither is implemented yet (FR-22). There are no `Cache-Control` or `ETag` headers, and
no request budget. Clients should page politely and not poll tighter than they need to.
