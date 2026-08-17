# Database migrations and upgrade notes

Covers running Alembic migrations against a real deployment and the out-of-band steps an
operator does that migrations deliberately do not automate. See
[`data-model.md`](../architecture/data-model.md) for the schema itself, and ADR-0011 for
the reasoning behind the decisions summarised here.

## Running migrations

```powershell
uv run alembic upgrade head
uv run alembic current
uv run alembic downgrade -1     # one revision back
```

Run from the repository root - `[tool.alembic]` in the root `pyproject.toml` resolves
`script_location` relative to that file's own directory (`%(here)s`), not the process's
working directory, so this is the one place these commands must be run from.

Alembic needs exactly one environment variable to run outside a test: `NPTC_MIGRATION_
DATABASE_URL`, a DSN for a role that owns the schema (able to `CREATE EXTENSION`,
`CREATE ROLE`, `GRANT`/`REVOKE`, and create/alter tables) - never the least-privilege
`nptc_app` runtime role, which cannot do any of that by design. See
[`configuration.md`](configuration.md) for both database DSNs this stack reads.

**`CREATE EXTENSION` needs superuser** (or a role explicitly granted `CREATE` on the
database, in a managed Postgres offering that restricts real superuser). The owning role
used for `NPTC_MIGRATION_DATABASE_URL` must have this - `0001_extensions_and_app_role.py`
installs `pg_trgm` and `unaccent` and will fail with a permission error otherwise.

## Provisioning the app role's login

Migrations create the `nptc_app` role (`NOLOGIN`) and grant it the privileges the
application needs - they deliberately do **not** create a `LOGIN` role or set a password
anywhere (NFR-26: no secrets committed to the repository). An operator provisions the
actual login role once, out-of-band, after the migration has run:

```sql
CREATE ROLE nptc_app_login LOGIN PASSWORD '<a real, generated secret>';
GRANT nptc_app TO nptc_app_login;
```

`NPTC_DATABASE_URL` (the application's own runtime DSN) then authenticates as
`nptc_app_login`. `backend/tests/conftest.py` reproduces exactly this two-step sequence
inside the disposable test container, with an obviously-synthetic local-only password -
never real credentials, and never anything committed.

## The asymmetric downgrade

`0001_extensions_and_app_role.py`'s `downgrade()` drops both extensions but **does not**
`DROP ROLE nptc_app`. This is deliberate, not an oversight: roles are cluster-wide, not
per-database, so dropping a role that still holds privileges in any other database sharing
the cluster fails - which would make `downgrade base` fail outright on a shared cluster (the
normal case in any real deployment, and even in this repo's own round-trip test, which runs
a dedicated database in the *same* container as the rest of the test suite). A role is not
schema, so this doesn't compromise the round-trip criterion (schema equality after
`downgrade base` → `upgrade head`) - only that a stale, unused role can be left behind in
the cluster after a full downgrade. If a role genuinely needs to be removed, that is a
manual operator step (`DROP ROLE nptc_app;`, after confirming it holds nothing elsewhere in
the cluster), not something a migration can safely automate.

The same downgrade also leaves `GRANT USAGE ON SCHEMA public TO nptc_app` in place - it is
a schema-level grant, not a table-level one, and revoking it isn't necessary for the same
reason dropping the role isn't: the role persisting with a stray schema grant is harmless,
and the alternative (`REVOKE USAGE ... ; downgrade base` re-`GRANT`-ing it on every
`upgrade head`) buys nothing. This is invisible to the round-trip fingerprint
(`backend/tests/test_db_round_trip.py`), which only reflects
`information_schema.role_table_grants` (table-level), not schema-level grants - noted here
rather than left for a future reader to notice the gap unassisted.

## `0003_user_and_user_identity.py`

Adds `app_user` and `user_identity` (issue #42, ADR-0015) and a new FK,
`audit_event.actor_user_id -> app_user.id`. `downgrade()` drops that FK **first**,
then `user_identity`, then `app_user` - the reverse of creation order, since a
foreign key must be dropped before the table it references can be. Its privilege
grants and revokes (see [`data-model.md`](../architecture/data-model.md#user-and-user_identity))
live in this same migration, following the same reasoning as `0002_audit_event.py`
above.

## Testcontainers and Docker

`uv run pytest` from the repository root now needs a **running** Docker daemon, not merely
an installed one - `backend/tests` runs every test against a real, containerized Postgres
(NFR-39). Docker (with Compose) was already a declared prerequisite
([CONTRIBUTING.md](../../CONTRIBUTING.md)); this is the same requirement, just now
exercised by the test suite as well as the local stack.
