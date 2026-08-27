# The catalogue write API: code bindings (issue #219)

The first state-changing HTTP routes in this platform. Everything they call already
existed and was already tested as a library - `nptc.catalogue.bindings` (issue #48) - so
this document is about the HTTP adapter: what it exposes, how it addresses a resource
with no internal identifier on the wire, and what it deliberately leaves for later
issues.

This is not the public API [public-api.md](public-api.md) describes. It requires
authentication and the `catalogue.edit_published` permission, and it is not part of the
FR-20 external-vendor contract - it exists for this platform's own admin screens (#149,
#150, #151), starting with code bindings.

## Endpoints

All under `/api/v1/catalogue`, same path space as the public read routes.

| Path | Method | Body | Returns |
|---|---|---|---|
| `/entries/{business_key}/bindings` | `POST` | `{code, fsn, au_preferred_term?, edition_hint?, reason}` | `201 Binding` |
| `/entries/{business_key}/bindings/{code}/retirement` | `POST` | `{reason}` | `200 Binding` |
| `/entries/{business_key}/bindings/{code}/replacement` | `POST` | `{successor: {code, fsn, au_preferred_term?, edition_hint?}, reason}` | `200 BindingList` (both rows) |

`business_key` accepts any status, not only `active` - an editing surface has to reach a
`draft` entry before it can ever become `active`. `nptc.catalogue.entries.
load_entry_for_update` is the loader, shared with `save_entry`/`save_entries` rather than
re-querying `CatalogueEntry` by hand.

## Addressing a binding: by `code`, never an id

The public `Binding` model deliberately carries no `id` or `entry_id` -
[public-api.md](public-api.md#what-is-published-and-what-is-not) explains why. A client
retiring or replacing a binding therefore addresses it by the SNOMED CT code it was
bound with, not a row id it was never given.

`nptc.catalogue.bindings.load_active_binding(session, entry_id=..., code=...)` resolves
this: `ix_code_binding_one_active_entry_per_code` guarantees at most one **active**
binding can match `code` on a given entry, so the lookup is unambiguous. A code that has
already been retired, or was never bound, is a `404` (`CodeBindingNotFoundError`) - not
a `409` - because it is simply not addressable this way any more, not a conflicting
state. One consequence worth stating plainly: retiring an already-retired code is a
`404`, not the `409` `CodeBindingAlreadyRetiredError` the service layer itself raises for
an already-loaded binding - this router never reaches that branch, because it only ever
loads a binding that is still active.

`system` is not exposed on the wire at all. Every route defaults to `SNOMED_CT_SYSTEM`,
the only system in use today; the model and the unique index already key on
`(system, code)`, so exposing a second system later is additive, not a breaking change.

## Replacement is one request, not three

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

## Authorisation

Every route requires `Permission.CATALOGUE_EDIT_PUBLISHED` (FR-44) - held only by
`Role.ADMINISTRATOR`, and therefore in `MFA_REQUIRED_PERMISSIONS` (NFR-06). An
administrator who has not completed the MFA step-up gets the RFC 9470 challenge
(`WWW-Authenticate: Bearer error="insufficient_user_authentication", acr_values="2"`),
the same as any other MFA-gated permission - see
[permissions.md](permissions.md).

## Errors

| Status | When |
|---|---|
| 401 | No credential, or one that could not be verified. |
| 403 | Authenticated but missing `catalogue.edit_published`, or holding it without MFA (carries the step-up challenge). |
| 404 | No catalogue entry with this `business_key`, or no *active* code binding for this `code`. |
| 409 | A second active binding on this entry (FR-08), or this code already actively bound to a different entry (issue #49's blocking severity). |
| 422 | A malformed or Verhoeff-failing SCTID, an unrecognised edition hint, or a changelog note that fails FR-37's validation. |

Every `CodeBinding*` exception from `nptc.catalogue.bindings` is mapped in
`nptc.api.errors` by the same convention every other handler in that module follows:
read `exc.http_status`, never echo `str(exc)` into the response body (an exception
message may name an internal id, for the log only - NFR-04/NFR-26), and log at `INFO`
for a routine, expected refusal.

## What this issue does not cover

- Designation and property write routes - #149 and #151's own obligation, though they
  are expected to follow this router's shape (request/response models declared
  in-router, `Final` error-response dicts, no `response_model=`, no try/except in a route
  body).
- Entry-level `PATCH` and FR-38 optimistic locking on the wire. A code binding is a child
  row with no `row_version` of its own; an entry-level write will need one, and the
  public `EntryDetail`/`EntrySummary` models deliberately omit `row_version` today.
- The remaining unhandled designation `IntegrityError` constraints `nptc.api.errors`'
  own module docstring names (malformed `use`, a duplicate active term, a second active
  preferred designation in one language) - #149's obligation, not made any worse here.

## Route-table inventory (issues #44, #165)

`backend/tests/route_inventory_support.py::mutating_routes` walks the real app's route
table recursively (an included router's routes are not flattened into `app.routes`) and
`backend/tests/test_authz_inventory.py::COVERED_WRITE_ROUTES` is the declared coverage
set, grown alongside each new mutating endpoint.
`test_the_real_app_has_no_uncovered_mutating_route` fails in both directions: a route
with no declared coverage, and a covered entry naming a route that no longer exists.
This issue is what first pointed that checker at the real app - previously it only had
synthetic apps to prove itself against, because the real app had no mutating routes yet.
