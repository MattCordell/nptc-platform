# Permission framework

Issue #44 (FR-44, NFR-20, FR-01, FR-80, FR-81, and the deferred half of NFR-06). See
[ADR-0019](../adr/0019-permission-framework.md) for the full rejected-alternatives
discussion; this document is the implementation reference.

## The matrix, as code

`nptc.auth.permissions` holds PRD §4.7's authoritative permission matrix as data:

- `Role(StrEnum)`: `ANON, OBSERVER, PROVISIONAL, MEMBER, REVIEWER, ADMINISTRATOR`.
  `GRANTABLE_ROLES` excludes `ANON` — an ungranted user simply *is* anonymous.
- `Permission(StrEnum)`: ~26 dotted constants, one per PRD capability (or capability
  group), e.g. `catalogue.edit_published`, `release.publish`, `submission.withdraw.own`.
- `ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]]`: each role an **explicit
  literal set**, never built by extending a lower role's set — the matrix is not
  monotone in general (Provisional may create submissions but not register interest).
  `test_permissions_data.py::test_role_permissions_is_monotonically_increasing` checks
  the monotonicity that *does* hold, as a test property, not a code-level assumption.

**Why code, not database rows.** A permission constant a check site references must
exist as a Python symbol. A database-sourced permission table could only ever be a
*shadow* of this enum, free to silently disagree with it. Changing who may publish a
release goes through `git blame` and PR review, not an unreviewed `INSERT` — especially
with no admin UI in this issue's scope. `mypy --strict` typechecks every `Permission`
reference; a database string cannot be.

