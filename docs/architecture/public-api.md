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

Every path is under `/api/v1`. Every one below is `GET` and requires no credential.
There is a separate, authenticated write surface over code bindings (issue #219) and
designations (issue #224) - see [catalogue-write-api.md](catalogue-write-api.md) -
which lives under this same `/catalogue` path but is documented on its own, since it
is not part of the public, unauthenticated contract this document describes.

| Path | Query parameters | Response |
|---|---|---|
| `/catalogue/entries` | `limit` (1-200, default 50), `after` | `{items: [EntrySummary], next_cursor}` |
| `/catalogue/entries/{business_key}` | — | `EntryDetail` |
| `/catalogue/entries/{business_key}/designations` | — | `{items: [Designation]}` |
| `/catalogue/entries/{business_key}/bindings` | — | `{items: [Binding]}` |
| `/catalogue/entries/{business_key}/properties` | — | `{items: [PropertyValue]}` |
| `/catalogue/search` | `q` (required), `limit`, `after` | `{items: [SearchHit], next_cursor}` |

`EntryDetail` is an `EntrySummary` plus `designations`, `bindings`, `properties` and
`row_version`, so one request renders an entry page. The sub-resources are also served
individually, for a client refreshing one panel.

`row_version` (issue #227) is FR-38's optimistic-locking token. It is not an identifier
and a read-only consumer can ignore it: `business_key` is still the only thing that
names an entry, and this counter addresses nothing - it exists so an *editing* client
(the admin API's `/amendment`, see
[catalogue-write-api.md](catalogue-write-api.md#expected_row_version-required-on-one-branch-honoured-on-both))
can prove it is not overwriting a change it never saw. It is on `EntryDetail` and not
`EntrySummary` deliberately: the detail is what an edit screen loads before it can edit
anything, whereas a list or a search result is not an editing context, and putting the
token on the summary would publish a per-row counter on every page to serve a case that
does not exist yet.

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
status. This contract is for an anonymous or under-permissioned caller only - an
authenticated Administrator loading an entry to edit it uses a separate, permission-gated
route (`GET /catalogue/admin/entries/{business_key}`, issue #228,
[catalogue-write-api.md](catalogue-write-api.md#entry-read-any-status-issue-228)), never
this one.

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

`GET /catalogue/search?q=...` matches against all five of FR-14's searchable fields - the
entry's own preferred term, its active designations, and the fully specified name, AU
preferred term and SNOMED code of its active binding - and returns one result per entry
scored by its best match. `docs/architecture/search.md` documents which indexes serve
which field and how the scores are combined.

- **One query field, five fields searched** (FR-14): `49466006`, `ACTH`,
  `Adrenocorticotropic hormone` and `Corticotropin` all reach the same entry.
- **Insensitive to case and to diacritics** (FR-14): `muller` finds `Müller cell
  antibody`.
- **Tolerant of typographical error** (FR-15): `Haemoglobni electrophoresis` finds
  `Haemoglobin electrophoresis`. Trigram similarity is what carries this - a
  transposition or a dropped letter still scores.
- **Tolerant of word order and of inflection** (FR-15): `electrophoresis haemoglobin`
  finds `Haemoglobin electrophoresis`, and full-text stemming matches a plural or an
  inflected form against the stored singular.
- **A SNOMED code is matched exactly**, not fuzzily. A code with a wrong digit finds
  nothing, rather than a list of codes that look similar.
- **An FSN is matched with or without its semantic tag.** `Full blood count` and
  `Full blood count (procedure)` both reach the entry; labels are stored and indexed
  exactly as served (FR-82, FR-98).
- **Retired designations and retired bindings are never a way in.** They are history, not
  a route to the entry.
- **A query below the similarity threshold returns an empty page**, not a broadened
  match. That is intended: a search that quietly matches everything cannot be told apart
  from a working search over a catalogue with nothing to offer.
- **`q` must contain a non-whitespace character.** A blank query is a 422, not the whole
  catalogue.

`score` is a relevance score between 0 and 1, combining trigram similarity, full-text
rank and how the entry was matched - an exact hit on the code or the preferred term
scores above any fuzzy match. It is comparable *within* one response (it is what the
ordering is), and is not a quality rating of the entry. The bands are documented in
[search.md](search.md); the weights behind them may be retuned, so a client should order
by it rather than threshold on it.

**Not here:** exact-code lookup as its own addressable endpoint is FR-17's, owned by
issue #140 - typing a code into `q` works, but a stable per-code URL is separate.
Faceted filtering over `filterable` properties (FR-16) is a separate child of epic #57.
See [ADR-0029](../adr/0029-hybrid-full-text-and-trigram-search.md).

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

## Compatibility and breaking changes

`.github/workflows/openapi.yml`'s `breaking` job (issue #206) diffs a PR's
`docs/api/openapi.json` against the same file on the base branch and blocks the PR if the
diff narrows something a consumer (concretely, issue #147's generated TypeScript client)
could depend on:

- a removed path or operation, or a removed `2xx` response status
- a request narrowed: a parameter removed or made required, a new required parameter, a
  request-body property newly required, a tightened `enum`/`maximum`/`minimum`/`maxLength`/
  `minLength`/`pattern`, or a schema that no longer accepts `null`
- a response narrowed: a property removed, a property demoted from required to optional,
  a scalar `type` changed, or a value added to a response `enum` (a client switching
  exhaustively on it now has an unhandled case)

Everything else - a new path, a new optional parameter, a new response property, a
relaxed request constraint, or any description/summary/title edit - is not flagged.

A maintainer who intends the break adds the `breaking-change-approved` label to the PR;
the check re-runs and passes, but `breaking-change` stays applied as the record of what
happened. `scripts/openapi_breaking_check.py` implements the rules and can be run
directly: `uv run python scripts/openapi_breaking_check.py --base <old.json> --head
<new.json>`.

## Generated TypeScript client (issue #147)

`frontend/src/api/schema.ts` is generated from this document by
[openapi-typescript](https://openapi-ts.dev/) - it is not hand-authored, and no
request/response interface should ever be hand-written alongside it. Regenerate it with:

```powershell
pnpm --filter nptc-frontend generate:api
```

Run this whenever `docs/api/openapi.json` changes (i.e. after regenerating it per
`docs/api/README.md`) and commit the result. `.github/workflows/openapi.yml`'s `client`
job and the local `generated-api-client-is-current` pre-commit hook both regenerate and
diff the file, so a stale commit fails CI the same way a stale `openapi.json` does.

Generation is a script, not a `pnpm build` step - `openapi-typescript` reads a file on
disk and needs no network access, but wiring it into the build would make the build's
output depend on regeneration order and put codegen on the offline clean-clone path that
`pnpm build` otherwise stays off of.

`frontend/src/api/client.ts` wraps the generated `paths` type in
[openapi-fetch](https://openapi-ts.dev/openapi-fetch/), attaching a bearer token from
the auth context's `getAccessToken()` on every request (never cached - a renewal may be
pending). `frontend/src/api/queries.ts` wraps that in TanStack Query hooks.
`frontend/src/api/fr-06.ts` is a compile-time guard: type-only assertions that every
SNOMED CT identifier field in the generated schema is `string`, never `number` (FR-06) -
if a future backend change ever typed one as a number, `pnpm typecheck` fails on this
file rather than the defect reaching the frontend silently.
