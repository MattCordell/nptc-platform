# ADR-0019: Permission framework

**Status:** Accepted
**Date:** 2026-08-19

## Context

Issue #43 (ADR-0016) can answer *who is this* (`TokenVerifier.verify` → `authenticate` →
`Resolution`), but nothing in the repository can yet answer *may they do this*. NFR-07's
second sentence — "authorisation decisions are made server-side from the internal user
record, never from claims in the token" — has had no second half until now.

Three prior decisions converge on this issue and constrain its shape:

- **ADR-0014**, decision 1: the Keycloak realm declares no roles or groups, both clients
  set `fullScopeAllowed: false`, and `roles` is excluded from the default client scopes —
  tokens carry no `realm_access`/`resource_access`/`groups` claim at all
  (`test_keycloak_realm.py::test_no_application_roles_declared_in_the_realm`, FR-44).
  Permissions must therefore come from the platform database, keyed on internal
  `app_user.id`, never from a token claim.
- **ADR-0015**: `app_user` has no `role` column — "adding one here would create a second
  place a role is granted, and FR-44 requires permission checks, never role-name checks.
  Role grants land with #44." This issue needs a new table, not a column.
- **ADR-0016**: deferred NFR-06's mandatory-TOTP-for-administrators half here, "since that
  is where a role exists to make it mandatory *for*".

`P1-SEQUENCING.md` makes this issue a hard gate on every write path in the catalogue core,
property registry, and admin editing UI sections — it must land before any of them.

**FR-44 (SHOULD):** "Roles MUST be implemented as named sets of discrete permissions rather
than as hard-coded checks against a role enum. Authorisation checks test for a permission,
never for a role name."

**NFR-20 (MUST):** "Every request authorised server-side against the internal user record.
No authorisation decision made in the browser."

**FR-01 (MUST):** "An administrator may grant and revoke any role. The system MUST prevent
removal of the last remaining administrator."

**FR-80 (MUST):** "The Observer role MUST have no write capability of any kind ... enforced
as an absence of permissions ... covered by a negative authorisation test for every write
endpoint."

**FR-81 (MUST):** "The boundary between Reviewer and Administrator MUST be enforced
server-side per permission, and each withheld capability ... MUST have a negative
authorisation test asserting that a Reviewer is refused."

**Scope, confirmed with the maintainer:** the full PRD §4.7 matrix now, as data, with no
grant/revoke HTTP endpoint or admin UI (P2's); pure-library per ADR-0016's own precedent —
no FastAPI app in `backend/src`; NFR-06's mandatory-admin-MFA half included; a one-time
operator CLI to bootstrap the first Administrator, since FR-01's guard otherwise makes that
unreachable; a suspended user drops to the anonymous read surface, not a blanket refusal;
delivered as one PR.

## Decision

### Permissions are code, grants are data

`nptc.auth.permissions.Permission` (`StrEnum`, ~26 dotted constants) and `Role` are Python
enums; `ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]]` is an explicit literal
mapping, never `MEMBER = PROVISIONAL | {...}` — the matrix is not monotone in general
(Provisional may create submissions but not register interest), so each role is written
flat and reviewed against the PRD row by row. Only `user_role` grants (which user holds
which role) are database rows.

**Why not a database permission/role table.** A permission constant referenced by a check
site must exist as a code symbol, so a database-sourced table could only ever be a *shadow*
of the enum, free to silently disagree with it — the exact drift `nptc.db.roles` exists to
prevent for privilege grants. "Who may publish a release" belongs in `git blame` and PR
review, not an unreviewed `INSERT`, especially with no admin UI in scope. `mypy --strict`
typechecks a `Permission` reference at every call site; a database-sourced string cannot be.

**The strongest test in the issue**: `test_permission_matrix.py` parses the markdown table
at PRD §4.7 directly out of `docs/prd/NPTC-Catalogue-Platform-PRD.md` and asserts
`ROLE_PERMISSIONS` reproduces it cell by cell, with exhaustiveness checked in both
directions (an unrecognised PRD row fails; an orphan `Permission` fails). This test caught
a real discrepancy during development: the code originally granted Administrator both
`SUBMISSION_WITHDRAW_OWN` and `_ANY`, but the PRD table marks Administrator's cell "Y
(any)" only — fixed by removing the redundant `_OWN` grant (harmless in practice, since
`may_act_on` checks `any_` first and short-circuits, but the table is authoritative per its
own words and the code now matches it exactly).

