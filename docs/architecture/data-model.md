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
can produce a name past that limit on a long table or column name combination - #54's
automatic index generation (properties marked filterable, FR-13) is the most likely first
place this bites, since it composes a table name with a dynamically-named property. There
is no guard against this yet; treat a migration that silently drops characters from a
generated name as a defect to raise against whichever issue introduced the long name.

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
