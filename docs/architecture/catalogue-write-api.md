# The catalogue write API: code bindings and designations (issues #219, #224)

The first state-changing HTTP routes in this platform. Everything they call already
existed and was already tested as a library - `nptc.catalogue.bindings` (issue #48),
`nptc.catalogue.designations`/`nptc.catalogue.collisions` (issues #47, #49) - so this
document is about the HTTP adapter: what it exposes, how it addresses a resource with no
internal identifier on the wire, and what it deliberately leaves for later issues.

This is not the public API [public-api.md](public-api.md) describes. It requires
authentication and a permission (`catalogue.edit_published` for most routes;
`validation.acknowledge` for one, described below), and it is not part of the FR-20
external-vendor contract - it exists for this platform's own admin screens (issue #149
onward).

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
| `/entries/{business_key}/designations/amendment` | `POST` | `{term, new_term, language?, reason}` | `200 {designation: Designation, warnings: [CollisionWarning]}` |
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

### Preferred term is out of scope here

ADR-0022 keeps the catalogue's own en-AU preferred term on
`catalogue_entry.preferred_term`, never a `designation` row
(`ck_designation_no_en_au_preferred`) - every route above only ever touches
`designation` rows. Amending the entry's own preferred term needs FR-38's optimistic
locking on the wire, which the public `EntryDetail`/`EntrySummary` models do not carry
yet (the same gap #219's own "What this issue does not cover" named) - a follow-up, not
folded in here.

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
| 404 | No catalogue entry with this `business_key`, or no *active* designation for this `term`/`language`. |
| 409 | An error-severity collision against another live entry (FR-05, names the colliding entry's `business_key`/`preferred_term`), a duplicate active term or a second active preferred term in one language on this same entry, a designation already retired, or a concurrent acknowledgement of the same collision. |
| 422 | An unrecognised `use`, a malformed BCP-47 language tag, a term left empty after whitespace cleaning, the catalogue's own en-AU preferred term submitted as a designation (`ck_designation_no_en_au_preferred` - refused before the ORM, not an unmapped `IntegrityError`), more than one preferred term in one batch, or a changelog note that fails FR-37. |

Every exception `nptc.catalogue.designations`/`nptc.catalogue.collisions` raises is
mapped in `nptc.api.errors` the same way the `CodeBinding*` family is: read
`exc.http_status`, never echo `str(exc)`, log at `INFO`. Closing this mapping (four
constraints were previously unmapped `IntegrityError`s - see
`nptc.api.errors`'s own former "Known gap" note) is this issue's own contribution, not
inherited from #219.

## What these issues do not cover

- Entry-level `PATCH` and FR-38 optimistic locking on the wire, for either resource.
  Neither a code binding nor a designation is a row with its own `row_version`; an
  entry-level write (including amending the catalogue's own preferred term) will need
  the entry's `row_version` on the wire, and the public `EntryDetail`/`EntrySummary`
  models deliberately omit it today. Two administrators editing one entry's bindings or
  designations concurrently is the routine case this leaves genuinely unguarded against
  - the "Concurrency" notes above only prevent *data corruption* (two active bindings, a
  duplicate term), not one admin's edit silently overwriting context the other was
  working from with no version check at all.
- Property write routes - #151's own obligation, expected to follow the same shape.
- A read endpoint for a designation's `warning_collisions` on its own, independent of a
  write - see "Warning-severity collisions ride back on the write response" above for
  why that is deliberate for now, not merely deferred.

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
`test_api_catalogue_designations.py`.