### The three matrix qualifiers are three different kinds of thing

- **`Y (own)` / `Y (any)`** (withdrawing a submission) — a resource-scope distinction: two
  permissions, `SUBMISSION_WITHDRAW_OWN`/`_ANY`, resolved by
  `nptc.auth.authorisation.may_act_on` against the resource's owner `app_user.id`. A single
  `SUBMISSION_WITHDRAW` permission plus an ownership `if` at the call site would be exactly
  the hard-coded check FR-44 forbids, merely relocated.
- **`max 5` / `20/hr`** (submission creation) — not a permission at all. Both Provisional
  and Member hold the same `SUBMISSION_CREATE`; the budget is `SubmissionQuota`/`QUOTAS`,
  resolved per-role-set by `effective_quota`. Defined and unit-tested here, **not
  enforced** — there is no `submission` table yet, exceeding a quota is a 429 (not a 403),
  and it is a different audit story ("rate limited" vs "not permitted"). FR-41's per-user
  override is deferred; `effective_quota(..., override=...)` is the seam.
- **Reviewer's "promote Provisional to Member and no more"** — `Permission.ROLE_GRANT_MEMBER`
  vs `Permission.ROLE_GRANT_ANY`, resolved in `nptc.auth.grants.grant_role`: a Reviewer
  needs `ROLE_GRANT_MEMBER` only when the target is exactly `MEMBER` and currently holds
  `PROVISIONAL` — otherwise `ROLE_GRANT_ANY`. This is what stops a Reviewer laterally
  promoting an Observer, and stops `ROLE_GRANT_MEMBER` being read as "may grant Member to
  anyone".

### `user_role` — the one new table

```
id                 UUID PK
user_id            UUID NOT NULL FK -> app_user.id
role               TEXT NOT NULL CHECK role IN ('observer','provisional','member','reviewer','administrator')
granted_at         TIMESTAMPTZ NOT NULL DEFAULT now()
granted_by_user_id UUID NULL FK -> app_user.id     -- NULL only for the bootstrap grant
UNIQUE (user_id, role)
```

Revocation is a hard `DELETE`, never a `revoked_at` tombstone: the append-only, hash-chained
`audit_event` table is already the permanent history of every grant and revocation, and a
second, mutable history would compete with the one that must win. NFR-17's tombstone
posture protects *identifying personal data*, which a role grant is not.

**Grants**: `SELECT, INSERT, DELETE`, plus — see "A real privilege surprise" below —
column-level `UPDATE (granted_at)` only. `user_id`/`role`/`granted_by_user_id` remain
immutable at the privilege level: a grant is created or removed, never *meaningfully*
edited.

### A real privilege surprise, found by actually running the tests

The original design called for **no `UPDATE` grant at all** on `user_role`, on the theory
that a grant is created or removed, never edited. Running `test_grants.py`'s concurrent
last-administrator test against the real testcontainers Postgres immediately failed with
`permission denied for table user_role` inside `assert_not_last_administrator`'s own
`SELECT ... FOR UPDATE OF ur`.

