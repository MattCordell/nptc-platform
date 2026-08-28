# ADR-0028: A member-tier `registry.read` permission, distinct from `catalogue.browse` and `registry.manage`

**Status:** Accepted
**Date:** 2026-08-28

## Context

Issue #55 (PR #223) added `GET /registry/properties` and `GET /registry/properties/{key}`,
gated on `Permission.CATALOGUE_BROWSE` per the round-1 review of that PR (finding 6):
`REGISTRY_MANAGE` is administrator-tier, and gating a read route on it made
`DefinitionAudience.DATA_ENTRY` unreachable by the very audience it is named for — a member
filling in a submission form could not call `GET /registry/properties` to learn which
properties to offer.

Round-2 review of the same PR found the fix over-corrected: `Permission.CATALOGUE_BROWSE`
is held by `Role.ANON` (`backend/src/nptc/auth/permissions.py`'s `ROLE_PERMISSIONS`), so
both registry `GET` routes ended up fully public and unauthenticated. That was never the
intent — the property registry (property definitions, including their `constraints` and
binding configuration) is submission/maintenance-form plumbing for signed-in members, not a
public catalogue browse surface, and PRD Section 4.7 does not list it as an anonymous
capability.

**The choice was between two fixes:** make the routes genuinely public (accept
`CATALOGUE_BROWSE`'s existing anonymous grant as correct), or introduce a permission that
sits strictly between "public" and "administrator-only". The maintainer decided on the
latter: `GET /registry/properties` and `GET /registry/properties/{key}` are read routes for
authenticated members and above, not for a stranger.

## Decision

Add `Permission.REGISTRY_READ = "registry.read"` (`PermissionKind.READ`) to
`backend/src/nptc/auth/permissions.py`, held by `Role.PROVISIONAL`, `Role.MEMBER`,
`Role.REVIEWER` and `Role.ADMINISTRATOR` — **not** `Role.ANON` or `Role.OBSERVER`. FR-23
(a MUST) names Provisional, Member, Reviewer and Administrator as the roles able to submit
a proposed new test, and the submission form is generated from the property registry —
every property whose `scope` includes `submission` appears, and `required_for_submission`
ones are enforced. A role FR-23 names as able to submit cannot be denied the read access
that form generation depends on. `Role.OBSERVER` stays excluded: FR-80 makes Observer
entirely read-only and non-contributing, so it has no submission form to generate and no
principled claim on this permission. This is the deliberate line `REGISTRY_READ` draws —
"roles that can submit", not "roles above Observer" — and it mirrors the existing
`own`/`any`/quota split's own principle from ADR-0019: a new capability boundary gets its
own permission, never an ad-hoc check bolted onto an existing one.

PRD Section 4.7 gains a new row, "View property registry (read-only)", with `Y` from
Provisional up — placed directly after "Propose amendments" (the other rows describing
what Provisional actually gains over Observer per Section 4.3), not after "Register
interest" (a capability Provisional is explicitly denied). `test_permission_matrix.py`'s
`_ROW_PERMISSIONS` bridge maps the new row to `Permission.REGISTRY_READ`, keeping the
PRD table the single independent source of truth the test already parses directly.

`backend/src/nptc/api/routers/registry.py`'s two `GET` routes are re-gated on
`Permission.REGISTRY_READ`. The four mutating routes (`POST`, `PATCH`, the deprecation
`POST`, `DELETE`) are untouched — they stay on `Permission.REGISTRY_MANAGE`, which was
never in question.

**Why not just make the routes public.** The registry lists every property definition
including its `datatype`, `constraints`, and code-binding configuration — internal
maintenance-form shape, not published catalogue content (that is `catalogue.browse`'s own
job, over `catalogue_entry`/`code_binding`, unaffected by this change). Exposing it to an
anonymous caller was never an explicit product decision, only a side effect of reusing the
nearest existing permission that happened to already exist.

**Why not reuse `REGISTRY_MANAGE` gated more loosely (e.g. a role check).** That is exactly
the hard-coded, non-permission check FR-44 forbids — and it was also the round-1 defect
this whole thread started from (`REGISTRY_MANAGE` unreachable by Member).

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Leave both `GET` routes on `Permission.CATALOGUE_BROWSE` | Makes the property registry (including internal `constraints`/binding shape) fully public, which was never an explicit product decision — only an artefact of round-1's fix reusing the nearest existing read permission. |
| A role check (`Role.MEMBER` or above) instead of a new permission | The exact hard-coded authorisation check FR-44 forbids, merely relocated — the same objection ADR-0019 already raised against a `SUBMISSION_WITHDRAW` permission plus an ownership `if`. |
| Reuse `Permission.REGISTRY_MANAGE` for the `GET` routes too | Reintroduces round-1's own defect: `REGISTRY_MANAGE` is administrator-tier, unreachable by the `DefinitionAudience.DATA_ENTRY` member audience the routes exist to serve. |

## Consequences

- `Permission.REGISTRY_READ` is deliberately scoped to "roles that can submit" (Provisional
  and up), not simply "member-tier and up": any later route serving the same "authenticated,
  submission-capable caller reading maintenance-form plumbing" audience (e.g. local code
  systems) can reuse it rather than reaching for `CATALOGUE_BROWSE` or `REGISTRY_MANAGE`.
- `test_permission_matrix.py`, `test_permissions_data.py` (exhaustive `PERMISSION_KIND`
  classification, monotonicity) and the AST authorisation guard
  (`test_authorisation_guard.py`) all pick up the new permission automatically — none of
  them hand-list permissions by name.
- The two `GET /registry/properties*` routes now return `401` for an anonymous caller and
  `403` for an authenticated caller without `registry.read`, restoring
  `test_list_properties_no_credential_is_401`,
  `test_list_properties_authenticated_without_permission_is_403`,
  `test_get_property_no_credential_is_401` and a new
  `test_get_property_authenticated_without_permission_is_403` in
  `backend/tests/test_api_registry_properties.py`.