**`Permission.REGISTRY_READ` (ADR-0028) in practice.** Two `GET` routes hold it so far:
`nptc.api.routers.registry`'s `/registry/properties`/`/registry/properties/{key}`
(issue #55, its originating case) and `nptc.api.routers.terminology`'s
`/terminology/concepts/{code}` (issue #240, FR-26) — exactly the reuse ADR-0028's own
Consequences predicted: "any later route serving the same … audience … can reuse it
rather than reaching for `CATALOGUE_BROWSE` or `REGISTRY_MANAGE`."

**Kept honest against the PRD itself.** `test_permission_matrix.py` parses the markdown
table at PRD §4.7 directly out of `docs/prd/NPTC-Catalogue-Platform-PRD.md` and asserts
`ROLE_PERMISSIONS` reproduces it cell by cell, with exhaustiveness checked in both
directions. This is the strongest test in the issue: a PRD edit that adds a row, or a
`Permission` no row ever grants, both fail loudly.

### The three matrix qualifiers

| PRD notation | Mechanism | Where |
|---|---|---|
| `Y (own)` / `Y (any)` | Two permissions (`SUBMISSION_WITHDRAW_OWN`/`_ANY`), resolved against the resource's `owner_user_id` | `nptc.auth.authorisation.may_act_on` |
| `Y (max 5)` / `Y (20/hr)` | Not a permission — a numeric budget on the same `SUBMISSION_CREATE` permission | `nptc.auth.permissions.SubmissionQuota`/`QUOTAS`/`effective_quota` (defined and unit-tested; **not yet enforced** — no `submission` table exists) |
| "Promote Provisional to Member and no more" | `Permission.ROLE_GRANT_MEMBER` vs `ROLE_GRANT_ANY` | `nptc.auth.grants.grant_role` |

A single permission plus an ownership `if` at the call site, or a predicate attached to a
permission set, would both be the hard-coded authorisation check FR-44 forbids — see
ADR-0019's rejected alternatives.

## `Principal`

`nptc.auth.principal.Principal` is the one object a check site ever inspects:

```python
@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID | None  # None only for ANONYMOUS
    user_ref: UserRef | None  # the NFR-04 serialisation boundary - never the ORM User
    status: UserStatus | None
    roles: frozenset[Role]
    permissions: frozenset[Permission]
    mfa_satisfied: bool  # derived from claims.acr, never a constructor argument
    mfa_suppressed_roles: frozenset[Role]
```

It does not carry the mapped `User` instance — an ORM object ties the `Principal`'s
lifetime to a `Session`, and a `Principal` is exactly the kind of object worth keeping
and logging after that session has moved on.

`nptc.auth.principal.principal_for(session, resolution, *, claims, mfa_acr_values)`
derives one from issue #43's `Resolution`:

| Resolution / status | Result |
|---|---|
| `MANUAL_LINK_REQUIRED` | `ManualLinkRequiredError` (409) — never degrades to anonymous |
| `status == CLOSED` | `AccountClosedError` (403) — fail-closed backstop, practically unreachable |
| `status == SUSPENDED` | Exactly `ROLE_PERMISSIONS[ANON]` — the public read surface a stranger has, nothing more |
| `status == ACTIVE` | `ROLE_PERMISSIONS[ANON]` ∪ the union over granted, MFA-effective roles |

`ANONYMOUS: Principal` is the constant for the unauthenticated visitor (PRD §4.1).

## The check API

`nptc.auth.authorisation`:

- `has_permission(principal, permission) -> bool`
- `require_permission(permission) -> Callable[[Principal], Principal]` — raises
  `PermissionDeniedError` (403) or `MfaRequiredError` (403, see below) otherwise. A plain
  callable, not a FastAPI dependency — this issue is deliberately pure-library (see
  ADR-0016's "Scope" and ADR-0019). The one-line adapter a future app needs:

  ```python
  def permission_dep(permission: Permission):
      def dep(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
          return require_permission(permission)(principal)

      return dep
  ```

- `may_act_on(principal, *, own, any_, owner_user_id) -> bool` and
  `require_ownership_or_permission(...)` — the own/any resolution.
- `resolve_quota(principal, *, override=None)` — `effective_quota`, defined but not
  enforced (see above).

Errors live in `nptc.auth.errors_authorisation`, deliberately apart from
`nptc.auth.errors.TokenError` (whose docstring states every member is 401-shaped — a
future router relies on `except TokenError → 401`):

| Error | HTTP | Meaning |
|---|---|---|
| `PermissionDeniedError` | 403 | Missing permission |
| `MfaRequiredError` (subclass) | 403 | Permission would be granted by a role suppressed for want of MFA |
| `AccountClosedError` | 403 | Fail-closed backstop |
| `ManualLinkRequiredError` | 409 | Ambiguous/untrusted identity resolution |
| `LastAdministratorError` | 409 | FR-01: would leave zero active administrators |

`http_status` is a `ClassVar` on the shared `AuthorisationError` base, so a blanket
exception handler cannot mis-map the two 409s as 403.

## Granting and revoking roles

`nptc.auth.grants` is the one module in this set that takes a `Session`:

- `grant_role(session, *, granter, target_user_id, role, audit)` — requires
  `ROLE_GRANT_ANY`, unless `role is MEMBER` and the target currently holds `PROVISIONAL`
  (then `ROLE_GRANT_MEMBER` suffices). Idempotent.
- `revoke_role(session, *, revoker, target_user_id, role, audit)` — requires
  `ROLE_GRANT_ANY` unconditionally (Reviewer holds no revocation power at all). Runs
  `assert_not_last_administrator` before touching `Role.ADMINISTRATOR`.
- `grant_role_unchecked`/`revoke_all_roles_unchecked` — no `Principal`, no permission
  check, still audited. Used only by the bootstrap CLI and `_create_user`'s default
  Provisional grant (`grant_role_unchecked`), and `close_account`
  (`revoke_all_roles_unchecked`).

### FR-01: the last-administrator guard

`assert_not_last_administrator(session, *, removing_user_id)` locks every `user_role` row
naming `'administrator'` for an *active* user:

```sql
SELECT ur.id FROM user_role ur JOIN app_user u ON u.id = ur.user_id
 WHERE ur.role = 'administrator' AND u.status = 'active' FOR UPDATE OF ur
```

This is an application check, not a database constraint — Postgres cannot express
"at least one row across the whole table" as a `CHECK`/`UNIQUE`/`EXCLUDE`, and PRD §14.1 /
ADR-0011 forbid business logic in triggers. The row lock is what makes two concurrent
revocations of the last two administrators resolve to exactly one survivor rather than
zero: a naive `SELECT count(*)` would let both transactions independently see "one other
remains" and both commit. `test_grants.py`'s concurrency test proves this with two real
threads and two real connections.

**A privilege detail worth knowing.** Postgres requires *some* `UPDATE` privilege on a
table before it honours `SELECT ... FOR UPDATE` at all — confirmed against a real
container while building this (see ADR-0019's "A real privilege surprise"). `user_role`
therefore grants column-level `UPDATE (granted_at)` only; `user_id`/`role`/
`granted_by_user_id` remain immutable at the privilege level.

**Closure is a removal path too.** `nptc.auth.identity.close_account` runs the guard
before tombstoning the user, then revokes every grant — without this, closing your own
account would bypass FR-01. Suspending the last administrator is refused for the same
reason (only `status = 'active'` holders count toward the floor).

### Bootstrapping the first administrator

There is no grant/revoke HTTP endpoint in this issue's scope, so a fresh deployment
cannot reach the checked path at all — no `Principal` yet holds `ROLE_GRANT_ANY`.
`scripts/grant_role.py` is the deliberate, one-time operator escape hatch:

```powershell
uv run python scripts/grant_role.py --username <username> --role administrator
```

See [`docs/operations/upgrade.md`](../operations/upgrade.md) for the full runbook.

## NFR-06: mandatory MFA for administrators

The token carries no `acr`/`amr` and the realm declares no roles (ADR-0014), so Keycloak
cannot itself know who is an administrator — enforcement is necessarily server-side.
Keycloak's job is only to make step-up authentication achievable and provable.

**Realm** (`deploy/keycloak/realm/nptc-realm.json`): `browserFlow: "nptc browser"`, a
copy of the built-in browser flow whose `forms` subflow gates OTP behind a
`conditional-level-of-authentication` executor (`loa-condition-level: "2"`), not
`conditional-user-configured` — OTP is required whenever the client requests a
satisfying `acr_values`, not merely when the user happens to already have TOTP
configured. Verified against a real, disposable Keycloak 26.7.1 container: the realm
imports cleanly, and a full browser authorization-code request with `acr_values=2`
against a user with no TOTP redirects to `CONFIGURE_TOTP` exactly as designed.

**Claims**: `OidcIdentityClaims.acr`/`auth_time` (both optional, added without breaking
any existing constructor call site), narrowed in `TokenVerifier.verify` exactly as
`email`/`preferred_username` already are. These are authentication facts (how/when the
user authenticated), never authorisation claims — reading them here is required by the
same discipline that forbids any other module re-parsing the token
(`test_token_verification_guard.py`'s rule 5, which also forbids `realm_access`/
`resource_access`/`groups`/`roles`/`scope` anywhere outside `tokens.py`).

**Enforcement, two layers, both fail-closed:**

1. **Structural** — `principal_for` drops `Role.ADMINISTRATOR` from the effective role
   set whenever `mfa_satisfied` is `False`, recording it in `mfa_suppressed_roles`. NFR-06
   holds even at a check site that forgot about MFA entirely.
2. **Diagnostic** — `require_permission` raises `MfaRequiredError`, not a bare denial,
   when a suppressed role would have granted the permission. A future adapter should
   render this as an RFC 9470 step-up challenge:
   `403` + `WWW-Authenticate: Bearer error="insufficient_user_authentication", acr_values="2"`.

`MFA_REQUIRED_PERMISSIONS` is derived as exactly `ADMINISTRATOR_ONLY` (every permission
only Administrator holds), never hand-listed — a new admin-only permission inherits the
requirement automatically.

**Configuration**: `AuthSettings.mfa_acr_values` (`NPTC_MFA_ACR_VALUES`, default `{"2"}`)
must match the realm's `loa-condition-level`.

**What is not yet closed.** The SPA must still request `acr_values` on login and handle
an `insufficient_user_authentication` challenge for the loop to close end to end — a
frontend follow-up issue. Full "the token's `acr` claim reads `2` after OTP completion"
was verified manually against a real container rather than automated (scripting a TOTP
enrolment round-trip end to end was judged disproportionate effort for this issue); the
automated coverage proves the flow binds and its executor resolves correctly on import.

## FR-80 and FR-81: provable without a single endpoint

Both are worded per-endpoint. Since issue #41 `backend/src/nptc/api/` serves one real
route (`GET /api/v1/auth/me`), and `test_api_error_mapping.py` exercises the
`permission_dep` adapter over HTTP. Both are also, independently, statements about data,
which is the stronger form:

- **FR-80**: `not (ROLE_PERMISSIONS[OBSERVER] & WRITE_PERMISSIONS)` — stronger than any
  per-endpoint test, and it fails the day a write permission is ever added to Observer.
- **FR-81**: `ROLE_PERMISSIONS[REVIEWER] & ADMINISTRATOR_ONLY == frozenset()`,
  parametrised over PRD §4.5's withheld-capability list plus the non-obvious seventh
  (`SUBMISSION_WITHDRAW_ANY` — a Reviewer may withdraw only their own submission, never
  any).

## The negative-authorisation harness

Three layers, in `backend/tests/`:

1. **`authz_support.assert_permission_refused(principal, permission)`** — library-level.
   Asserts the raise, that the message names only the permission (never a role or the
   internal UUID), and — critically — that the permission is genuinely held by *some*
   role, so a typo'd constant cannot make the helper pass forever without checking
   anything real.
2. **`authz_app_support.build_authz_test_app`/`assert_http_forbidden`** — HTTP-level,
   against a throwaway `FastAPI()` app (the only `fastapi` import in this issue's
   scope). Proves 403-with-no-leakage and the 401-vs-403 pair real endpoints most
   reliably get backwards, exercisable today with zero real routes.
3. **`route_inventory_support.mutating_routes`/`assert_inventory_covers_every_mutating_route`**
   — fails in both directions (an uncovered mutating route, or a covered entry naming a
   route that no longer exists). Proven meaningful today via a positive-control synthetic
   app; intended to be shared with issue #165's own route-table inventory test.

## Requirement status

- **FR-44**: `implemented` — the matrix test and the AST guard
  (`test_authorisation_guard.py`) are both mechanical, durable proofs.
- **NFR-20, FR-01, FR-80, FR-81**: `in-progress` — all four are worded
  per-request/per-endpoint, and there are zero endpoints yet. The permission-level
  property tests here are stronger than any per-endpoint test, but the issue's own
  acceptance criterion is vacuously true at zero endpoints; the route inventory test
  (with its positive control) holds that debt honestly.
- **NFR-06**: `in-progress` — server refusal and realm step-up flow done and verified
  against a real container; the SPA-side request/challenge handling is a follow-up issue.