Confirmed directly against a scratch Postgres 16 container (not merely inferred): Postgres
requires **some** `UPDATE` privilege on a table before it will honour `SELECT ... FOR
UPDATE`/`FOR SHARE` at all, even column-level, even on a column unrelated to the ones being
locked. Verified empirically: a role with `SELECT, INSERT, DELETE` and zero `UPDATE`
privilege is refused `SELECT ... FOR UPDATE` outright (`permission denied for table t`); the
same role granted `UPDATE (b)` on an unrelated column succeeds. This is the actual mechanism
FR-01's guard depends on — the row lock that makes two concurrent revocations of the last
two administrators resolve to exactly one success, not zero survivors.

The fix: `nptc.db.roles.GRANT_USER_ROLE_UPDATE_SQL` grants `UPDATE (granted_at)` only —
`granted_at` is the one column nothing in this codebase ever writes to after insert (a
server-defaulted creation timestamp), so this satisfies Postgres's row-locking requirement
while `user_id`/`role`/`granted_by_user_id` — the columns that would actually rewrite "who
granted this, and when" — stay immutable. `test_db_user_role_privileges.py` proves both
halves directly: `UPDATE role`/`UPDATE user_id` refused (`42501`), `UPDATE granted_at` and a
bare `SELECT ... FOR UPDATE` both succeed.

### FR-01's last-administrator guard: application check, row-locked

Postgres cannot express "at least one row where `role='administrator'` and the joined user
is active" as a `CHECK`/`UNIQUE`/`EXCLUDE` constraint (all per-row only), and PRD §14.1 /
ADR-0011 forbid business logic in triggers or stored functions. So
`nptc.auth.grants.assert_not_last_administrator` is an application check — but a naive
`SELECT count(*)` is unsafe under concurrency: two transactions each revoking one of the
last two administrators would each independently see "one other remains" and both commit,
leaving zero. The guard instead runs:

```sql
SELECT ur.id FROM user_role ur JOIN app_user u ON u.id = ur.user_id
 WHERE ur.role = 'administrator' AND u.status = 'active' FOR UPDATE OF ur
```

inside the caller's own transaction, locking every qualifying grant row (including the one
about to be removed). A concurrent caller blocks on this same statement until the first
commits or rolls back, then re-evaluates against the *post-commit* count.
`test_grants.py::test_concurrent_revocation_of_the_last_two_administrators_leaves_exactly_one`
proves this directly: two real threads, two real connections, racing to revoke the last two
administrators — exactly one succeeds, the other raises `LastAdministratorError`.

Counting only `status = 'active'` holders means a suspended administrator's grant does not
keep the floor satisfied — suspending the last administrator must itself be refused, an
invariant the P2 suspend path inherits from this same function.

**Closure is a removal path too, even though it never calls `revoke_role`.**
`nptc.auth.identity.close_account` runs `assert_not_last_administrator` before doing
anything else, then revokes every `user_role` grant alongside the identity deletions it
already performs — without the guard there, closing your own account would be the one-line
bypass of FR-01.

**Lock ordering, stated once so it is easy to find**: `nptc.audit.writer` takes a
fixed-key `pg_advisory_xact_lock` on every append, and every role mutation ends with one.
The order is always *`user_role` rows → advisory lock*, in both `revoke_role` and
`close_account`; reversing it anywhere would deadlock against a concurrent caller doing the
same in the opposite sequence.

### Bootstrapping the first administrator

With no grant/revoke endpoints and a last-administrator guard, a fresh deployment can never
acquire its first Administrator through the checked path — there is no `Principal` yet that
could hold `role.grant.any`. `scripts/grant_role.py` is the deliberate, one-time, out-of-band
escape hatch: an operator with direct database access, calling the same
`nptc.auth.grants.grant_role_unchecked` a first-login Provisional grant uses (still
audited, still idempotent). No `--force`, no revoke path through the script — once a second
Administrator exists, every further grant/revoke goes through the ordinary
`Principal`-checked functions.

### Default role on registration

PRD §4.3: a newly registered user *is* Provisional. `nptc.auth.identity._create_user` now
inserts a `PROVISIONAL` grant and records `user_role.granted`, inside the same
`begin_nested()` savepoint as the identity insert, so a username-collision retry leaves no
orphan grant event. A real row, not an implicit default: FR-40's dashboard must answer
"what roles" in one query, and an implicit default would make Observer (a demotion)
representable only as an absence.

