# ADR-0033: Exact-code lookup routes — 200 with the shared body, a code-not-a-table alias registry, and one 404 sentence for two causes

**Status:** Accepted
**Date:** 2026-09-07

## Context

FR-17 requires the catalogue to be reachable by a stable, citable URL - vendors are meant
to reference catalogue entries from their own documentation, so the URL shape is a public
contract from the day it ships. Two of the three required forms already existed:
`GET /catalogue/entries/{business_key}` (issue #142) and the frontend's route-tree
declaration of all three shapes with FR-06-safe string codecs (issue #146). Missing was
the exact-code half: `GET /catalogue/code/{system_token}/{code}` and
`GET /catalogue/lookup?system={uri}&code={code}`.

The unambiguity FR-17 asks for is already a database invariant for the common case -
`ix_code_binding_one_active_entry_per_code` is a partial unique index on `(system, code)
WHERE status = 'active'` - but that invariant is silent on a code retired on more than one
entry, since a binding is retired and replaced, never rebound in place, and each
retirement is a new row.

## Decision

### Both routes return 200 with the identical `EntryDetail` body, never a redirect

`GET /catalogue/code/{system_token}/{code}` and `GET /catalogue/lookup` serve exactly what
`GET /catalogue/entries/{business_key}` serves for the same entry - not a `307` to the
business-key URL, and not a thinner "here is the business_key, look it up yourself"
response. This makes the issue's own acceptance criterion ("all three route forms resolve
the same entry for the same code") literally testable as body equality, costs a vendor's
HTTP client no redirect hop, and keeps the existing whole-body response-hygiene scan
(`test_api_public_response_hygiene.py`) covering the new routes for free, since it derives
its route list from the app's own OpenAPI document.

`catalogue_shared.build_entry_detail` is a new shared assembler so `read_entry` and its two
new siblings build one `EntryDetail` shape from one place, rather than three near-copies
that could drift.

### A retired-only code still resolves (FR-08), flagged on the binding itself

No extra response field: the caller finds its code in the entry's `bindings` list, exactly
as it does through `/catalogue/entries/{business_key}/bindings` today, carrying
`status: "retired"`, `retirement_reason`, and - where FR-08's replacement case applies -
`replaced_by_code`.

### The retired-collision tie-break is a real `retired_at` column, not a proxy

More than one entry can hold the same code as a *retired* binding, which is the one case
the active-binding invariant above cannot rule out. `nptc.catalogue.queries.
get_entry_by_code` needs a deterministic answer for that case, and three shapes were
considered:

1. **Order by `updated_at DESC`, `business_key ASC`.** No schema change, but `updated_at`
   moves on *any* update to the row (an `fsn` refresh from the FR-45 validation sweep,
   say), not only a retirement - so the winner among two retired duplicates could change
   without anything about the retirement itself changing. This was the plan's own
   provisional recommendation, flagged as a weakness to confirm before this ADR was
   written.
2. **A real `retired_at` column**, set once by `nptc.catalogue.bindings.retire_binding`
   alongside `status`/`retirement_reason`, ordered `retired_at DESC, business_key ASC`.
   A migration, but an honest one: the column means exactly what it says, and cannot drift
   for a reason unrelated to retirement.
3. **A 409 refusing to resolve an ambiguous retired code at all**, naming the candidate
   business keys. The most literal reading of "MUST be unambiguous", but it puts a new
   status on the public contract for a case issue #49's collision detection already makes
   rare, and it makes a *read* route capable of refusing to answer a well-formed request
   about published data - a posture nothing else on this public surface takes.

**Chosen: option 2**, confirmed with the maintainer during planning. `retired_at` mirrors
`retirement_reason`'s own CHECK shape exactly (mandatory iff `status = 'retired'`,
forbidden otherwise - `ck_code_binding_retired_at`), migration 0016 backfills existing
retired rows from `updated_at` (the best available approximation for a row that predates
the column) before the CHECK is added, and the privilege grant is a new, separate
statement (`GRANT_CODE_BINDING_RETIRED_AT_UPDATE_SQL`) rather than a widened
re-execution of migration 0008's, so a from-scratch replay never grants a column before it
exists (matching migration 0013's own precedent for `local_code_system_key`).

`business_key ASC` as the second ordering key is what makes the whole order total: without
it, two retired bindings sharing the same `retired_at` to the microsecond (unlikely, but
not impossible under a bulk retirement) would leave the winner to whatever order the
database happened to return rows in, and the answer could change between two identical
requests.

### The `system_token` alias registry is code, not a database table

`nptc.catalogue.code_systems.SYSTEM_TOKENS` is a frozen `dict[str, str]` mapping a short,
URL-legal token to a code system's full URI (`sct` → `http://snomed.info/sct`), ships with
one entry, and never imports FastAPI. This is
[ADR-0019](0019-permission-framework.md)'s "permissions as code, never DB rows" argument
applied to a second registry: a second registered system is a deployment adding support
for a code system the catalogue does not yet bind against - a reviewed, diffable change to
one module, not a seed row an administrator could add by accident through a future admin
screen, and not a second implementation to keep in step with the first (ADR-0001/FR-74).

### One 404, shared by two different causes

An unregistered `system_token` (or, on `/lookup`, an unregistered system URI) and a
registered one that simply matches no published entry's code are both reported with the
identical fixed sentence (`CodeLookupNotFoundError`, mapped by
`nptc.api.errors.register_exception_handlers` exactly as `EntryNotFoundError` is), naming
the registered tokens. This was an open question at planning time - the alternative was
two distinct sentences, on the reasoning that "your token is wrong" is more actionable
for a caller than "that code does not exist" - and was resolved in favour of the single
sentence: response text should not let a caller distinguish "is this deployment's registry
misconfigured" from "does this code simply not exist", the same non-disclosure posture
`nptc.catalogue.queries.get_entry` already takes for a hidden-status `business_key`
(neither a caller nor an unauthenticated party enumerating codes learns anything about
*why* a code did not resolve).

A **malformed** `system_token` - one that fails `SYSTEM_TOKEN_PATTERN`
(`^[a-z][a-z0-9-]{0,31}$`) - is a 422 before any query runs, matching `BusinessKeyPath`'s
own precedent: shape validation happens at the path-parameter layer, and only a
well-formed-but-unregistered token reaches the 404 above. `code` itself is deliberately
**not** shape-validated at the API layer (no Verhoeff check, no digit-only pattern): an
unrecognised code and a malformed one both simply fail to match a binding and get the
identical 404, which is one rule instead of two and costs nothing since the SNOMED CT
CHECK already guarantees every *stored* code is well-formed.

## Consequences

- `EntryNotFoundError` and `CodeLookupNotFoundError` are two separate exception types
  carrying two different fixed sentences, even though both mean roughly "nothing to show
  you" - `EntryNotFoundError`'s sentence says nothing about tokens because the
  business-key route has no token to have gotten wrong, and reusing one sentence across
  both routers would either name tokens on a route that never has any or omit them where
  FR-17's acceptance criterion requires them.
- `get_entry_by_code`'s `ORDER BY (status = 'active') DESC, retired_at DESC,
  business_key ASC LIMIT 1` answers "which entry" in one statement rather than "try active,
  then fall back to retired" as two - an active match, when one exists, sorts first by
  construction (the partial unique index already guarantees at most one), so the second and
  third ordering keys only ever decide between retired candidates.
- The three public route stubs on the frontend (`/catalogue/$businessKey`,
  `/catalogue/code/$systemToken/$code`, `/catalogue/lookup`) remain
  `createPlaceholderPage` - out of scope here, per the plan's own scoping. They become real
  screens with the public search/entry UI issue, which should be listed as blocked on this
  one.
- A second registered system_token (a future LOINC or local code system alias) is a
  one-line addition to `SYSTEM_TOKENS` plus a fixture, not a migration.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| `307` redirect from the code routes to the business-key URL | Costs a vendor's HTTP client a redirect hop, and makes the "byte-identical body" acceptance criterion untestable as a direct equality. |
| A thin resolution stub (`{business_key}` only) from the code routes | Forces a second request for the entry a caller almost always wants next; the by-business-key route already exists to serve exactly that. |
| Resolve only the *active* binding for a code (retired codes 404) | Contradicts FR-08, which requires a retired binding stay resolvable so an implementer holding an inactivated code learns that rather than getting a bare 404. |
| `updated_at DESC` as the retired-collision tie-break | No schema change, but drifts on any unrelated column update, not only a retirement - an honest column costs one migration. |
| A 409 refusing to resolve an ambiguous retired code | The most literal "unambiguous" reading, but adds a new status to the public contract for a case collision detection already makes rare, and makes a read route refuse a well-formed request about published data. |
| A database-backed `system_token` registry | Unreviewable and untypecheckable as a diff, the same argument ADR-0019 already settled for the permission matrix. |
| Two distinct 404 sentences (unregistered token vs. no matching code) | Lets response text distinguish "your token is wrong" from "that code does not exist" - a disclosure this surface has no reason to make, and inconsistent with the non-disclosure rule already applied to a hidden `business_key`. |
| Shape-validating `code` (Verhoeff/digit-pattern) at the API layer | A second rule alongside the token check for no real benefit - an unrecognised and a malformed code already resolve to the identical 404, and the database CHECK already guarantees every stored code is well-formed. |
