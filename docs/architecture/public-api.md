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

`/history`'s own `changed_by` field is the one exception to "requires no credential" -
see [Change history](#change-history-fr-19) below. The route itself still returns `200`
to an anonymous caller; only that one field's population depends on a credential.

| Path | Query parameters | Response |
|---|---|---|
| `/catalogue/entries` | `limit` (1-200, default 50), `after` | `{items: [EntrySummary], next_cursor}` |
| `/catalogue/entries/{business_key}` | — | `EntryDetail` |
| `/catalogue/entries/{business_key}/designations` | — | `{items: [Designation]}` |
| `/catalogue/entries/{business_key}/bindings` | — | `{items: [Binding]}` |
| `/catalogue/entries/{business_key}/properties` | — | `{items: [PropertyValue]}` |
| `/catalogue/entries/{business_key}/history` | `limit` (1-200, default 50), `before` | `{items: [HistoryEvent], next_cursor}` |
| `/catalogue/search` | `q` (required), `limit`, `after` | `{items: [SearchHit], next_cursor}` |
| `/catalogue/code/{system_token}/{code}` | — | `EntryDetail` |
| `/catalogue/lookup` | `system` (required), `code` (required) | `EntryDetail` |

`EntryDetail` is an `EntrySummary` plus `designations`, `bindings`, `properties` and
`row_version`, so one request renders an entry page. The sub-resources are also served
individually, for a client refreshing one panel. `/history` is a fourth sub-resource in
this same sense, but is not folded into `EntryDetail` itself: unlike the other three, it
is its own paged collection (see [Change history](#change-history-fr-19) below), not a
bounded list that fits comfortably in one combined response.

`has_open_finding` (FR-18, issue #141) is on `EntrySummary` itself, so it appears on
every list row, every search hit, and the detail response alike - see
[What is published, and what is not](#what-is-published-and-what-is-not) below for
exactly what it does and does not expose.

`code` and `disciplines` (issue #439) are also on `EntrySummary`, so a list screen can show
them without a request per row. `code` is the entry's one active SNOMED CT binding, as a
string, or `null` when it has none. A retired binding never appears there, only in the
detail's `bindings`. `disciplines` is the stored display text of each `discipline` value,
in `ordinal` order, falling back to the code where a value carries no display. On the public list and
search only, `fsn` and `specimens` (issue #511) sit beside them. `fsn` is the active binding's FSN with its semantic
tag removed. `specimens` is each `specimen` value's stored display, in `ordinal` order, with a
trailing "specimen" word removed and repeats dropped, and the code where a value carries no
display. The seed stores a SNOMED CT-AU preferred term as that display, but the admin write
path stores whatever the client sends, so a value written there may be another wording. One
loader, `nptc.catalogue.queries.row_facts`, fills these fields and `has_open_finding` in three
statements per page, whatever its size. It is keyed on `business_key`, because a search
hit carries no entry id.

`row_version` (issue #227) is FR-38's optimistic-locking token. It is not an identifier
and a read-only consumer can ignore it: `business_key` is still the only thing that
names an entry, and this counter addresses nothing - it exists so an *editing* client
(the admin API's designation and code-binding write routes, see
[catalogue-write-api.md](catalogue-write-api.md#expected_row_version-required-on-every-route-and-every-route-bumps-it))
can prove it is not overwriting a change it never saw. It is on `EntryDetail` and not
`EntrySummary` deliberately: the detail is what an edit screen loads before it can edit
anything, whereas a list or a search result is not an editing context, and putting the
token on the summary would publish a per-row counter on every page to serve a case that
does not exist yet.

## Authentication

None required. `Role.ANON` holds `Permission.CATALOGUE_BROWSE`, and every route above
depends on that permission (FR-44: the check is against a permission, never a role
name), so an anonymous request is served a 200, unless that caller's address has used up its
request budget and gets a 429 (see [Rate limiting and caching](#rate-limiting-and-caching)).

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
authenticated Administrator loading an entry to edit it, or finding one to edit in the
first place, uses a separate, permission-gated surface instead, never this one:
`GET /catalogue/admin/entries/{business_key}` (issue #228),
`GET /catalogue/admin/entries` and `GET /catalogue/admin/search` (issue #266, every
`CatalogueEntryStatus` rather than `PUBLIC_STATUSES` alone) - see
[catalogue-write-api.md](catalogue-write-api.md#entry-read-any-status-issue-228) and
[catalogue-write-api.md](catalogue-write-api.md#all-status-listing-and-search-issue-266).

**Retired code bindings *are* published** (FR-08). An implementer holding a code that
has since been inactivated needs to learn that here, with `retirement_reason` and -
where PRD FR-08's replacement case applies - `replaced_by_code`, the code that
superseded it. Retired *designations* are not published on this surface: a retired
synonym carries no forward pointer and no obligation, and is editorial history rather
than a term the entry is known by.

That reasoning is about *this*, the public surface, and stops at it (issue #239): the
admin route (`GET /catalogue/admin/entries/{business_key}`) serves retired designations
too, because its reader is an editor deciding against editorial history, not an
implementer with no use for it. See
[catalogue-write-api.md](catalogue-write-api.md#entry-read-any-status-issue-228).

**SNOMED CT synonyms are fetched live, not stored** (FR-53, FR-54, issue #517). The
detail routes carry `snomed_synonyms`, which has three states:

| Value | Meaning |
|---|---|
| `null` | The entry has no active SNOMED CT binding, so nothing was looked up. |
| `{"status": "available", "terms": [...]}` | The terminology server's synonyms for the active code. `terms` is empty when the concept has none, or when the code is not in the current AU edition. |
| `{"status": "unavailable", "terms": []}` | The server could not answer. The rest of the response is unchanged and still 200. |

`terms` leaves out the FSN and the AU preferred term, which `bindings` already carries. It
is not limited to en-AU synonyms, because no FHIR operation separates them, so US
spellings can appear. `label_provenance` is the same `synonym`/`not_applicable` descriptor
a `designations` row carries (FR-98). Results are cached in process by code for 24 hours,
so this field can lag a new SNOMED CT release by a day. After the server fails, every entry
shows `unavailable` for 60 seconds without asking it again. It
does not take part in `row_version` or the change history. List and search rows carry no
such field and make no terminology call.
[terminology-client.md](terminology-client.md#entry-detail-live-snomed-ct-synonyms-issue-517)
has the filter, the probe that ruled out an en-AU filter, and the failure budget.

**`has_open_finding` names nothing about the finding itself** (FR-18, issue #141). It is
a bare boolean: `true` when the entry carries at least one `open` `ValidationFinding`,
`false` when its findings (if any) are all `acknowledged`, `resolved` or `superseded`,
or when it has none. No finding type, no severity, no count, and no internal id ever
accompanies it, for any caller including an anonymous one - there is exactly one place
on the response models such a value could ever be added, and none is. An authenticated
Reviewer or Administrator wanting the finding's actual detail uses a separate,
permission-gated surface once P3's sweep and acknowledge lifecycle land; this API never
carries it.

**No internal identifier appears in any response.** `business_key` is the only
identifier a caller ever sees (PRD §6.2). `code_binding.replaced_by_binding_id` is a
UUID in the database and is resolved to the successor's *code* in the read layer, so no
route ever holds an id it could serialise by accident. A `business_key` path parameter
is validated against `^NPTC-[0-9]{6,}$` before any query runs, so a UUID in the path is
a 422 - not a 404, which would imply a UUID is a kind of identifier this API accepts.

**Every SNOMED CT code is a JSON string** (FR-06). This is the defect class the platform
exists to eliminate: an SCTID that reached a client as a JSON number would have passed
through a JavaScript `number` before anyone noticed. The only numbers these endpoints
serve are `length`, `ordinal`, `score` and numeric property values. A row's `code` is
`null` or a string, never a number.

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
- **Surrounding whitespace does not change the answer.** A code or a term pasted out of a
  spreadsheet still counts as an exact match, rather than dropping to a fuzzy score -
  including the trailing carriage return and newline a single copied cell carries.
- **`q` accepts web-search syntax, on the full-text half only.** A quoted `"phrase"`, `or`
  between alternatives, and a leading `-` to exclude a word. A query that can be satisfied
  by exclusion alone - `-glucose`, and also `zymogen or -kinase`, whose negated half would
  match nearly everything on its own - is dropped from that half rather than returning the
  catalogue, and is still searched by similarity as the literal text typed.

`score` is a relevance score between 0 and 1, combining trigram similarity, full-text
rank and how the entry was matched - an exact hit on the code or the preferred term
scores above any fuzzy match, and a full-text-only match is scored on the same range as a
trigram one rather than beneath it. It is comparable *within* one response (it is what the
ordering is), and is not a quality rating of the entry. The bands are documented in
[search.md](search.md); the weights behind them may be retuned, so a client should order
by it rather than threshold on it.

Typing a code into `q` above matches it exactly, by similarity/full-text on everything
else - but that is a *search*, not a stable, citable URL for one code. The next section
is the addressable form FR-17 requires.

### Exact-code lookup (FR-17)

```http
GET /api/v1/catalogue/code/sct/49466006
GET /api/v1/catalogue/lookup?system=http://snomed.info/sct&code=49466006
```

Both forms resolve the same entry `GET /catalogue/entries/{business_key}` does for that
entry, and serve the identical `EntryDetail` body - never a redirect, never a thinner
shape. `sct` is a short alias for `http://snomed.info/sct`, registered in
`nptc.catalogue.code_systems.SYSTEM_TOKENS` - a frozen mapping in code, not a database
table (the same "permissions as code" reasoning [ADR-0019](../adr/0019-permission-framework.md)
applies to the auth matrix). `/catalogue/lookup` is for a caller holding the full system
URI rather than the short alias; it accepts the identical set of URIs the alias registry
recognises. See [ADR-0033](../adr/0033-exact-code-lookup-routes.md) for the full design
and the alternatives rejected.

**A retired code still resolves** (FR-08), the same as it does through
`/catalogue/entries/{business_key}/bindings`: the matched binding in the response carries
`status: "retired"`, its `retirement_reason`, and - where PRD FR-08's replacement case
applies - `replaced_by_code`. Because a binding is retired and replaced rather than
rebound in place, more than one entry can hold the same code as a *retired* binding (an
active binding is still unambiguous - the database itself enforces at most one). When
that happens, the most recently retired binding's entry wins, deterministically.

**One 404, for two different causes.** A `system_token` (or, on `/lookup`, a system URI)
that is not registered, and a registered one that matches no published entry's code, both
return the identical fixed sentence naming each registered system as both its token and
its URI (a `/lookup` caller supplied a URI and never saw the token form, so naming only
the token would leave that caller told about a parameter they didn't use) - a caller cannot use
response text to tell "your token is wrong" from "that code does not exist", matching the
non-disclosure rule above for a hidden `business_key`. A malformed `system_token` (one
that does not even look like a token) is a 422 instead, before any query runs - the same
treatment `business_key` gets.

### Faceted filters (FR-16)

Both collection endpoints accept `?filter.<key>=<value>`, repeated once per value.
Values within one facet are OR-ed; filters on different facets are AND-ed, so adding one
always narrows. An operator other than the default `equals` is named after the key,
separated by a colon - `?filter.assay_name:prefix=glu`, `?filter.volume_ml:range=1..5`.
At most 50 distinct values are accepted in one facet's selection (repeated parameter or
`:in` list alike, matching `limit`'s own 1-200 discipline above); more is a 422, not a
silent truncation.

```http
GET /api/v1/catalogue/search?q=glucose&filter.discipline=Chemistry&filter.specimen=119297000
```

`GET /catalogue/search` also returns the facets themselves, with counts.
`GET /catalogue/entries` returns them when asked: add `facets=true`, and the response
carries the same `facets` array, counted over every published entry the filters leave
rather than over the page. Without `facets=true`, the field is `null` and no counts are
computed. A client that pages sends `facets=true` once, with `limit=1`, and reads the
counts from that:

```jsonc
{
  "items": [ /* ... */ ],
  "next_cursor": null,
  "facets": [
    {
      "key": "discipline",
      "label": "Discipline",
      "facetable": true,
      "truncated": false,
      "buckets": [
        { "value": "Chemistry", "label": "Chemistry", "count": 42 },
        { "value": "Haematology", "label": "Haematology", "count": 17 }
      ]
    }
  ]
}
```

Four things a client must build for, none of them optional:

- **The facet list is not fixed.** It is derived from the property registry on every
  request - an administrator marking a property filterable makes it appear with no
  deployment and no restart (FR-09/FR-16). Render whatever you are given; do not hard-code
  the facets you know about today.
- **A count is over the whole result set, not this page**, and a facet's own selection is
  excluded from its own counts, so a bucket you have not chosen still tells you how many
  entries it would give you. An entry holding several values of one property counts once
  under each of them.
- **`facetable: false` means "filterable, but there is nothing to group"** - a continuous
  numeric property, where every value would be its own bucket. Such a facet is reported
  with no buckets rather than omitted, so you can tell it apart from one whose values
  match nothing.
- **`truncated: true` means the cap bit.** At most 20 buckets are returned, most common
  first. There is no way to page through the rest; narrow the search. The `discipline`
  and `specimen` facets are the exception: they return every value, in the same order, and
  `truncated` is always `false`. A client that builds a type-to-narrow list from them can
  rely on that.

A `filter.` parameter this API cannot use is a **422**, never a silently ignored
parameter: an unknown key, a property that is not filterable, an operator the property
does not support, a value of the wrong kind, or more values in one selection than that
facet's limit allows. On `/catalogue/search` the `next_cursor`
is bound to the filter set as well as to `q`, so replaying it with the filters changed is
also a 422 - a relevance score means nothing against a different request. On
`/catalogue/entries` the cursor is a business key and is unaffected by the filters.

`/catalogue/entries` accepts the same filters, and `facets=true` does not soften them: a
filter it cannot use is still a 422.
See [ADR-0032](../adr/0032-faceted-filter-query-surface.md).

### Change history (FR-19)

```http
GET /api/v1/catalogue/entries/NPTC-000247/history
Authorization: Bearer <token>
```

```jsonc
{
  "items": [
    {
      "occurred_at": "2026-08-14T03:12:47.512Z",
      "action": "catalogue_entry.updated",
      "changed_by": "J. Reviewer",
      "changed_fields": ["preferred_term"],
      "note": "renamed per RCPA-QAP review",
      "release": null
    }
  ],
  "next_cursor": null
}
```

Every entry that has ever been edited has a history - not only the entry's own row, but
its designations, code bindings and property values too, so retiring a synonym or
rebinding a code shows up here exactly as changing the preferred term does. Most recent
first; keyset-paged like every other collection above, but on the audit log's own
`sequence` rather than `business_key` - a globally monotonic counter, so paging can never
skip or repeat an event even while the catalogue is being edited concurrently. `before`
is the previous page's `next_cursor`, passed back unmodified; an entry never edited since
being seeded returns `200` with an empty `items` list, never an error.

**Only field *names* are ever served, never the values that changed.** `changed_fields`
tells you *that* `preferred_term` changed, not what it changed from or to - the raw diff
`audit_event` records internally is never serialised here, whether or not the field is
one this API otherwise publishes elsewhere. `note` is the changelog note (FR-37) the
administrator supplied for that write, verbatim.

**`changed_by` needs a credential (PR #278 review, NFR-26).** This endpoint itself has no
`Authorization` requirement - the example above sends one only because `changed_by` does.
An anonymous request gets `200` with every other field populated and `changed_by: null`
on every event, the same value it would show for a system-initiated change or a
pseudonymised account - the three are indistinguishable to an anonymous caller by design.
Sign in (any role) to see who made a change; `changed_by` is always the administrator's
display name, never their internal id.

**`release` is always `null` today.** FR-19 asks for "every published release in which
[the entry] appeared" as well as what changed - releases do not exist until P4, so this
is a defined slot rather than a field dropped from the shape and added back later. A
client should render its absence (a `null`) rather than assume the field will never be
populated.

## Errors

Every refusal is `{"detail": "<one sentence>"}`, and a 429 adds one field, `bulk_artefacts`. Detail strings are fixed, client-facing
sentences: they never name a role, a permission, an internal identifier, or echo back
user-supplied text (FR-44, NFR-04, NFR-26).

| Status | When |
|---|---|
| 401 | A credential was presented and could not be verified. Sending none is not an error. |
| 404 | No published entry has this business key - including one that exists but is not published. On `/catalogue/code/{system_token}/{code}` and `/catalogue/lookup`, the identical fixed sentence also covers an unregistered `system_token`/`system` (see "Exact-code lookup" above). Not produced by `/catalogue/entries` or `/catalogue/search`: an unmatched query is an empty page, not a missing resource. |
| 422 | A malformed `business_key` or `system_token`, a blank `q`/`system`/`code`, a cursor this API did not issue (including one issued for a different `q`), or a `limit` out of range. |
| 429 | An anonymous caller exceeded the per-address request budget, or a caller kept sending credentials the API rejected. Carries `Retry-After` (whole seconds) and a body naming the bulk release artefacts. See [Rate limiting and caching](#rate-limiting-and-caching). A valid credential is never counted. |

Every status each endpoint can produce is declared in `docs/api/openapi.json`, and only
the ones it can actually produce - so a generated client (#147) has no branch for a
response that never arrives.

**A published code binding's `fsn` is never re-derived (issue #144, FR-98).** A binding's
`fsn` is served exactly as stored (FR-82), and `label_provenance` declares what it is:
`label_provenance.fsn` is `{"designation": "fsn", "semantic_tag": "intact"}` by default
(`NPTC_FSN_SEMANTIC_TAG`, see `docs/operations/configuration.md`).

**A list row's own `fsn` is stripped, and can fail to render (FR-83, FR-98).** Every public
list row and search hit carries `fsn`: the active code's FSN with its
final parenthesised group removed, or `null` when the entry has no active code. Its
`label_provenance.fsn` is always `{"designation": "fsn", "semantic_tag": "stripped"}`. The
strip is `nptc.exports.semantic_tag.render_display_term`, the renderer FR-83 names. It
refuses a stored FSN with no trailing group, because that value may already have been
stripped. A catalogue seeded before the transform seeded served FSNs holds such values, so
a refusal means stored data needs repair. It is a server-side fault, not a bad request: the
request fails with a `500` and a fixed sentence, and the cause is logged at error level. A stored value is never shown unstripped to avoid the failure. The detail and the admin
listing carry neither `fsn` nor `specimens`, so an entry with such an FSN still opens and can
be repaired. The same row's
`specimens` carry each specimen's stored display with a trailing "specimen" word removed and
repeats dropped, declared as AU preferred terms.

## Rate limiting and caching

There are no `Cache-Control` or `ETag` headers. Clients should not poll tighter than they need
to.

### Anonymous per-IP limit (FR-22, NFR-24)

NFR-24 asks for rate limiting at three layers. This is the first: a request budget for each
anonymous client address. The other two are not built. They are a per-user limit on
authenticated actions, and the domain-level submission quotas of FR-43 (`QUOTAS` in
`nptc.auth.permissions` holds the numbers, and ADR-0019 says that exceeding one is a 429 with
its own audit story). The three layers are independent. This one never reads a user record, and
neither of the others should read this one's counters.

**Behaviour.**

- Each client address may make `NPTC_ANON_RATE_LIMIT_REQUESTS` requests in each
  `NPTC_ANON_RATE_LIMIT_WINDOW_SECONDS` window (600 and 60 by default). The window opens at the
  address's first counted request and closes at a fixed time after it. A refused request does not
  extend it.
- A request over the budget gets `429` with a `Retry-After` header. The header is the whole
  number of seconds until the window closes, rounded up and never below 1, so a client that waits
  that long is served. The body is
  `{"detail": "<one sentence>", "bulk_artefacts": "<url>"}`, and `bulk_artefacts` is
  `NPTC_BULK_ARTEFACTS_URL`. Like every other refusal, the body never repeats anything from the
  request.
- The 429 is declared on every operation in `docs/api/openapi.json` with its `Retry-After`
  header, and `Retry-After` is in the CORS `expose_headers`, so a browser calling cross-origin
  can read it.

**Two budgets per address.** Both use the same limit and window, and each is counted per client
address.

- **The anonymous budget.** A request with no `Authorization` header spends it: the same test the
  API uses to decide a caller is anonymous.
- **The rejected-credential budget.** A request that carries an `Authorization` header spends
  nothing, unless the route answers 401. A rejected token is not free to the server, because
  `current_principal` opens a database session before it refuses one. Each 401 therefore spends
  this second budget, and once an address has used it up, every request from that address that
  carries an `Authorization` header gets a 429 until the window closes. Routes that never check a
  token (`/docs`, `/openapi.json`) answer 200 and spend nothing, so a header sent to them costs
  the caller no more than an anonymous request would.

The limiter verifies no token, and the two budgets are separate on purpose. A valid credential
is never counted, and anonymous traffic never refuses a signed-in user. The price is that it
cannot tell a good credential from a bad one before the route runs, so a shared address (an
office network) whose rejected-credential budget is used up refuses its signed-in users too,
until the window closes. Expired tokens that a client keeps sending spend that budget too.

**The healthcheck.** The compose healthcheck probes `/api/v1/openapi.json` from loopback, and a
refused probe would mark a healthy container unhealthy. That one path from a loopback address is
not limited. Every other request from loopback is, so an API behind a proxy on the same host
with no `NPTC_TRUSTED_PROXIES` shows every caller as `127.0.0.1` and they share one budget. A
preflight (`OPTIONS`) request is answered by the CORS layer before the limiter sees it.

**Where it runs.** `nptc.api.rate_limit.AnonymousRateLimitMiddleware` sits inside the CORS layer
and outside routing. A refused request therefore opens no database session, verifies no token,
and still carries the CORS headers. It also covers `/docs` and `/openapi.json`, which are
anonymous too.

**Which address.** `nptc.api.client_ip` decides it. Behind Caddy the connecting address is
Caddy's, so the API reads `X-Forwarded-For` when, and only when, the connecting address is in
`NPTC_TRUSTED_PROXIES`. It reads the header from the right and stops at the first address that
is not trusted, so an address a caller writes at the left of the header is never believed. A
header sent as several lines is read as one list, and a hop with a port (`203.0.113.7:51234`,
`[2001:db8::1]:443`) is read without it. A connecting address that is not an IP (a unix socket,
or a test client) shares one budget rather than escaping the limit. Every address in one IPv6 `/64` shares a budget. The same address goes
into the audit log's actor address.

**Limits of the design.**

- Counters are in the API process's memory. They reset on a restart, and a second worker or
  replica keeps its own, so the budget a caller sees is the limit times the number of processes.
  The compose stack runs one process. A deployment that scales out should either accept that or
  enforce the limit at the proxy as well. A shared counter in Postgres was considered and set
  aside for now: it would cost a database write on every anonymous request, and the platform has
  no Redis (ADR-0001).
- A caller behind a shared address, such as an office network, shares a budget with everyone
  there. The defaults are generous for that reason.
- Where Docker's userland proxy handles the connection (Docker Desktop and rootless Docker do),
  Caddy sees the Docker gateway address, such as `172.18.0.1`, as every visitor's address. That
  address is inside the trusted ranges, so the API resolves it as the caller, and every visitor
  shares one budget and the audit log records the gateway. This is a limit of the platform
  Docker runs on, not of `NPTC_TRUSTED_PROXIES`. For local use, raise the limit; for a real
  deployment, use a host setup that preserves source addresses.

### Bulk release artefacts

FR-22 says the refusal must direct heavy consumers to the bulk release artefacts, which are the
published release files of FR-21. No release exists to serve until P4, so
`NPTC_BULK_ARTEFACTS_URL` defaults to this section. When releases are published, set it to
their location. Until then, a consumer who needs the whole catalogue can page
`GET /catalogue/entries` within the limit, or ask the maintainers for an export.

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

### Query hooks for the public catalogue routes

Each public read route has one hook in `frontend/src/api/queries.ts`, so a screen never
writes fetch code of its own:

| Hook | Route |
|---|---|
| `useEntriesList` | `GET /catalogue/entries` |
| `useCatalogueSearch` | `GET /catalogue/search` |
| `useEntryDetail` | `GET /catalogue/entries/{business_key}` |
| `useEntryDesignations` | `GET /catalogue/entries/{business_key}/designations` |
| `useEntryBindings` | `GET /catalogue/entries/{business_key}/bindings` |
| `useEntryProperties` | `GET /catalogue/entries/{business_key}/properties` |
| `useEntryHistory` | `GET /catalogue/entries/{business_key}/history` |
| `useEntryByCode` | `GET /catalogue/code/{system_token}/{code}` |
| `useEntryBySystemCode` | `GET /catalogue/lookup` |

- **Paging is keyset only** (ADR-0024). The list and search hooks take `after`, the
  history hook takes `before`, and each passes the previous page's `next_cursor` through
  unchanged. No hook has a page number, an offset or a total. Search puts `q`, `after`
  and the filters in one query key, because the server binds a cursor to its `q` and
  filters and answers a mismatch with a 422.
- **Facet filters are repeated `filter.<key>` pairs.** The generated type has only the
  literal field `"filter.{property_key}"`, because OpenAPI cannot describe a parameter
  name that varies. The list and search hooks therefore take `filters:
  Record<string, string[]>`, build the wire pairs with `filterQueryParams`
  (`frontend/src/api/filter-params.ts`), and cast the result through the operation's own
  query type. The admin hooks use the same approach. This closes the gap that issue #276
  recorded.
- **Codes stay strings** (FR-06). The lookup hooks pass `code` exactly as given, so
  `0012345` reaches the server as `0012345`. They check only that the code is not empty;
  the server decides what a valid code is, and it answers an unknown and a malformed code
  with the same 404. A caller reads `ApiError.status` to tell "no such entry" from an
  outage.
- **History depends on who asks** (NFR-26). The route returns `changed_by: null` to an
  anonymous caller. `useEntryHistory` puts whether the caller is signed in into its query
  key, and waits while the session is still restoring, so an anonymous page is never
  shown to a signed-in reader.
- **The entry page reads one entry and a short history** (issue #440).
  `pages/catalogue-entry.tsx` calls `useEntryDetail` for the entry, with its designations,
  bindings and properties in the one response, and `useEntryHistory` with `limit: 5`. The
  two queries are independent, so a slow or failed history leaves the entry on screen. The
  page shows every property the route returns and does not decide which are public: the
  route serves each value of an entry's properties, including maintenance-scoped ones such
  as `usage_guidance`.
- **A 404 and a 422 both mean "no such entry" to the entry page.** The route answers an
  unknown key and a key that is not public with one identical 404. It answers a malformed
  key with a 422 whose body is the framework's own, not a declared shape. The page shows
  the not-found page for either and never reads the 422 body.