### `Principal`, not the mapped `User`

`nptc.auth.principal.Principal` carries `user_id` (a UUID) and `UserRef` (the existing
NFR-04 serialisation boundary), never the ORM `User` instance: an ORM object binds the
`Principal`'s lifetime to a `Session`, whereas a `Principal` is exactly the kind of object
worth keeping and logging after the session that produced it has moved on. `mfa_satisfied`
is derived *inside* `principal_for` from the verified `claims.acr`, never accepted as a
constructor argument — no call site can hand-construct a `Principal` with `mfa_satisfied=
True`.

| Resolution / status | Behaviour |
|---|---|
| `MANUAL_LINK_REQUIRED` | `ManualLinkRequiredError` (409) — never degrades to anonymous, since that would make "a valid token that cannot be linked" indistinguishable from "no token" in any log built from the result. |
| `CLOSED` | `AccountClosedError` (403) — a fail-closed backstop; closure deletes every identity, so this should be unreachable via `resolve_user_for_claims` in practice. |
| `SUSPENDED` | Drops to exactly `ROLE_PERMISSIONS[ANON]` — the public read surface a stranger has, losing the Observer-only rows. Refusing every request outright (including genuinely public GETs) was considered and rejected as both surprising and a small information leak. |
| `ACTIVE` | `ROLE_PERMISSIONS[ANON]` ∪ the union over granted (MFA-effective) roles — a user with zero grants is never *less* capable than a stranger. |

A new error hierarchy, `nptc.auth.errors_authorisation.AuthorisationError`, lives apart from
`nptc.auth.errors.TokenError` — the latter's docstring states every member is 401-shaped,
which a future #41 `except TokenError → 401` depends on; adding 403/409 types there would
falsify that. `http_status` is a `ClassVar` per subclass so a blanket handler cannot
mis-map `ManualLinkRequiredError`/`LastAdministratorError` (409) as 403.

### NFR-06: mandatory MFA for administrators

The token carries no `acr`/`amr` and the realm has no roles, so Keycloak cannot know who is
an administrator — enforcement is necessarily server-side; Keycloak's only job is making
step-up authentication achievable and provable.

**Realm** (`deploy/keycloak/realm/nptc-realm.json`): a new top-level `browserFlow: "nptc
browser"`, a copy of the built-in browser flow whose `forms` subflow gates OTP behind a
`conditional-level-of-authentication` executor (`loa-condition-level: "2"`) rather than
`conditional-user-configured` — OTP is required whenever the client requests a satisfying
`acr_values`, not merely when the user happens to already have TOTP configured. The existing
`CONFIGURE_TOTP` required action means an un-enrolled user is driven into enrolment rather
than refused outright.

**Verified against a real, disposable Keycloak 26.7.1 container** (not merely asserted
offline): the realm imports cleanly; the `conditional-level-of-authentication` authenticator
resolves and binds its config correctly (confirmed via the admin REST API); and a full
browser authorization-code request with `acr_values=2` against a user with no TOTP
configured redirects to `CONFIGURE_TOTP` exactly as designed. `test_keycloak_realm.py`
gained a `nptc_loa-2 condition` container of offline assertions plus an extension to the
existing integration test proving the flow binds and its executions resolve on a live
import. Full "the resulting token's `acr` claim reads `2` after OTP completion" was not
automated (it requires scripting a TOTP enrolment round-trip, disproportionate effort for
this issue) but was manually verified against the same container; a frontend follow-up
issue must still wire `acr_values` into the SPA's login request and handle the
`insufficient_user_authentication` step-up challenge for the loop to close end to end.

