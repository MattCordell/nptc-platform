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

## `audit_event`

The minimal table NFR-08 will eventually be built on. `prev_hash`/`entry_hash` (NFR-10, the
hash chain) and `verify_audit_chain.py` land with #36; the append-only re-assertion after a
downgrade/upgrade round-trip (part of #35's acceptance criteria) is already covered here by
the reflection fingerprint in `backend/tests/test_db_round_trip.py`, which folds
`information_schema.role_table_grants` into the comparison for exactly that reason.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` (core since PG13 - no `pgcrypto` extension needed) |
| `sequence` | `BIGINT` | `GENERATED ALWAYS AS IDENTITY`, unique - see the identity-vs-serial note above |
| `occurred_at` | `TIMESTAMPTZ` | `NOT NULL`, server-assigned `now()` |
| `actor_user_id` | `UUID` | Nullable, no FK - the user table lands with #42 |
| `actor_ip` | `INET` | Nullable |
| `user_agent` | `TEXT` | Nullable |
| `correlation_id` | `UUID` | `NOT NULL` |
| `action` | `TEXT` | `NOT NULL` |
| `entity_type` | `TEXT` | `NOT NULL` |
| `entity_id` | `TEXT` | `NOT NULL` |
| `before` | `JSONB` | Nullable |
| `after` | `JSONB` | Nullable |
| `reason` | `TEXT` | Nullable |

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
| `index_seq` | `BIGINT` | `GENERATED ALWAYS AS IDENTITY`, used only to build a truncation-proof generated index name (never the property key) |
| `key` | `TEXT` | `UNIQUE`, `CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$')`, immutable (FR-12) |
| `label` | `TEXT` | Human-facing, changeable |
| `datatype` | `TEXT` | No CHECK, no ENUM - FR-77's handler-module extension point |
| `cardinality` | `TEXT` | CHECK against `0..1` / `1..1` / `0..*` / `1..*` |
| `scope` | `TEXT` | CHECK against `submission` / `maintenance` / `both` |
| `required_for_submission` | `BOOLEAN` | |
| `required_for_publication` | `BOOLEAN` | |
| `binding_target` | `TEXT` | `value_set` or `local_code_system`; `NULL` unless `datatype = 'code'` (FR-10) |
| `value_set_uri` | `TEXT` | Required when `binding_target = 'value_set'` |
| `strength` | `TEXT` | `required` / `extensible` / `example` |
| `edition` | `TEXT` | SNOMED edition the value set resolves against |
| `constraints` | `JSONB` | Handler-owned datatype parameters (#137 owns its interior) |
| `filterable` | `BOOLEAN` | Drives #54's index generation (FR-13) |
| `origin` | `TEXT` | `system` or `admin_defined` |
| `status` | `TEXT` | `active` or `deprecated` - no delete (FR-11) |
| `display_order` | `INTEGER` | |
| `deprecated_at` | `TIMESTAMPTZ` | Nullable |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |
| `row_version` | `INTEGER` | Cache key (with `key`) for #52's in-process JSON Schema memoisation |

`property_value` is one row per value: `(entry_id, property_key, value JSONB, ordinal)` plus
`justification` (FR-10's extensible-strength case), with `UNIQUE (entry_id, property_key,
ordinal)` and an FK on `property_key` to `property_definition(key)` - not a surrogate id, so
FR-11/FR-12 are referential integrity rather than application logic. `property_value.entry_id`'s
FK to `catalogue_entry` cannot be added until `catalogue_entry` lands with #46-#48; #51 tracks
this as an open follow-on migration, not a silently dropped constraint.

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
