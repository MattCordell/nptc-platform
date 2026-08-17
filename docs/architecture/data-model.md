# Data model

The database baseline landed with issue #33 (ADR-0011) - Alembic migrations targeting real
SQLAlchemy metadata, the `pg_trgm`/`unaccent` extensions #138's search depends on, a
least-privilege role model, and a testcontainers integration harness. Everything below
describes what exists today; later issues (#35, #36, #42, #46-#48, #51-#55, #138) extend
it rather than replace it.

## Migration layout

| File | Responsibility |
|---|---|
| `backend/migrations/env.py` | Resolves the migration connection, targets `Base.metadata`, `compare_type=True` |
| `backend/migrations/script.py.mako` | Repo-local revision template (see below) |
| `backend/src/nptc/db/base.py` | `NAMING_CONVENTION`, `MetaData`, `Base(DeclarativeBase)` |
| `backend/src/nptc/db/models/__init__.py` | Import-aggregator so `Base.metadata` is complete for autogenerate |
| `backend/src/nptc/db/models/audit.py` | The `audit_event` table |
| `backend/src/nptc/db/models/user.py` | The `app_user` table (issue #42) |
| `backend/src/nptc/db/models/user_identity.py` | The `user_identity` table (issue #42) |
| `backend/src/nptc/db/roles.py` | `APP_ROLE` and every grant/revoke SQL statement, imported by both the migration that applies them and the tests that assert them |

Alembic's configuration lives in the root `pyproject.toml` as `[tool.alembic]`, not a
`backend/alembic.ini` - see ADR-0011 for why a relative `script_location` there would break
every `uv run alembic ...` invocation. Running migrations (as an operator, not a test) is
covered in [`upgrade.md`](../operations/upgrade.md).

`backend/migrations/script.py.mako` replaces the stock template with
`from __future__ import annotations`, `collections.abc.Sequence`, and `str | None` instead
of `typing.Sequence`/`typing.Union`, plus `[[tool.alembic.post_write_hooks]]` running
`ruff check --fix` then `ruff format` against every newly generated revision - so a
generated migration is clean before it is ever committed, not after a human remembers to
run ruff by hand.

## Naming convention

`nptc.db.base.NAMING_CONVENTION` gives every constraint and index a deterministic,
autogenerate-produced name (`pk_<table>`, `uq_<table>_<column>`, `ck_<table>_<name>`,
`fk_<table>_<column>_<referred_table>`, `ix_<column_label>`) instead of Postgres's own
anonymous or driver-dependent defaults. Without this, the same model can autogenerate a
different constraint name on two separate runs depending on declaration order, which
breaks a downgrade's ability to reliably find the constraint to drop.

**Postgres identifier truncation caveat:** Postgres silently truncates any identifier
(table, column, constraint, index name) longer than 63 bytes. The naming convention above
can still produce a name past that limit on a long table or column name combination. #54's
automatic index generation (properties marked filterable, FR-13) is **no longer** the likely
first victim: ADR-0012 fixes its index names as `ix_propval_p{index_seq}_{slot}`, never
composed from the property key, provably at most 33 bytes for any 64-bit `index_seq` value.
The warning stands for every other future long name; treat a migration that silently drops
characters from a generated name as a defect to raise against whichever issue introduced it.

## Role and privilege model

One least-privilege application role, `nptc_app` (`NOLOGIN` - nothing ever authenticates
directly as it; a `LOGIN` role is granted membership in it instead - see
[`upgrade.md`](../operations/upgrade.md) for the operator-side provisioning step, and
`backend/tests/conftest.py`'s `nptc_app_login` for the equivalent inside the test harness).

For `audit_event` specifically: `GRANT SELECT, INSERT ... TO nptc_app`, with an explicit
(belt-and-braces) `REVOKE UPDATE, DELETE, TRUNCATE ... FROM nptc_app` alongside it. Nothing
ever grants `ALL` on this table - `nptc.db.roles` never spells that statement, and
`backend/tests/test_sql_parameterisation.py`'s NFR-22 guard fails outright on any migration
that does (rule 3). `TRUNCATE` is a distinct, owner-only Postgres privilege not implied by
`DELETE` - but it *is* included in `GRANT ALL`, which is exactly why that shorthand is
banned here rather than merely discouraged.

**Identity, not `serial`, for `audit_event.sequence`.** An identity column
(`GENERATED ALWAYS AS IDENTITY`) is an internal dependency of the column and its backing
sequence is not ACL-checked against the inserting role, so `INSERT` on the table alone is
sufficient. A `serial` default is a plain `nextval(...)` evaluated with the *inserting*
role's own privileges, and would silently need a separate
`GRANT USAGE ON SEQUENCE ... TO nptc_app` - the classic thing forgotten on a re-migration.
This is proven empirically by `backend/tests/test_db_audit_privileges.py`
(`test_app_role_can_insert_and_select`), not assumed: if it were wrong, the insert would
fail immediately with `42501` and the fix is one `GRANT`.

Grants live in the **same migration that creates the table**
(`0002_audit_event.py`), never a later "permissions" migration: table ACLs
(`pg_class.relacl`) are cluster state that lives and dies with the table itself, so a
separate migration would leave a re-created table grant-less after a
`downgrade base` → `upgrade head` round-trip.

The refusals above are asserted twice: once against a freshly migrated database
(`backend/tests/test_db_audit_privileges.py`) and once against a database that has been
through a full `downgrade base` -> `upgrade head` round-trip
(`backend/tests/test_db_round_trip.py`'s `test_app_role_is_still_refused_*_after_round_trip`
tests) - see the `audit_event` section below for why the round-trip's own reflection
fingerprint isn't sufficient to make that second assertion on its own.

For `app_user` (issue #42): `GRANT SELECT, INSERT` plus a **column-level**
`GRANT UPDATE (username, display_name, organisation, status, closed_at, updated_at)` -
excluding `id` and `created_at`, so the retained UUID is immutable even to the app role
itself - and an explicit `REVOKE DELETE, TRUNCATE`. There is no path by which `nptc_app`
can delete a row from this table; NFR-17's "pseudonymise, never delete" is a database
invariant, not an application convention. For `user_identity`: ordinary
`SELECT, INSERT, UPDATE, DELETE` (closing an account deletes its identity rows outright -
there is no tombstone shape for a link row) with `TRUNCATE` revoked.
`backend/tests/test_db_round_trip.py`'s fingerprint queries
`information_schema.column_privileges` as well as `role_table_grants`, specifically so the
column-level `app_user` grant is not silently invisible to the round-trip check.

## `audit_event`

The minimal table NFR-08 will eventually be built on. `prev_hash`/`entry_hash` (NFR-10, the
hash chain) and `verify_audit_chain.py` land with #36. The append-only re-assertion after a
downgrade/upgrade round-trip (part of #35's acceptance criteria) is covered in two parts:
`backend/tests/test_db_round_trip.py`'s reflection fingerprint folds
`information_schema.role_table_grants` into its comparison, which catches a grant
*changing* across the round-trip - but `before`/`after` are both taken from the same
database, so a grant missing in both compares equal and passes, meaning the fingerprint
alone cannot catch a grant that silently *disappeared* on re-migration. That absence case is
what the same file's `test_app_role_is_still_refused_update_after_round_trip`,
`..._delete_after_round_trip` and `..._truncate_after_round_trip` tests assert instead: real
UPDATE/DELETE/TRUNCATE statements, run as `nptc_app_login` (never a superuser connection),
against the schema produced by an actual `downgrade base` -> `upgrade head` round-trip.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` (core since PG13 - no `pgcrypto` extension needed) |
| `sequence` | `BIGINT` | `GENERATED ALWAYS AS IDENTITY`, unique - see the identity-vs-serial note above |
| `occurred_at` | `TIMESTAMPTZ` | `NOT NULL`, server-assigned `now()` |
| `actor_user_id` | `UUID` | Nullable FK to `app_user.id` (issue #42) - null for a system-initiated event |
| `actor_ip` | `INET` | Nullable |
| `user_agent` | `TEXT` | Nullable |
| `correlation_id` | `UUID` | `NOT NULL` |
| `action` | `TEXT` | `NOT NULL` |
| `entity_type` | `TEXT` | `NOT NULL` |
| `entity_id` | `TEXT` | `NOT NULL` |
| `before` | `JSONB` | Nullable |
| `after` | `JSONB` | Nullable |
| `reason` | `TEXT` | Nullable |

## `user` and `user_identity`

Landed with issue #42 (ADR-0015). An internal `app_user` record with a stable UUID is
what every future submission, interest record and audit event references - never the
IdP's `sub` claim (NFR-04). `user_identity` links it to a verified OIDC `(iss, sub)` pair;
one user can hold more than one linked identity.

**Why `app_user`, not `"user"`.** `"user"` is a reserved word in Postgres (and an
unquoted `FROM user` is a `current_user` trap) - every literal in `roles.py`, migrations
and tests would need quoting for a name NFR-04 never actually required. NFR-04 fixes the
*shape* of identity (an internal UUID, never the IdP's subject), not this identifier.

`app_user`:

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` |
| `username` | `TEXT` | Nullable, `UNIQUE` (`NULLS DISTINCT` - see the tombstone note below) |
| `display_name` | `TEXT` | Nullable |
| `organisation` | `TEXT` | Nullable |
| `status` | `TEXT` | `NOT NULL`, `CHECK IN ('active','suspended','closed')`, default `'active'` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |
| `closed_at` | `TIMESTAMPTZ` | Nullable, `CHECK (status = 'closed') = (closed_at IS NOT NULL)` |

No `role` column: adding one here would create a second place a role is granted, and
FR-44 requires permission checks, never role-name checks. Role grants land with #44.

**The tombstone CHECK is what makes NFR-17 a database invariant.** A row cannot be
`closed` while `username`/`display_name`/`organisation` still carry a value, and cannot
be non-`closed` without a `username` and `display_name`. Closing an account nulls those
three columns rather than deleting the row, which is only safe because Postgres's
`UNIQUE` constraint on `username` is `NULLS DISTINCT` by default - every closed account's
`NULL` username coexists with every other closed account's `NULL` username. **Never add
`NULLS NOT DISTINCT` to this constraint** - it would cap the platform at exactly one
closed account cluster-wide.

`user_identity`:

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` |
| `user_id` | `UUID` | `NOT NULL`, FK to `app_user.id` (no `ON DELETE` - users are never deleted), indexed |
| `issuer` | `TEXT` | `NOT NULL`, `CHECK (length(btrim(issuer)) > 0)` |
| `subject` | `TEXT` | `NOT NULL`, `CHECK (length(btrim(subject)) > 0)` |
| `email` | `TEXT` | Nullable |
| `email_verified` | `BOOLEAN` | `NOT NULL`, default `false` |
| `linked_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |

`UniqueConstraint("issuer", "subject")` - one `(iss, sub)` pair links to exactly one
user. `NAMING_CONVENTION` names composite unique constraints from their **first** listed
column only (`column_0_name`), so this constraint is `uq_user_identity_issuer`, not
`uq_user_identity_issuer_subject` - this is the naming convention working as designed,
not a bug to "fix" without changing every other multi-column unique/index name in the
schema.

**Auto-linking (NFR-05).** `nptc.auth.linking.may_auto_link` gates whether the
*incoming* `(iss, sub)` may be linked automatically: its issuer must be in an explicit,
exact-match trusted-issuer allowlist (`NPTC_TRUSTED_ISSUERS`, empty by default - fail
closed) *and* its `email_verified` must be `True` (`is True`, never merely truthy). That
alone is not sufficient - `nptc.auth.identity.resolve_user_for_claims`'s own candidate
query additionally requires the *matched* `user_identity` row's own issuer to be trusted
too. Without that second check, a first registration through any issuer at all (including
an untrusted one) could plant a verified email that a later, genuinely trusted login would
then auto-link into - trusting only the incoming side lets the untrusted side plant the
bait. If more than one existing user has a trusted, verified identity for the same email,
the match is ambiguous and resolves to manual-link-required rather than picking one via
undefined query order. There is deliberately no `app_user.email` column - matching is
against *verified identities* in `user_identity`, never a mutable, unverified field on the
user itself. See `resolve_user_for_claims`'s docstring for the full resolution (existing
identity, no candidate, auto-link, ambiguous/untrusted candidate, manual-link-required).

**Account closure** (`nptc.auth.identity.close_account`) nulls the three identifying
columns, sets `status = 'closed'`/`closed_at = now()`, and deletes every `user_identity`
row for that user - but never deletes the `app_user` row itself, which the privilege
grants below make structurally impossible regardless. Idempotent. Does **not** emit an
audit event (there is no audit writer until #36). Documented consequence: because the
identity row is gone, the same OIDC subject logging in again after closure creates a
*new* user with a *new* UUID - the AC is "can no longer authenticate into the tombstoned
user", which this satisfies; disabling the account on the Keycloak side is a separate
operator concern from the #41-era realm.

**`nptc.auth.identity.UserRef`** is the NFR-04 serialisation boundary: a frozen Pydantic
model carrying `username`/`display_name`/`organisation`/`status` and **no `id` field at
all**, so a future response model or export renderer routes through a type that cannot
leak the internal UUID, rather than relying on reviewer memory. Its own test
(`test_user_ref_excludes_internal_id.py`) includes a positive control proving the leak
check itself would fire on a payload that actually does leak the UUID.

## Property registry (design, lands with #51-#55)

**Not implemented yet.** This section describes the shape ADR-0012 fixes for #51
(`PropertyDefinition`/`PropertyValue`), #52 (JSON Schema validation), #54 (automatic index
generation) and #55 (deprecation/key immutability) to build against, the same way the tables
above describe what issue #33 actually shipped. See ADR-0012 for the full reasoning,
including the rejected alternatives (runtime DDL, classic EAV) and the FR-13 index executor's
still-open question.

`property_definition` is a conventional relational table, not a document:

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK |
| `index_seq` | `BIGINT` | `NOT NULL`, `GENERATED ALWAYS AS IDENTITY`, used only to build a truncation-proof generated index name (never the property key) |
| `key` | `TEXT` | `NOT NULL`, `UNIQUE`, `CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$')`, immutable (FR-12) |
| `label` | `TEXT` | `NOT NULL`. Human-facing, changeable |
| `datatype` | `TEXT` | `NOT NULL`. No CHECK, no ENUM - FR-77's handler-module extension point; the valid set is `DatatypeRegistry.known_datatypes()`, checked at write time, not a schema-level constraint (ADR-0013) |
| `cardinality` | `TEXT` | `NOT NULL`, CHECK against `0..1` / `1..1` / `0..*` / `1..*` |
| `scope` | `TEXT` | `NOT NULL`, CHECK against `submission` / `maintenance` / `both` |
| `required_for_submission` | `BOOLEAN` | `NOT NULL` |
| `required_for_publication` | `BOOLEAN` | `NOT NULL` |
| `binding_target` | `TEXT` | Nullable. `value_set` or `local_code_system`; `NULL` unless `datatype = 'code'` (FR-10) |
| `value_set_uri` | `TEXT` | Nullable. `CHECK` requires it when `binding_target = 'value_set'` |
| `strength` | `TEXT` | Nullable. `required` / `extensible` / `example` |
| `edition` | `TEXT` | Nullable. SNOMED edition the value set resolves against |
| `constraints` | `JSONB` | `NOT NULL DEFAULT '{}'`. Handler-owned datatype parameters; interior validated by each handler's `constraints_schema()` (ADR-0013) |
| `filterable` | `BOOLEAN` | `NOT NULL`. Drives #54's index generation (FR-13) |
| `origin` | `TEXT` | `NOT NULL`. `system` or `admin_defined` |
| `status` | `TEXT` | `NOT NULL`. `active` or `deprecated` - no delete (FR-11) |
| `display_order` | `INTEGER` | `NOT NULL` |
| `deprecated_at` | `TIMESTAMPTZ` | Nullable. `CHECK` ties it to `status = 'deprecated'` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `row_version` | `INTEGER` | `NOT NULL DEFAULT 1`. Cache key (with `key`) for #52's in-process JSON Schema memoisation - owned by exactly one write path, the ORM's `version_id_col` on this table's mapped `UPDATE` (see ADR-0012) |

`property_value` is one row per value, with **`(entry_id, property_key, ordinal)` as the
primary key** (not a surrogate id plus a separate `UNIQUE`) - `ordinal` `NOT NULL`,
`CHECK (ordinal >= 0)`, zero-based - plus `value JSONB NOT NULL` and `justification`
(nullable, FR-10's extensible-strength case). An FK on `property_key` to
`property_definition(key)`, not a surrogate id, gives a real but *conditional* backstop for
FR-11/FR-12 (it blocks deleting or renaming a definition only while a dependent value row
exists); the unconditional guarantee for both comes from the column-level privilege below,
not from this FK - see ADR-0012 for why the two must not be conflated. The PK's own
uniqueness on `ordinal` closes only the duplicate-ordinal race, not cardinality's upper
bound (a `0..1` property can still race two inserts at `ordinal` 0 and 1); #52 enforces the
upper bound at validation time. `property_value.entry_id`'s FK to `catalogue_entry` cannot be
added until `catalogue_entry` lands with #46-#48; #51 tracks this as an open follow-on
migration, not a silently dropped constraint.

`nptc_app` gets `UPDATE` at column level on `property_definition`, excluding `key`, `id`,
`index_seq`, `origin` and `created_at`, and no `DELETE` grant at all (FR-11's unconditional
form) - this is FR-12 as a database invariant, not an ORM convention. `property_value` gets
ordinary `SELECT`/`INSERT`/`UPDATE`/`DELETE`.

FR-13's generated indexes (`ix_propval_p{index_seq}_{slot}`, see the truncation caveat above)
are excluded from Alembic autogenerate and this file's own round-trip fingerprint via an
`include_object` hook in `env.py` - without it, the first index #54 creates would fail the
downgrade/upgrade comparison in `test_db_round_trip.py`.

## Extensions

`pg_trgm` and `unaccent` are created in `0001_extensions_and_app_role.py` - #138's search
depends on both. Both require Postgres superuser (or a role with `CREATEDB`/appropriate
grant) to install; see [`upgrade.md`](../operations/upgrade.md) for the operator-facing
note.

## Test harness

`backend/tests/conftest.py` runs every backend test against a real, containerized
Postgres pinned to the exact tag `deploy/compose.yml` specifies (NFR-39) - parsed at
runtime so there is exactly one pin, never `metadata.create_all` and never an in-memory
substitute. See ADR-0011 for the full fixture graph and the reasoning behind each piece
(the dedicated round-trip database, the genuinely separate `nptc_app_login`
authentication, why each privilege refusal gets its own test function, and the
`backend-integration` CI job that proves NFR-37 for this test tree).