**Claims/verifier**: `OidcIdentityClaims` gained `acr: str | None` and `auth_time: int |
None` (both defaulted, so no existing constructor call site broke), narrowed in
`TokenVerifier.verify` exactly as `email`/`preferred_username` already are. These are
authentication facts (how/when the user authenticated), not authorisation claims — reading
them here is required by the AST guard's own discipline (no other module may re-parse the
token), and an inline comment in `claims.py` draws that line explicitly so a future reader
does not take `acr` as licence to add `realm_access`.

**Enforcement, two layers, both fail-closed**: *structural* — `principal_for` drops
`Role.ADMINISTRATOR` from the effective role set whenever `mfa_satisfied` is false,
recording it in `mfa_suppressed_roles`, so NFR-06 holds even at a check site that forgot
about MFA entirely; *diagnostic* — `require_permission` raises `MfaRequiredError` (a
`PermissionDeniedError` subclass) rather than a bare denial when a suppressed role would
have granted the permission, so a future #41 adapter can render an actionable RFC 9470
step-up challenge. `MFA_REQUIRED_PERMISSIONS` is derived as exactly `ADMINISTRATOR_ONLY`,
never hand-listed, so a new admin-only permission inherits the requirement automatically.

### The FR-44 AST guard

`test_authorisation_guard.py`, modelled directly on `test_token_verification_guard.py`
(including its own positive control with an exact `Counter`), scans `backend/src` for four
patterns: a comparison against a role-name string literal; a comparison against a `Role`
member directly (the rule that actually satisfies FR-44's wording — a literal-only guard is
bypassed by simply writing `Role.ADMINISTRATOR`); a membership test against `.roles`; and a
string literal passed where `require_permission`/`has_permission`/`may_act_on` expects a
`Permission` member. `test_token_verification_guard.py` itself gained a fifth rule: the
literals `"acr"`, `"amr"`, `"realm_access"`, `"resource_access"`, `"groups"`, `"roles"`,
`"scope"` must never be subscripted or `.get()`-read outside `nptc/auth/tokens.py` — a
direct, greppable enforcement of NFR-07's second sentence.

### FR-80 and FR-81 are provable today, without a single endpoint

Both are worded per-endpoint, and `backend/src/nptc/api/` is still a docstring stub with
zero routes. Both are also, independently, statements about *data*:

- FR-80: `not (ROLE_PERMISSIONS[OBSERVER] & WRITE_PERMISSIONS)` — stronger than any
  per-endpoint test, and it fails the day a write permission is ever added to Observer.
- FR-81: `ROLE_PERMISSIONS[REVIEWER] & ADMINISTRATOR_ONLY == frozenset()`, parametrised
  over the PRD §4.5 withheld-capability list plus the non-obvious seventh
  (`SUBMISSION_WITHDRAW_ANY` — a Reviewer may withdraw only their own submission).

