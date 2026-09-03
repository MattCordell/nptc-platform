# The catalogue admin API: entry read, code bindings, designations and property values (issues #219, #224, #228, #227, #248)

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

## Code bindings

All under `/api/v1/catalogue`, same path space as the public read routes.

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/{business_key}/bindings` | `POST` | `{code, fsn, au_preferred_term?, edition_hint?, reason}` | `201 Binding` |
| `/entries/{business_key}/bindings/{code}/retirement` | `POST` | `{reason}` | `200 Binding` |
| `/entries/{business_key}/bindings/{code}/replacement` | `POST` | `{successor: {code, fsn, au_preferred_term?, edition_hint?}, reason}` | `200 BindingList` (both rows) |

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
| 409 | A second active binding on this entry (FR-08), this code already actively bound to a different entry (issue #49's blocking severity) - including two concurrent requests racing for the same entry or code, see below - or `/replacement`'s successor naming the same code it is meant to replace. |
| 422 | A malformed or Verhoeff-failing SCTID, an unrecognised edition hint, a blank `fsn`/`au_preferred_term`, or a changelog note that fails FR-37's validation. |
| 500 | A platform-side invariant failed - not a caller mistake, and not produced by anything a well-formed request can trigger on its own. |

**Concurrency.** `create_binding`'s active-binding checks are read-then-write, so two
concurrent binds racing for the same entry or the same code both pass the pre-check and
only one wins at insert - `ix_code_binding_one_active_per_entry`/
`ix_code_binding_one_active_entry_per_code` are what actually decide. The loser's
`IntegrityError` is translated to the same `409` domain error the pre-check would have
raised, via `nptc.db.errors.unique_violation_constraint` - the same constraint-name unwrap
`nptc.auth.identity._is_username_collision` already needed, pulled out to one shared
helper rather than a second copy - so a lost race still reads as a normal conflict, not a
500. `load_entry_for_update` takes no row lock, so this is optimistic, not pessimistic,
concurrency control - acceptable here because the loser gets a clean, actionable 409
rather than corrupting state. Forced deterministically (an `after_cursor_execute` hook
supplying the ordering plain sequential test code cannot express) in
`test_a_lost_concurrent_code_race_is_a_domain_error_not_a_raw_integrityerror`.

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
| `/entries/{business_key}/designations` | `POST` | `{terms: [string], use?, language?, reason}` | `201 {designations: [Designation], warnings: [CollisionWarning]}` |
| `/entries/{business_key}/designations/amendment` | `POST` | `{term, new_term, language?, use?, expected_row_version?, reason}` | `200 {designation: Designation, warnings: [CollisionWarning], row_version}` |
| `/entries/{business_key}/designations/retirement` | `POST` | `{term, language?, reason}` | `200 Designation` |
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

### `expected_row_version`: required on one branch, honoured on both

`catalogue_entry` is a row with FR-38 optimistic locking, so a write to it cannot be
accepted without the caller's version. `designation` has no version of its own. The
field is therefore optional in the schema and conditionally required in fact:

| `term` resolves to | `expected_row_version` |
|---|---|
| the entry's own preferred term | **Required.** 422 without it. |
| an active `designation` row | Optional. Checked against `catalogue_entry.row_version` whenever supplied. |

Optional rather than required outright, because making it required would break every
client of the designation branch this route has shipped with since #224. Enforced
whenever supplied rather than ignored on the branch that does not demand it, because
silently discarding a caller's lock token is worse than either honouring it or refusing
it - a client that sent one believes it is protected.

The missing-token refusal is `nptc.api.errors.PreferredTermVersionRequiredError`, raised
in the route rather than validated on the request model: which storage home a term lives
in is a database question, and a pydantic validator runs before the route body has a
session.

Callers read the current version from `EntryDetail.row_version` (issue #227 put it
there; `EntrySummary` deliberately does not carry it - see
[public-api.md](public-api.md)), and get the new one back on the write response, so a
save never has to be followed by a re-read. On the designation branch that value is
unchanged by the write.

A stale version is a 409 carrying `business_key`, `expected_row_version`,
`current_row_version`, `conflicts[]` (each `field`/`submitted`/`current`) and
`changed_by`/`changed_at` - FR-38's rationale is explicit that the caller must be able
to reconcile rather than retry blind. `conflicts` is empty on the designation branch,
which declared no entry-level change: that is `ConflictReport`'s documented
non-overlapping-field case, still refused because the version is the contract
regardless.

This is a partial answer to the concurrency gap "What these issues do not cover" names
below, not a complete one: it is opt-in, and amending a designation does not itself bump
the entry's version, so two administrators editing different designations still do not
conflict with each other.

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
| 409 | An error-severity collision against another live entry (FR-05, names the colliding entry's `business_key`/`preferred_term`), a duplicate active term or a second active preferred term in one language on this same entry, a designation already retired, or a concurrent acknowledgement of the same collision. On `/amendment` only, also a stale `expected_row_version` (FR-38) - a richer body, see "`expected_row_version`" above. |
| 422 | An unrecognised `use`, a malformed BCP-47 language tag, a term left empty after whitespace cleaning, the catalogue's own en-AU preferred term submitted as a designation to `POST .../designations` (`ck_designation_no_en_au_preferred` - refused before the ORM, not an unmapped `IntegrityError`; amend it through `/amendment` instead), more than one preferred term in one batch, or a changelog note that fails FR-37. On `/amendment` only, also amending the entry's own preferred term with no `expected_row_version`. |

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

## What these issues do not cover

- Entry-level writes other than the preferred term. `EntryChanges` also carries `status`
  and `specimen_unconstrained`, and neither has a route: issue #227 wired only
  `preferred_term`, through `/amendment`. Publishing or withdrawing an entry over HTTP
  is still unbuilt.
- FR-38 optimistic locking on the code-binding routes. Issue #227 put `row_version` on
  the wire and `expected_row_version` on `/amendment` only; `catalogue_bindings.py`'s
  three routes take no version at all. Two administrators editing one entry's bindings
  concurrently therefore remains unguarded - the "Concurrency" notes above only prevent
  *data corruption* (two active bindings, a duplicate term), not one admin's edit
  silently overwriting context the other was working from. The same is true of two
  administrators editing different *designations* on one entry, since a designation
  write does not bump the entry's version.
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
its own negative-auth coverage in `test_api_catalogue_properties.py`.
