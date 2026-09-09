# The catalogue admin API: entry read, all-status listing/search, code bindings, designations, property values and entry core columns (issues #219, #224, #228, #266, #227, #248, #249, #265)

The first state-changing HTTP routes in this platform, plus the one authenticated read
route alongside them. Everything they call already existed and was already tested as a
library - `nptc.catalogue.bindings` (issue #48), `nptc.catalogue.designations`/
`nptc.catalogue.collisions` (issues #47, #49) - so this document is about the HTTP
adapter: what it exposes, how it addresses a resource with no internal identifier on the
wire, and what it deliberately leaves for later issues.

This is not the public API [public-api.md](public-api.md) describes. It requires
authentication and a permission (`catalogue.edit_published` for most routes;
`validation.acknowledge` for one, described below), and it is not part of the FR-20
external-vendor contract - it exists for this platform's own admin screens (issue #149
onward).

## Entry read, any status (issue #228)

| Path | Method | Returns |
|---|---|---|
| `/catalogue/admin/entries/{business_key}` | `GET` | `200 EntryDetail` |

`nptc.api.routers.catalogue_admin` - a router separate from `catalogue.py` (the public
read surface) for the same reason `catalogue_bindings.py`/`catalogue_designations.py`
stay apart from it: `test_api_public_status_filter.py`/`test_api_public_response_
hygiene.py` both derive what they scan from `catalogue.py`'s own route table, and this
route is deliberately not part of that table.

Every catalogue entry is born `draft` (`create_entry`'s own default status), and the
write routes below already resolve a `draft` entry fine (`load_entry_for_update` carries
no status filter). What was missing was a read route an edit screen (#149) could call
first to render the form: [public-api.md](public-api.md)'s `GET /catalogue/entries/
{business_key}` only ever serves `active` - a `draft` 404s identically to a
`business_key` that was never minted, which is FR-20's own deliberate contract, not a
gap to close there. This route is the authenticated counterpart, at its own path rather
than a permission-gated branch of the public one: one URL per audience, so a reviewer
can permission-audit each route independently of who is asking.

Gated on `Permission.CATALOGUE_EDIT_PUBLISHED` - the same permission the write routes
below require, on the reasoning that the audience for "load an entry to edit it" and
"save an edit to it" is the same audience, so it needs one credential posture, not two.
Serves the identical `EntryDetail` shape `catalogue.py`'s own detail route does,
assembled by the same three loaders (`queries.load_designations`, `queries.
load_bindings`, `queries.load_property_values`); `EntryDetail` and its assembly helpers
live in `catalogue_shared.py` so both routers stay byte-for-byte in agreement on the
shape.

This is also where an edit screen reads FR-38's `EntryDetail.row_version` (issue #227) -
the token `/amendment` requires before it will save the entry's own preferred term. See
"`expected_row_version`" below, and [public-api.md](public-api.md) for why the field is
on `EntryDetail` rather than `EntrySummary`.

### Errors (entry read)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 404 | No catalogue entry, of any status, has this `business_key`. Deliberately the same generic body the public route's 404 carries (they share the same `EntryNotFoundError` handler) - this route exists so an authenticated caller can see a `draft`, not so it can distinguish "never minted" from "exists but hidden". |
| 422 | The business key is not `NPTC-nnnnnn`. |
| 500 | A published code binding's stored FSN is not in the form the terminology server serves (FR-83), same as the public detail route's own 500. |

## All-status listing and search (issue #266)

| Path | Method | Returns |
|---|---|---|
| `/catalogue/admin/entries` | `GET` | `200 AdminEntryPage` |
| `/catalogue/admin/search` | `GET` | `200 AdminSearchPage` |

The collection counterpart to "Entry read, any status" above, and split out of the same
gap: an edit screen needs a route to *find* a draft, deprecated or withdrawn entry before
it can load and save one. [public-api.md](public-api.md#what-is-published-and-what-is-not)'s
`GET /catalogue/entries` and `GET /catalogue/search` only ever serve `PUBLIC_STATUSES`
(`active`), so these two routes are the maintenance-scoped counterparts, on the same
`catalogue-admin`-tagged router and the same permission.

Parameters, paging, ranking and the `filter.*` query surface are otherwise identical to
their public counterparts - see [public-api.md](public-api.md#pagination) and
[search.md](search.md#maintenance-search-issue-266) - with two differences:

- **Every `CatalogueEntryStatus` is in scope**, not `active` alone.
  `nptc.catalogue.maintenance.MAINTENANCE_STATUSES` is derived from the enum rather than
  hand-listed, so a fifth status is covered the day it is added.
  `nptc.catalogue.queries`'s own rule that `PUBLIC_STATUSES` is the *only* status filter
  that module applies is why the listing query lives in a new `maintenance.py` instead of
  a `statuses=` parameter bolted onto `queries.list_entries`.
- **`status` is meaningful to filter on.** `?filter.status=draft` is a 422 on the public
  search (a status it can never show is refused, not silently emptied); here it is a real,
  narrowing filter, and the search response's own `status` facet has a bucket per status
  actually present rather than the public route's single `active` bucket.

`status` is present on every row of both responses (`AdminEntryPage.items[].status`,
`AdminSearchPage.items[].status`) - already true of `EntrySummary` generally
([public-api.md](public-api.md)), stated here because it is this issue's own acceptance
criterion: a caller has to be able to tell a draft from an active entry without a second
call.

Gated on `Permission.CATALOGUE_EDIT_PUBLISHED`, the same permission as the entry-read
route above and the write routes below - no new permission was minted, for the identical
reason "Entry read, any status" gives for its own gate.

**Rows also carry `row_version` (issue #267).** `AdminEntrySummary`/`AdminSearchHit` -
`EntrySummary`/`SearchHit` plus FR-38's optimistic-locking token - are what these two
routes actually return, not the public shapes: `AdminEntryPage.items: [AdminEntrySummary]`,
`AdminSearchPage.items: [AdminSearchHit]`. Defined in `catalogue_admin.py`, not
`catalogue_shared.py` (which the public router also imports), so the public
`/catalogue/entries`/`/catalogue/search` stay byte-identical by construction -
`test_api_public_response_hygiene.py` asserts they still omit the field. The admin
catalogue list screen's row-selection surface is the reason: it locks the bulk
property-value write route below on `(business_key, expected_row_version)`, and a list is
where that token has to come from without a second read per selected row.

### Errors (all-status listing and search)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 422 | A blank search query, a cursor this API did not issue (including one issued for a different `q` or filter set), a `limit` outside 1-200, or a `filter.*` parameter naming a facet this endpoint does not offer, an operator the facet does not support, or a value it cannot hold. |

No 404: neither route can produce one - an unmatched query or filter is an empty page,
not a missing resource, matching the public collection routes' own contract.

## Code bindings

All under `/api/v1/catalogue`, same path space as the public read routes.

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/{business_key}/bindings` | `POST` | `{code, fsn, au_preferred_term?, edition_hint?, reason, expected_row_version}` | `201 BindingWriteResult {binding, row_version}` |
| `/entries/{business_key}/bindings/{code}/retirement` | `POST` | `{reason, expected_row_version}` | `200 BindingWriteResult {binding, row_version}` |
| `/entries/{business_key}/bindings/{code}/replacement` | `POST` | `{successor: {code, fsn, au_preferred_term?, edition_hint?}, reason, expected_row_version}` | `200 BindingReplacementResult {items, row_version}` (both rows) |

`business_key` accepts any status, not only `active` - an editing surface has to reach a
`draft` entry before it can ever become `active`. `nptc.catalogue.entries.
load_entry_for_update` is the loader, shared with `save_entry`/`save_entries` rather than
re-querying `CatalogueEntry` by hand. Pinned by an explicit test for every status
(`draft`/`active`/`deprecated`/`withdrawn`), not just exercised incidentally.

The `201` from `POST /bindings` carries a `Location` header pointing at
`GET /entries/{business_key}` - not a URL for the binding on its own, since nothing below
`/bindings/{code}` serves a `GET` (the two routes there are both `POST` sub-resources). A
client wanting the binding it just created already has it in the response body; `Location`
exists so the response also names the resource it changed, and names one that actually
resolves.

### Addressing a binding: by `code`, never an id

The public `Binding` model deliberately carries no `id` or `entry_id` -
[public-api.md](public-api.md#what-is-published-and-what-is-not) explains why. A client
retiring or replacing a binding therefore addresses it by the SNOMED CT code it was
bound with, not a row id it was never given.

`nptc.catalogue.bindings.load_active_binding(session, entry_id=..., code=...)` resolves
this: `ix_code_binding_one_active_per_entry` guarantees at most one **active** binding
exists per entry, so scoping the lookup to `(entry_id, code, status='active')` returns at
most one row. A code that has already been retired, or was never bound, is a `404`
(`CodeBindingNotFoundError`) - not a `409` - because it is simply not addressable this way
any more, not a conflicting state. One consequence worth stating plainly: retiring an
already-retired code is a `404`, not the `409` `CodeBindingAlreadyRetiredError` the
service layer itself raises for an already-loaded binding - this router never reaches
that branch, because it only ever loads a binding that is still active.

Re-reading a binding this router just wrote (to build the response) is *not* done by
`code`, for the same reason: `(entry_id, code)` is unique only among active rows, so a
code bound, retired, and bound again would leave two retired rows sharing that code, and
a code-keyed re-read could resolve to either. `_row_to_binding` keys on the just-written
row's own `id` instead - an internal detail that never itself reaches a response.

`system` is not exposed on the wire at all. Every route defaults to `SNOMED_CT_SYSTEM`,
the only system in use today; the model and the unique index already key on
`(system, code)`, so exposing a second system later is additive, not a breaking change.

### Replacement is one request, not three

`nptc.catalogue.bindings`' own module docstring explains why replacing a binding is a
three-step sequence at the service layer - retire the predecessor, create the successor,
link them - rather than one function:
`ix_code_binding_one_active_per_entry` forbids a successor existing active while its
predecessor still is, so no other order is valid.

Exposing that as three separate HTTP calls would let a client's failed second or third
request strand an entry with no active binding and no successor recorded. The
`/replacement` route instead runs all three service-layer calls inside the one request's
transaction (`nptc.api.dependencies.get_session` / `session_scope` commits on success),
so all three audit events land together or none do.

One `reason` covers all three steps: a caller explaining *why* a code is being replaced
is explaining one editorial decision, not three, and `retire_binding`/`create_binding`/
`link_replacement` each validate the same note independently regardless.

A successor naming the same code it is meant to replace is refused up front (`409`,
`CodeBindingSelfSupersessionError`) rather than attempted: `link_replacement`'s own
self-supersession check compares row *identity*, not code, so a same-code replacement
would otherwise retire and re-bind one code in a single request and leave the response
unable to tell the two rows apart by code.

### `expected_row_version` (code bindings) - issue #60

`code_binding` carries no version of its own, so all three routes share `catalogue_entry.
row_version` as their lock - the same argument `nptc.catalogue.property_values.
save_property_values` already makes for `property_value`, via `nptc.catalogue.entries.
entry_child_write`.

**Required, not optional.** Contrast the designations route's `/amendment`, which added
the field to an endpoint with shipped clients and so left it optional on one branch (see
below). These three routes gained the field at the same moment their only client learned
to send it, so there was no back-compat client to break - a missing field is a compile-
time TypeScript error once the generated client is regenerated, not a runtime 422.

**`replace_binding` takes the lock once, before any of its three writes**, wrapping the
retire/create/link sequence in a single `entry_child_write` - so a stale version refuses
before `retire_binding` even runs, leaving no partial replacement (the predecessor still
active, no successor row, no audit event) and not merely a refused final step. The
self-supersession check runs first, ahead of the lock, since it needs no entry state.

**Admin-only result models, not the public `Binding`/`BindingList`.** `BindingWriteResult`
and `BindingReplacementResult` are declared in `catalogue_bindings.py`, mirroring
`catalogue_properties.PropertyValuesWriteResult` - widening the public models with an
admin-only `row_version` field would break `test_api_public_response_hygiene.py`, which
derives its assertions from `catalogue.py`'s own route table.

### Authorisation (code bindings)

Every route requires `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44) - held only by
`Role.ADMINISTRATOR`, and therefore in `MFA_REQUIRED_PERMISSIONS` (NFR-06). An
administrator who has not completed the MFA step-up gets the RFC 9470 challenge
(`WWW-Authenticate: Bearer error="insufficient_user_authentication", acr_values="2"`),
the same as any other MFA-gated permission - see
[permissions.md](permissions.md).

### Errors (code bindings)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 404 | No catalogue entry with this `business_key`, or no *active* code binding for this `code`. |
| 409 | A second active binding on this entry (FR-08), this code already actively bound to a different entry (issue #49's blocking severity) - including two concurrent requests racing for the same entry or code, see below - `/replacement`'s successor naming the same code it is meant to replace, or a stale `expected_row_version` (FR-38, issue #60; `VersionConflictResponse`, see below). |
| 422 | A malformed or Verhoeff-failing SCTID, an unrecognised edition hint, a blank `fsn`/`au_preferred_term`, a changelog note that fails FR-37's validation, or a missing `expected_row_version`. |
| 500 | A platform-side invariant failed - not a caller mistake, and not produced by anything a well-formed request can trigger on its own. |

**Concurrency (updated by issue #60).** `create_binding`'s active-binding checks are
read-then-write, so two concurrent binds racing for the same entry or the same code both
pass the pre-check and only one wins at insert - `ix_code_binding_one_active_per_entry`/
`ix_code_binding_one_active_entry_per_code` are what actually decide. The loser's
`IntegrityError` is translated to the same `409` domain error the pre-check would have
raised, via `nptc.db.errors.unique_violation_constraint` - the same constraint-name unwrap
`nptc.auth.identity._is_username_collision` already needed, pulled out to one shared
helper rather than a second copy - so a lost race still reads as a normal conflict, not a
500. Forced deterministically (an `after_cursor_execute` hook supplying the ordering plain
sequential test code cannot express) in
`test_a_lost_concurrent_code_race_is_a_domain_error_not_a_raw_integrityerror` - this test
still passes unchanged after #60, because it races two *distinct* codes/entries where
`entry_child_write`'s version check never fires (neither caller's `expected_row_version`
is stale relative to the other's write) and the race is still decided at the index.

`load_entry_for_update` itself still takes no row lock - see its own docstring - but
`entry_child_write` (FR-38, issue #60) introduces the *first* `catalogue_entry` row lock
these three routes take, via the ordinary `UPDATE ... WHERE row_version = ...` `version_
id_col` produces when its own internal flush bumps the count - inside a `session.
begin_nested()` savepoint, with the `StaleDataError` that flush can raise translated into
the same `409` version conflict rather than escaping uncaught as a 500 (issue #60 review;
see `entry_child_write`'s own docstring for the two-layer shape this relies on). Two
concurrent binding writes against the *same* entry that do **not** collide on either
partial unique index - different codes, or a bind racing a retire - now serialise on that
row: the loser's flush blocks until the winner commits, then sees a stale `expected_row_
version` and gets a `409` version conflict, because there was no index collision to race
on in the first place. Two writers racing for the *same* code are unchanged by this issue:
`create_binding`'s own `append_audit_event` flushes its `INSERT` before `entry_child_write`
ever bumps or re-flushes, so that race is still decided at the partial unique index exactly
as before, and the loser still sees the translated `IntegrityError`-derived domain error
above, not a version conflict. The index-level race is otherwise unchanged and still
applies wherever `entry_child_write` cannot see it at all - two *different* entries, or two
callers each holding a version that was still current when they read it.

Every `CodeBinding*` exception from `nptc.catalogue.bindings` is mapped in
`nptc.api.errors` by the same convention every other handler in that module follows:
read `exc.http_status`, never echo `str(exc)` into the response body (an exception
message may name an internal id, for the log only - NFR-04/NFR-26), and log at `INFO`
for a routine, expected refusal.

## Designations

All under `/api/v1/catalogue`, in `nptc.api.routers.catalogue_designations` - a router
separate from both `catalogue.py` (the public read surface) and `catalogue_bindings.py`,
for the same reason those two stay apart from each other.

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/{business_key}/designations` | `POST` | `{terms: [string], use?, language?, reason, expected_row_version}` | `201 {designations: [Designation], warnings: [CollisionWarning], row_version}` |
| `/entries/{business_key}/designations/amendment` | `POST` | `{term, new_term, language?, use?, expected_row_version, reason}` | `200 {designation: Designation, warnings: [CollisionWarning], row_version}` |
| `/entries/{business_key}/designations/retirement` | `POST` | `{term, language?, reason, expected_row_version}` | `200 {designation: Designation, row_version}` |
| `/entries/{business_key}/designations/acknowledgement` | `POST` | `{term, language?, reason}` | `200 {language, reason}` |

`business_key` accepts any status, the same as the code binding routes, via the same
`load_entry_for_update` loader.

### Addressing a designation: by term in the body, never a path segment or an id

The public `Designation` model carries no `id` (NFR-04/NFR-26, the same rule
`Binding` follows) - but unlike a SNOMED CT code, a term is free text an editor typed,
and can contain a `/` (`"CD4/CD8 ratio"`). FastAPI decodes a path segment before
routing, so a term with a slash in a `{term}` path parameter would either 404 against
the wrong route or need a client-side double-encoding scheme nobody should have to
reason about. Every route above therefore takes its target term in the request body.

`nptc.catalogue.designations.load_active_designation(session, entry_id=..., term=...,
language=...)` resolves it, mirroring `load_active_binding`: looked up by *comparison
key* (`nptc_shared.similarity.collision_key` over the cleaned term), not the raw
string, since `ix_designation_no_duplicate_active_term` is itself keyed on `term_key` -
a caller naming a case or punctuation variant of the stored term still resolves the
same row. `use` is deliberately not part of the address: that index has no `use`
column, so `(entry_id, term_key, language)` already identifies at most one active row.
A term already retired, or never added, is a `404` - not addressable this way any
more, not a conflicting state - matching code bindings' own `404`-not-`409` reasoning
for a retired code.

Re-reading a just-written row (to build the response) is by the row's own `id`, not by
term, for the same reason `_row_to_binding` avoids a code-keyed re-read: `(entry_id,
term_key, language)` is unique only among *active* rows, so a term retired and re-added
would leave two retired rows sharing a `term_key`, and only `id` still tells them apart.
`nptc.catalogue.queries.load_designations_for_write` is the retired-inclusive loader
this needs - `load_designations` (the FR-20 public read path) stays active-only.

### Editing in place, not retire-and-re-add

`amend_designation` mutates `designation.term` directly rather than retiring the old
row and creating a new one. The row keeps its identity (`id`), and the audit log shows
one `designation.amended` edit rather than a retirement paired with an
unrelated-looking creation - the same "one editorial decision, one audit trail" posture
`/replacement`'s single request takes for code bindings.

### `/amendment` writes to two storage homes (issue #227)

ADR-0022 keeps the catalogue's own en-AU preferred term on
`catalogue_entry.preferred_term`, never a `designation` row
(`ck_designation_no_en_au_preferred`). Rather than expose that split as a second
endpoint, `/amendment` resolves `term` against both: an active `designation` row if
there is one, otherwise the entry's own preferred term, saved through
`nptc.catalogue.entries.save_entry`. Every term the catalogue holds is a designation as
far as this API is concerned - one route, one mental model, two storage homes - and the
preferred-term branch returns its result shaped as a `Designation`
(`use: "preferred"`, `language: "en-AU"`, with FR-85's computed `length`), so a client
never has to model where a term happens to live.

**Rejected: a dedicated `POST .../preferred-term` route.** Its request and response
would be honest about the split - no conditionally-required field, no dispatch - but it
pushes ADR-0022's storage decision onto every client, and onto the edit screen most of
all: #149 renders one list of terms and would have to route each edit by which table
the platform happens to keep it in. That is the coupling this API exists to hide.

**Designation-first, and the order is load-bearing.** Nothing forbids an entry from
carrying an active en-AU synonym whose `term_key` equals its own `preferred_term_key`:
`ix_designation_no_duplicate_active_term` is designation-vs-designation only, and
`assert_no_error_collisions` compares against *other* live entries. Resolving the
preferred term first would therefore make such a synonym unreachable for editing -
silently changing what a route shipped in #224 does. Taking the designation first means
the new branch only ever claims what this route already 404s on.

**`use` says which one you meant, when the term alone cannot.** Designation-first is the
right default, but on its own it leaves the mirror-image problem: once a shadowing
synonym exists - and `POST .../designations` will create one - the entry's preferred term
becomes permanently unreachable, and a caller asking for it silently moves the synonym
instead. For #149's screen, which renders both in one list, that is an ambiguous click
with a silent wrong outcome. The optional `use` on the request resolves it:

| `use` | `language` | Resolves to |
|---|---|---|
| unset | any | An active `designation` row; the entry's own preferred term only if there is none and `term` names it. |
| `preferred` | `en-AU` | The entry's own preferred term, if `term` names it. No designation lookup runs - ADR-0022 guarantees there is no such row, and skipping it is what reaches past a shadowing synonym. |
| `preferred` | anything else | A `designation` row. A non-en-AU preferred variant is a real row, and `ck_designation_no_en_au_preferred` is what keeps the two unambiguous. |
| `synonym` | any | A `designation` row, never the entry. A term that is only the preferred term is a 404. |

**`use` narrows which storage home to look in; it never excuses the caller from naming
the term.** `term` is required, and its job on this route is to address the thing being
edited, so `use="preferred"` with a term that is not the preferred term is a 404 rather
than a rename - the same silent-wrong-target class `use` exists to close. This costs the
escape hatch nothing: a shadowing synonym folds to the *same* comparison key as the
preferred term by definition, so a caller reaching past one always names a matching term
anyway.

Addressing folds the same way on both branches: `preferred_term_key` is written by
`CatalogueEntry`'s own `@validates` hook from the same `collision_key(clean_term(...))`
composition `load_active_designation` looks a designation up by, so a caller naming a
case or punctuation variant resolves either one.

### `expected_row_version`: required on every route, and every route bumps it

`catalogue_entry` is a row with FR-38 optimistic locking, so a write to it cannot be
accepted without the caller's version. `designation` has no version of its own, so all
three routes here (`/designations`, `/amendment`'s designation branch, `/retirement`)
share `catalogue_entry.row_version` as their lock, the same argument
`nptc.catalogue.property_values.save_property_values` already makes for `property_value`
and `catalogue_bindings.py` makes for `code_binding` (issue #60). `expected_row_version`
is required on every route below - no exceptions, no optional branch (issue #300).

`/amendment`'s two branches take the lock through two different mechanisms, since one
writes `catalogue_entry` directly and the other does not:

| `term` resolves to | Lock taken via |
|---|---|
| the entry's own preferred term | `nptc.catalogue.entries.save_entry`, which already required and bumped the version before issue #300 - unchanged by it. |
| an active `designation` row | `nptc.catalogue.entries.entry_child_write`, the same helper `/designations` and `/retirement` use - new in issue #300. |

Required outright everywhere, not left optional on `/amendment`'s designation branch the
way issue #227 originally shipped it: bumping the version on a write that let the field
stay optional would silently invalidate every other editor's still-current token the
moment a caller that omits it saves, which is worse than the concurrency gap it would
close. This is a breaking change to all three routes' request bodies (and to
`/retirement`'s response shape, below) for exactly that reason.

Callers read the current version from `EntryDetail.row_version` (issue #227 put it
there; `EntrySummary` deliberately does not carry it - see
[public-api.md](public-api.md)), and get the new one back on every write response, so a
save never has to be followed by a re-read. It now advances on every successful write to
any of the three routes, including the designation branch of `/amendment`.

A stale version is a 409 carrying `business_key`, `expected_row_version`,
`current_row_version`, `conflicts[]` (each `field`/`submitted`/`current`) and
`changed_by`/`changed_at` - FR-38's rationale is explicit that the caller must be able
to reconcile rather than retry blind. `conflicts` is empty on every route here except
`/amendment`'s preferred-term branch: none of the others declare an entry-level change,
which is `ConflictReport`'s documented non-overlapping-field case - still refused
because the version is the contract regardless.

This closes the concurrency gap "What these issues do not cover" used to name below:
two administrators editing different terms on one entry, by any combination of add,
amend and retire, now conflict with each other.

### Warning-severity collisions ride back on the write response

`nptc.catalogue.collisions.warning_collisions` never raises - a warning permits the
save by construction (FR-05). `add_designations`/`amend_designation` call it after
their own write and return whatever it finds as `warnings` on the same response,
rather than exposing it as a separate `GET` endpoint under `/catalogue` that
`test_api_public_response_hygiene.py`'s GET scanner would otherwise discover and
attempt to exercise without a credential.

### Acknowledging a collision needs a different permission

`POST .../designations/acknowledgement` is gated on `Permission.VALIDATION_ACKNOWLEDGE`,
not `catalogue.edit_published` - held by `Role.REVIEWER` *and* `Role.ADMINISTRATOR`,
unlike the Administrator-only permission the other three routes require. It is
therefore not in `MFA_REQUIRED_PERMISSIONS`, and its `403` never carries a step-up
challenge, for either role. Acknowledgements are insert-only at the database
privilege level (`UPDATE`/`DELETE` revoked on `designation_collision_acknowledgement`),
so there is no route to withdraw one.

### Errors (designations)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing the route's required permission, or (for `catalogue.edit_published` routes only) holding it without MFA. |
| 404 | No catalogue entry with this `business_key`, or a `term` that is neither an *active* designation for this `language` nor (on `/amendment`) the entry's own en-AU preferred term. |
| 409 | An error-severity collision against another live entry (FR-05, names the colliding entry's `business_key`/`preferred_term`), a duplicate active term or a second active preferred term in one language on this same entry, a designation already retired, or a concurrent acknowledgement of the same collision. On every route except `/acknowledgement`, also a stale `expected_row_version` (FR-38) - a richer body, see "`expected_row_version`" above. |
| 422 | An unrecognised `use`, a malformed BCP-47 language tag, a term left empty after whitespace cleaning, the catalogue's own en-AU preferred term submitted as a designation to `POST .../designations` (`ck_designation_no_en_au_preferred` - refused before the ORM, not an unmapped `IntegrityError`; amend it through `/amendment` instead), more than one preferred term in one batch, a changelog note that fails FR-37, or a missing `expected_row_version` (FastAPI's own `HTTPValidationError`, matching the code-binding routes - there is no longer a route-specific missing-token error here). |

**Two 409 bodies carry more than `detail`, and are declared as such.** Most refusals are
an `ErrorResponse` - one sentence, and deliberately nothing else. FR-05's collision and
FR-38's version conflict are not, because a bare sentence withholds exactly what those
requirements exist to give the caller: the colliding entry (PRD §17.2 item 5), and the
conflicting values to reconcile against. Both are declared response models
(`DesignationCollisionResponse`, `VersionConflictResponse` in `nptc.api.errors`) rather
than prose, `anyOf`-ed with `ErrorResponse`, so #147's generated client can read the
payload instead of typing the branch as `{detail}` and dropping it. The models are
constructed by the handlers that emit them, so the declared schema and the real body
cannot drift.

They are declared only where they can occur: `POST .../designations` and `/amendment`
call service functions that run `assert_no_error_collisions`; only `/amendment` writes an
entry. Retirement and acknowledgement can produce neither, and a documented body a route
cannot emit is a branch a generated client can never exercise.

Every exception `nptc.catalogue.designations`/`nptc.catalogue.collisions` raises is
mapped in `nptc.api.errors` the same way the `CodeBinding*` family is: read
`exc.http_status`, never echo `str(exc)`, log at `INFO`. Closing this mapping (four
constraints were previously unmapped `IntegrityError`s - see
`nptc.api.errors`'s own former "Known gap" note) is this issue's own contribution, not
inherited from #219.

## Property values (issue #248)

All under `/api/v1/catalogue`, in `nptc.api.routers.catalogue_properties` - a router
separate from `registry.py` (which owns `PropertyDefinition`, what a property *is*) for
the same reason `catalogue_bindings.py`/`catalogue_designations.py` stay apart from
`catalogue.py`: this one owns `PropertyValue`, what an entry *holds* for a property.

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/{business_key}/properties/{key}` | `PUT` | `{values: [{value, justification?}], reason, expected_row_version}` | `200 {values: [PropertyValue], row_version}` |

Everything below HTTP already existed and was already tested as a library -
`nptc.catalogue.property_values.save_property_values` (issue #52) - so this route is
purely the HTTP adapter: whole-property replace, cardinality bounds, FR-10 binding
strength, FR-11 deprecation refusal, FR-89's specimen cross-field check, FR-38's
optimistic lock and the audit event are all handled by that existing service. `key`
addresses the `property_definition` being written, not a value's own identifier - there
is no route for a single value in isolation, matching `save_property_values`'
whole-set-replace posture (see that module's own docstring for why).

The response carries the entry's new `row_version`, mirroring
`/amendment`'s `AmendDesignationResult` above - so an editing client never has to
re-fetch the entry just to learn its next lock token.

### `form_control` and `scope` on the read side (issue #248)

Two contract gaps blocked a generated form from consuming this route at all, closed on
the *read* side (`GET /registry/properties`, `nptc.api.routers.registry`) alongside the
write route above:

- **`form_control: {control, params}`** on `PropertyDefinitionResponse` - the wire form
  of each datatype handler's own `FormControlDescriptor` (ADR-0013 §3, FR-77).
  `control` is typed against the closed `ControlKind` enum, so OpenAPI emits a union a
  generated client can switch over exhaustively, and a form never has to branch on
  `datatype` (which FR-77 forbids). A datatype registered nowhere in `nptc.registry.
  datatypes.BUILTIN_DATATYPES` still appears correctly - no route or response model is
  edited per datatype - because the value comes from resolving the live
  `DatatypeRegistry`, not a hand-maintained mapping.
- **`?scope=submission|maintenance`** on `GET /registry/properties` - re-added, with a
  test, the filter issue #223 review finding 8 dropped as YAGNI. Inclusive of
  `PropertyScope.BOTH`: `?scope=submission` returns `submission` and `both` properties,
  `?scope=maintenance` returns `maintenance` and `both`, and omitting the parameter
  returns everything.

### The 422 body is a declared model, not a hand-built dict (issue #248)

`nptc.catalogue.property_values.PropertyValidationError`'s `issues[]` - naming
`property_key`, `label`, `code`, `message` and `ordinal` for each failing value - is now
`PropertyIssueItem`/`PropertyValidationResponse` in `nptc.api.errors`, constructed by the
handler rather than assembled as a raw dict. Declared in the route's `responses={422:
...}` alongside `ErrorResponse` (via a `model` union, the same `_RESPONSE_409_AMENDMENT`
mechanism designations use above) so both schemas actually land in
`components/schemas` and the generated client can read the field-level detail instead of
typing the branch as a bare `{detail}`.

### Definition status travels with a recorded value (issue #248)

`EntryDetail.properties[].status` (via `catalogue_shared.PropertyValue`) is the
*definition's* status - `active` or `deprecated` - not a fact about the value itself.
FR-11 makes a deprecated definition retain its recorded values, so without this field a
client reading an entry could not tell such a value apart from one recorded against a
property still open for writes, short of a second call to `GET /registry/properties
?include_deprecated=true`.

### Errors (property values)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 404 | No catalogue entry with this `business_key`, or no `property_definition` with this `key`. |
| 409 | A stale `expected_row_version` (FR-38) - the same `VersionConflictResponse` body `/amendment` returns above. |
| 422 | A missing or low-information `reason` (FR-37), a write against a deprecated property (FR-11), or one or more submitted values fail their property's JSON Schema, cardinality bound, or FR-89's specimen cross-field check - `issues[]` then names each failing value's `property_key`, `label` and `ordinal`. |

A rejected write leaves no partial `property_value` state and no audit event -
`save_property_values` validates the whole submitted set before it touches a row (see
that function's own docstring), the same "reject before mutating" posture `save_entry`'s
row-version check already takes.

### Bulk write across entries (issue #265, FR-39)

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/bulk/properties/{key}` | `POST` | `{values: [{value, justification?}], reason, entries: [{business_key, expected_row_version}]}` | `200 {outcomes: [BulkPropertyOutcomeItem], applied, unchanged, conflict, not_found}` |

The server half of #63's bulk reclassify - discipline, across a set of entries an
editor selected on screen, is the motivating case, but the route is generic over
`property_key` (FR-90's cardinality `0..*` and system origin apply to discipline, not to
this route). `save_property_values` (above) has no plural form of its own: it takes one
`CatalogueEntry`, not many. `nptc.catalogue.property_values.
save_property_values_for_entries` is the seam this route adapts - **not**
`nptc.catalogue.entries.save_entries`, which batches `EntryChanges`' own columns
(`preferred_term`/`status`/`specimen_unconstrained`), not `property_value` rows.

**Selection is an explicit list, not a filter.** `entries` names every target as
`(business_key, expected_row_version)` - the version each entry held when the caller
selected it, resolved client-side, never a server-side filter expression. A filter
resolved on the server has no per-entry version to lock a conflict check against, which
FR-38 requires. Capped at 100 entries (`_MAX_BULK_ENTRIES`, matching
`AddDesignationsRequest.terms`'s own cap and rationale, ADR-0017): each entry's write
holds a `pg_advisory_xact_lock` until commit, so an unbounded batch is an unbounded
amount of lock contention for one request. A repeated `business_key` is a 422 at the
request layer - a duplicate would carry the same `expected_row_version` twice, and the
second occurrence is guaranteed to conflict against a version the first one just wrote.

**Whole-set replace, applied per entry - not remove-one/add-one.** Every targeted entry
ends up holding exactly the `values` sent, identical to the singular route's own
semantics (see above), just applied across many entries in one request rather than one.
A compound value one entry already holds (`"Chemical pathology or Haematology"`) is
therefore *replaced*, not amended - see ADR-0035 for why remove-one/add-one was
rejected.

**Always 200 - the outcome list is the representation, not a status code.** Each entry
in `entries` produces exactly one `BulkPropertyOutcomeItem`, in request order, so a
client can zip its own request against the response:

| `status` | `row_version` | `conflict` | Meaning |
|---|---|---|---|
| `applied` | the new version | `null` | The write succeeded and changed something. |
| `unchanged` | the current version, unmoved | `null` | The entry already held exactly `values` - a no-op, not an error (FR-90/ADR-0018's `AuditNoOpError` never reaches this route). |
| `conflict` | the entry's *current* version | `VersionConflictResponse` | `expected_row_version` no longer matched - the same body `/properties/{key}` returns as a 409, embedded here instead. |
| `not-found` | `null` | `null` | No catalogue entry has this `business_key`. |

A batch where every entry conflicts is still a 200: the request was authorised,
well-formed, and fully processed, and a whole-request 409 would have to discard the
`row_version`s of any entries that *did* apply - the one thing a retrying client needs.
This is also why the route declares **no 409** in `responses=`: a stale version is now a
per-entry outcome, and a documented body a route can never actually emit is a branch no
generated client can exercise. For the same reason there is **no per-entry `values`
echo** - the batch wrote one set every caller already has, so echoing it back once per
entry would repeat the request N times over for no new information.

**Whole-request vs per-entry, one rule.** Anything derivable from `property_key`/
`values`/`reason` alone - with no dependency on any one entry's state - is validated
once, before any entry is touched: an unknown or deprecated property (404/FR-11, named
`key` in the route's own path segment, `property_key` in the seam it calls),
a missing or low-information `reason` (FR-37, checked first of all), and the shared
`values` set's own schema/cardinality/binding-strength validation. This is what keeps
the batch order-independent: a batch whose first several entries all conflict must still
refuse a bad `values` set or an invalid `reason`, rather than returning 200 having never
looked at either. Everything else - a stale `expected_row_version`, a missing entry - is
per-entry.

**FR-89's specimen cross-field check is the one exception, deliberately.** It depends on
one entry's own `specimen_unconstrained` flag, so it cannot be checked before the loop
reaches that entry - but a violation still aborts the *whole* batch (a 422, no partial
write) rather than producing a per-entry outcome, because the operator explicitly
selected that entry: silently skipping it would report success for a batch that did not
do what was asked. See ADR-0035.

**Atomicity, at two granularities.** A genuine concurrent write (the row-version check
above passed, but another writer committed before this one's flush) is caught per entry
by the same `session.begin_nested()`/`StaleDataError` pattern `save_entry` uses for a
single write - that entry becomes a `conflict` outcome, and the rest of the batch still
applies. A whole-request failure (an unhandled exception, including FR-89's abort above)
discards the entire batch, applied entries included: `nptc.db.session.session_scope`
commits once per request and rolls back on any exception.

**One audit event per applied entry, plus one batch header when at least one entry
applied.** Every entry that reaches `applied` goes through `save_property_values` itself,
so it gets the same `property_value.set` event the singular route produces (an
`unchanged` entry is a no-op and, like the singular route, emits nothing). A diff-free
`property_value.bulk_set` event (`entity_type="property_value_bulk"`, deliberately
distinct from `property_value_set` so a value-diff history query cannot pick up a header
it cannot render as one; `entity_id=key`) is appended once, after the loop, carrying the
outcome tallies structurally in its `after` payload
(`{"applied": n, "unchanged": n, "conflict": n, "not-found": n}`) via `nptc.audit.
recording.record_batch_summary` - `reason` stays the operator's own changelog note,
identical to the per-entry events', never decorated with counts. A batch where nothing
applied (every target `conflict`/`not-found`) emits no header at all, matching ADR-0018's
no-op posture at the batch level. All of it shares one `correlation_id` for free - minted
once per request (NFR-08), not per audit call - so the whole batch is reconstructable
from the log via that one value. See ADR-0035's addendum.

**Lock ordering (issue #265 round-2 review).** This route is the first HTTP surface that
can hold row-exclusive locks on more than one `catalogue_entry` row in one transaction,
which makes a cycle against `nptc.audit.writer`'s own `pg_advisory_xact_lock` reachable:
a bulk request already holding that lock (from an earlier entry's audit append) can
block on a row a concurrent single-entry writer holds, while that writer blocks on the
same advisory lock. `save_property_values_for_entries` now acquires the lock once,
deterministically, before its loop (`nptc.audit.writer.acquire_append_lock`) - this
closes the bulk-vs-bulk case but not bulk-vs-a-concurrent-singular-write, which Postgres
resolves safely (aborts one transaction, no corruption) rather than correctly (no
deadlock at all). See ADR-0035's own addendum and issue #281 for the complete fix.

### Errors (bulk property-value write)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 404 | No `property_definition` with `key`. An unknown `business_key` among `entries` is a `not-found` outcome in the 200 body, never a 404 for the whole request - and so is one deleted by a concurrent transaction mid-batch, rather than an uncaught `EntryNotFoundError` escaping as a whole-request 404. |
| 422 | A missing or low-information `reason` (FR-37), a write against a deprecated property (FR-11), the shared `values` set failing its property's JSON Schema, cardinality bound, or FR-89's specimen cross-field check (aborts the whole batch - see above), a `business_key` not shaped `NPTC-nnnnnn` (the same `BusinessKeyPath` pattern the singular route's path segment enforces - a malformed key is never a `not-found` outcome, indistinguishable from a well-formed one that simply does not exist), a repeated `business_key`, or more than 100 `entries`. |

## Entry core columns (issue #249)

All under `/api/v1/catalogue`, in `nptc.api.routers.catalogue_entries` - a router
separate from every module above, the same one-router-per-thing-written pattern: this
one owns `CatalogueEntry`'s own core columns, not a sub-resource attached to it.

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/{business_key}` | `PATCH` | `{status?, specimen_unconstrained?, reason, expected_row_version}` | `200 {status, specimen_unconstrained, row_version}` |

`business_key` is immutable (FR-03) and `preferred_term` writes through
`/amendment` (issue #227, ADR-0022's two storage homes) - this route is the remaining
two of `catalogue_entry`'s four auditable core columns. It shares its path with
[public-api.md](public-api.md)'s public `GET /catalogue/entries/{business_key}`: one
path item in the OpenAPI document carrying a public `get` (tag `catalogue`) and an
admin `patch` (tag `catalogue-admin`), the same write family every route above lives
in rather than `catalogue_admin.py`'s separate `/admin/` prefix.

Everything below HTTP already existed and was already tested as a library -
`nptc.catalogue.entries.save_entry` (issue #46) - so this route is purely the HTTP
adapter: FR-37's reason gate, FR-38's optimistic lock and FR-89's specimen cross-field
check (see below) are all handled by that existing service, the same posture
`/properties/{key}` above takes for `save_property_values`.

**`PATCH` semantics, not two named sub-resources.** Both fields are core columns of one
row under one `row_version`, and `save_entry` applies them in one `EntryChanges`/one
audit event - splitting them into two routes would mean two lock tokens and two audit
events for one editorial save. An absent field means "leave this alone", matching
`EntryChanges`' own `None`-means-unchanged contract - including
`specimen_unconstrained: false`, which is not `None` and so is applied like any other
value. A body naming neither field is refused (422) rather than silently accepted as a
no-op write.

**No status transition rules.** `save_entry` does a bare `setattr` and the PRD defines
no state machine for `status`; the wire type is the closed `CatalogueEntryStatus` enum,
so a value outside `draft|active|deprecated|withdrawn` is a 422 before the route body
ever runs, and the table's own `CHECK` constraint remains the backstop. Introducing
transition rules would be new editorial policy with no PRD backing today.

**FR-89's cross-field check, the reverse direction.** `save_property_values` already
refused a specimen value on an entry already flagged `specimen_unconstrained`; this
route is what makes the other direction reachable over HTTP - setting the flag on an
entry that already holds specimen values. `nptc.catalogue.property_values.
assert_specimen_flag_allowed` is the shared implementation, called from `save_entry`
itself (so `save_entries` inherits it too, not just this route), and it raises the same
`PropertyValidationError`/`PropertyValidationResponse` the forward direction does -
both directions share one refusal message, and the 422 names each blocking specimen
value by `ordinal`. It only runs on the *transition* to `True` - a submitted
`specimen_unconstrained: true` against an entry already `True` is not checked again, so
an editor whose form resends the whole entry on every save (issue #149's edit screen)
never gets refused for a flag they are not changing.

**A no-op resubmission returns 200, not 422.** `save_entry`'s own short-circuit means a
body naming a field but submitting its already-current value - e.g.
`{"status": "draft", ...}` against an entry already `draft` - returns `200` with the
entry's *unchanged* `row_version` and writes no audit event; the submitted `reason` is
silently discarded. This differs from a body naming neither field, which is refused
(422): naming neither is ambiguous between "no-op" and "caller forgot the field",
whereas naming a field with its current value unambiguously states the intent and
`save_entry` simply finds nothing to apply.

### Errors (entry core columns)

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 404 | No catalogue entry with this `business_key`. |
| 409 | A stale `expected_row_version` (FR-38) - the same `VersionConflictResponse` body `/amendment` and `/properties/{key}` return above. |
| 422 | A missing or low-information `reason` (FR-37), a body naming neither `status` nor `specimen_unconstrained`, a `status` outside the closed enum, or setting `specimen_unconstrained` to `true` while the entry does not already hold it and still holds one or more specimen values (FR-89) - `issues[]` then names each blocking value's `ordinal`. |

A rejected write leaves no partial mutation and no audit event: `save_entry`'s
row-version and FR-89 preconditions both run before its savepoint opens, the same
"reject before mutating" posture every write path in this document takes. A no-op
resubmission (see above) is not a rejection - it is a `200`.

## What these issues do not cover

- Entry creation over HTTP. `nptc.catalogue.entries.create_entry` is library-only -
  same FR-36 family as the writes above, different gap, not covered by any issue here.
- A read endpoint for a designation's `warning_collisions` on its own, independent of a
  write - see "Warning-severity collisions ride back on the write response" above for
  why that is deliberate for now, not merely deferred.
- Server-side SCTID resolution for the code binding form. `POST .../bindings` and
  `/replacement` still take `fsn`/`au_preferred_term` as caller-supplied fields, exactly
  as documented above - issue #240 (FR-26) adds `GET /api/v1/terminology/concepts/{code}`
  as the resolver #150's edit screen calls *before* submitting one of these routes, so an
  editor only ever types the code. That route is deliberately not under `/catalogue` and
  not part of this API - see [terminology-client.md](terminology-client.md#fr-26-the-interactive-lookup-route-issue-240)
  for why, and for its own error table.

## Route-table inventory (issues #44, #165)

`backend/tests/route_inventory_support.py::mutating_routes` walks the real app's route
table recursively (an included router's routes are not flattened into `app.routes`) and
`backend/tests/test_authz_inventory.py::COVERED_WRITE_ROUTES` is the declared coverage
set, grown alongside each new mutating endpoint.
`test_the_real_app_has_no_uncovered_mutating_route` fails in both directions: a route
with no declared coverage, and a covered entry naming a route that no longer exists.
Issue #219 is what first pointed that checker at the real app - previously it only had
synthetic apps to prove itself against, because the real app had no mutating routes yet.
Issue #224's four designation routes are added to `COVERED_WRITE_ROUTES` alongside the
three code-binding ones, with their negative-auth coverage in
`test_api_catalogue_designations.py`. Issue #228's entry-read route is a `GET`, so
`mutating_routes` never sees it and it needs no entry in `COVERED_WRITE_ROUTES` - its
own negative-auth coverage is `test_api_catalogue_admin_read.py`. Issue #248's `PUT
.../properties/{key}` is added the same way as the code-binding/designation routes, with
its own negative-auth coverage in `test_api_catalogue_properties.py`. Issue #249's
`PATCH /entries/{business_key}` is added the same way, with its own negative-auth
coverage in `test_api_catalogue_entries.py` - it shares a path with the public `GET`
`catalogue.py` already serves at that same URL, but the two are independent route
objects with independent method keys, so this is no different from any other route
sharing a path with a differently-methoded one. Issue #265's `POST .../entries/bulk/
properties/{key}` is added the same way, with its own negative-auth coverage
in `test_api_catalogue_properties.py` alongside the singular route's - no path
collision with `PUT .../entries/{business_key}/properties/{key}`, since `bulk` is a
literal path segment (not a `{business_key}` match) and the two routes use different
HTTP methods regardless. Issue #266's `GET /catalogue/admin/entries` and
`GET /catalogue/admin/search` are GETs, like #228's entry-read route before them, so
`mutating_routes` never sees them and neither needs a `COVERED_WRITE_ROUTES` entry - their
own negative-auth coverage is `test_api_catalogue_admin_listing.py`, and
`test_api_catalogue_admin_read.py::test_every_catalogue_admin_get_route_401s_anonymously`
picks both up automatically (it discovers every `catalogue-admin`-tagged GET from the
OpenAPI document rather than naming routes by hand).