The reusable negative-authorisation harness has three layers: `authz_support.
assert_permission_refused` (library-level, with its own vacuity guard — it first asserts
the permission is genuinely held by *some* role, so a typo'd constant cannot make the
helper pass forever without checking anything real); `authz_app_support.
build_authz_test_app`/`assert_http_forbidden` (a throwaway `FastAPI()` app — the only
`fastapi` import in this issue's scope — proving 403-with-no-leakage and the 401-vs-403
pair real endpoints most reliably get backwards); and `route_inventory_support.
mutating_routes`/`assert_inventory_covers_every_mutating_route` (fails in both directions —
an uncovered mutating route, or a covered entry naming a route that no longer exists —
proven meaningful today via a positive-control synthetic app, and intended to be shared
with issue #165's own route-table inventory test rather than each growing a divergent
walker).

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Permissions and role→permission mappings as database rows | Unreviewable and undiffable without an admin UI (out of scope); creates a shadow of the code enum that can silently disagree with it; a database-sourced string cannot be typechecked by `mypy --strict`. |
| Keycloak realm roles + `realm_access` in the token | Reverses ADR-0014's decision 1 and contradicts NFR-07's second sentence directly; would require `fullScopeAllowed: true` and break `test_no_application_roles_declared_in_the_realm`. |
| A `role` column on `app_user` | One role per user, no grant provenance, no audit trail — already argued down in `user.py`'s own docstring and ADR-0015. |
| A database trigger or a materialised counter row for the last-administrator invariant | PRD §14.1 / ADR-0011's ban on business logic in triggers or stored functions, precisely because both are invisible to tests and code review. |
| `revoked_at` soft-delete on `user_role` | A second, mutable history competing with the append-only, hash-chained `audit_event` log that must win. |
| A single `SUBMISSION_WITHDRAW` permission plus an ownership `if` at the call site | The exact hard-coded authorisation check FR-44 forbids, merely relocated rather than removed. |
| Predicates/lambdas attached to a permission set to express own/any or quota qualifiers | Destroys the "permissions are inspectable data" property the matrix test, the FR-80/FR-81 property tests, and the AST guard all depend on. |
| A bootstrap-admin environment variable consulted at login | Claim-derived authorisation — exactly what NFR-07's second sentence forbids — and a permanent backdoor rather than a one-time, audited operator action. |
| `amr` rather than `acr` for the MFA proof claim | `acr` is standard OIDC and Keycloak's entire step-up mechanism is built on it; Keycloak's `amr` support is newer and version-sensitive, with no compensating benefit here. |
| Refusing every request from a suspended user outright | Surprising and mildly information-leaking on the genuinely public read surface — a suspended user would get *less* than an anonymous stranger on an endpoint anyone may call. |
| `Permission` as a plain `Enum` (not `StrEnum`) | Would block string-literal smuggling into a `frozenset[Permission]` by construction, but costs readable audit payloads and clean JSON/database round-tripping; the AST guard's `string-permission-argument` rule covers the same gap instead. |
| No `UPDATE` grant at all on `user_role` | The original plan — discovered wrong by actually running `test_grants.py`'s concurrency test against real Postgres: `SELECT ... FOR UPDATE` requires *some* `UPDATE` privilege on the table, confirmed against a scratch container. Column-level `UPDATE (granted_at)` only was the fix (see "A real privilege surprise" above). |

## Consequences

- `FR-44` moves to `implemented` — the matrix test and the AST guard are both mechanical,
  durable proofs, not endpoint-shaped ones.
- `NFR-20`, `FR-01`, `FR-80`, `FR-81` move to `in-progress`, deliberately not
  `implemented`: all four are worded per-request/per-endpoint, and `backend/src/nptc/api/`
  has zero routes today. The permission-level property tests proven here are stronger than
  any per-endpoint test would be, but the issue's own acceptance criterion ("every mutating
  endpoint has a negative-case test") is vacuously true at zero endpoints. The route
  inventory test (with its positive control) is how that debt is held honestly rather than
  claimed away — see ADR-0002's own point about evidence without a test cutting both ways.
- `NFR-06` stays `in-progress`: the server-side refusal and the realm step-up flow are
  done and proven against a real container; the SPA must still request `acr_values` on
  login and handle an `insufficient_user_authentication` challenge for the loop to close.
  A follow-up frontend issue is required and must be referenced from this PR.
- FR-41's per-user rate-limit override is deferred — `effective_quota`'s `override`
  parameter is the seam, but no column exists yet for it to read, and none is added
  speculatively.
- The lock-ordering rule (`user_role` rows before the audit advisory lock) binds every
  future role mutator, including the P2 suspend/grant/revoke endpoints.
- `route_inventory_support.py`'s route walker is intended to be shared with issue #165's
  own audit-write-path route inventory test, rather than each growing a second, potentially
  divergent walker.
- `docs/architecture/permissions.md` records the matrix as implemented, the `Principal`
  derivation table, the check API and its future #41 adapter, the last-administrator guard
  and its locking, and the NFR-06 step-up flow end to end.
