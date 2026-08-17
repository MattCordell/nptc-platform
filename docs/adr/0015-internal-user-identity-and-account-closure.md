# ADR-0015: Internal user identity and account closure

**Status:** Accepted
**Date:** 2026-08-17

## Context

Keycloak authenticates through the realm ADR-0014 landed, but nothing yet stores who
those authenticated people *are* to the platform. Issue #42 creates that layer: an
internal `app_user` record with a stable UUID, and a `user_identity` table linking it to
the OIDC `(iss, sub)` pair. `P1-SEQUENCING.md` makes this a hard gate - #42 before #44,
#44 before any write path - because every submission, interest record and audit event to
come references the internal `user.id`, never the identity provider's `sub` claim.

Three non-functional requirements drive every decision below:

- **NFR-04** - application data must never be keyed on the IdP's `sub`. Adding a second
  federated identity provider later must be a row insert into `user_identity`, not a
  migration of every foreign key in the schema.
- **NFR-05** - auto-linking an external identity to an existing account only when the
  issuer is explicitly trusted *and* the claim asserts `email_verified`. The failure mode
  this exists to prevent is blunt: auto-linking on an unverified email claim means anyone
  who can mint a token asserting an administrator's email inherits that administrator's
  privileges.
- **NFR-17** - account closure pseudonymises rather than deletes, so audit attribution
  and the NFR-10 hash chain (landing with #36) stay verifiable against a row that still
  exists.

**Scope**, confirmed with the maintainer: persistence plus domain services only. There is
no `session.py`, no FastAPI app and no API in this issue - #41/#43/#44 own those. Every
function added here takes its unit of work (a `Session`) as an argument; nothing
constructs an engine or a sessionmaker.

## Decision

**Physical table name is `app_user`, not `"user"`.** `"user"` is a reserved word in
Postgres - every literal in `roles.py`, migrations and tests would need quoting, and an
unquoted `FROM user` is a `current_user` trap waiting for a future contributor. NFR-04
fixes the *shape* of identity (an internal UUID, never the IdP's `sub`), not the specific
identifier chosen for the table, so there is no requirement forcing the reserved word.

**`status` is `TEXT` + `CHECK`, not a native `ENUM`.** `ALTER TYPE ... ADD VALUE` cannot
run inside a transaction, and Alembic autogenerate mishandles the create/drop-type pair
on a downgrade. `property_definition.status` in `data-model.md` already sets this
precedent; `app_user.status` follows it for the same reason.

**No `role` column.** Including one here creates a second place a role can be granted,
and FR-44 requires every authorisation check to be against a permission, never a role
name - a `role` column on `app_user` would be a standing temptation to check it directly.
Role grants are deferred to #44 in full.

**Tombstone shape: identifying fields go to `NULL`, enforced by a `CHECK`, not a synthetic
sentinel.** A sentinel like `closed-user-<uuid>` would leak the internal UUID into a
column NFR-04 says should never carry it in an externally-visible form, and would still
need its own uniqueness handling. Postgres's `UNIQUE` constraint is `NULLS DISTINCT` by
default, so nulling `username` on closure gives unlimited closed accounts for free with
no extra mechanism - `test_two_closed_accounts_can_coexist_with_null_usernames` proves
this rather than assuming it. The CHECK
(`(status = 'closed' AND username/display_name/organisation IS NULL) OR (status <>
'closed' AND username/display_name IS NOT NULL)`) is what makes NFR-17 a database
invariant instead of an application convention: a half-done tombstone (closed but still
carrying a name) is rejected at the database, not merely discouraged in code review.

**"Never delete a user" is expressed as a missing `DELETE` grant, not solely an
application convention.** `nptc_app` gets `SELECT`, `INSERT`, and a **column-level**
`UPDATE` on `app_user` excluding `id` and `created_at` - so the retained UUID is
immutable even to the app role itself, which is exactly what audit attribution and the
eventual NFR-10 hash chain depend on - and no `DELETE` grant at all. This mirrors the
argument NFR-09 already makes for `audit_event`: a privilege refusal is provable by a
failing statement, an application-level "we just never call delete" is not.

**`audit_event.actor_user_id` gets a real FK to `app_user.id`, still nullable.** Leaving
it unconstrained (as `0002_audit_event.py` shipped it, with a placeholder comment) would
let an audit row reference a UUID that was never a real user. The FK stays nullable
because a system-initiated event has no human actor at all.

**`resolve_user_for_claims` returns a result object, never raises for control flow.**
`LinkOutcome` (`EXISTING`/`CREATED`/`AUTO_LINKED`/`MANUAL_LINK_REQUIRED`) plus a
`Resolution` dataclass gives #43 a value to map to an HTTP response without a
try/except ladder mixing genuine errors with "this needs a human to resolve."

**`UserRef` is a structural NFR-04 boundary, not a code-review reminder.** A frozen
Pydantic model carrying `username`/`display_name`/`organisation`/`status` and
deliberately no `id` field gives #43/#142/#143 a type to route every user-shaped API
response and export field through, rather than relying on a reviewer remembering that the
UUID must never serialise. Its own test includes a positive control (a payload that
deliberately does leak a UUID) so the leak-detection assertion itself cannot rot into an
always-pass.

**`close_account` deletes every `user_identity` row for the user but does not emit an
audit event.** There is no audit writer until #36 lands; stubbing one here would either
be dead code or would need its own migration path once #36 defines the real event shape.
The docstring and this ADR say so explicitly rather than leaving a silent gap for a
reviewer to notice unassisted.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| `"user"` as the table name | Reserved word; every literal reference needs quoting, and an unquoted `FROM user` silently resolves to `current_user` instead of failing to parse. |
| Native `ENUM` for `status` | `ALTER TYPE ADD VALUE` can't run in a transaction, and Alembic autogenerate mishandles the type create/drop pair on downgrade - `TEXT` + `CHECK` is the established precedent in this schema. |
| A `role` column on `app_user` | Creates a second place a role is granted; FR-44 forbids role-name checks in the first place, so the column would only invite the anti-pattern it exists to prevent. |
| A synthetic tombstone sentinel (`closed-user-<uuid>`) | Leaks the internal UUID into a column meant to be externally meaningless, and still needs its own uniqueness story - `NULL` plus `NULLS DISTINCT` solves both for free. |
| Relying on application code alone to never delete a user | Not provable the way a privilege refusal is; NFR-09 already established the pattern of enforcing "never" at the grant level for `audit_event`, and this issue extends it to `app_user`. |
| Raising exceptions from `resolve_user_for_claims` for the manual-link case | Manual-link-required is an expected outcome of NFR-05's policy, not an error - a result object lets #43 map it to a 409-style response without conflating it with a genuine failure. |
| Stubbing an audit event inside `close_account` | There is no audit writer until #36 defines the real event shape; a stub here would be dead code today and possibly the wrong shape once #36 lands. |

## Consequences

- #43 (server-side JWT verification) and #44 (role model, permission framework) both
  build directly on this: #43 produces an `OidcIdentityClaims` from a verified token and
  calls `resolve_user_for_claims`; #44 adds the `role` grant this issue deliberately
  omitted.
- **Documented, intentional consequence of closure:** because `close_account` deletes the
  `user_identity` row rather than marking it closed, the same OIDC subject logging in
  again after closure resolves to a *brand new* user with a *new* UUID - it does not, and
  cannot, resolve back to the tombstoned account. The acceptance criterion is "can no
  longer authenticate into the tombstoned user," which this satisfies; disabling the
  account on the Keycloak side (so the subject can't get a token at all) is a separate,
  operator/#41-era concern, not something this issue's persistence layer can reach.
- OI-15 (whether pseudonymisation as implemented here discharges the Australian Privacy
  Principle 11.2 obligation) is recorded here as **still open** - an engineering
  invariant (the CHECK, the missing grant) is not the same thing as a compliance
  determination, and this ADR does not claim to resolve it.
- NFR-04, NFR-05 and NFR-17 move to `implemented` in `requirements.yaml`. NFR-04's notes
  flag the follow-on sweep needed once real response models and export renderers exist,
  to confirm every one of them routes through `UserRef` rather than a user's own fields.
