# Database migrations and upgrade notes

Covers running Alembic migrations against a real deployment and the out-of-band steps an
operator does that migrations deliberately do not automate. This document owns
*operational* facts only - a precondition, a manual step, a non-obvious downgrade order.
See [`data-model.md`](../architecture/data-model.md) for the schema shape, and each
migration's own module docstring for the design rationale behind it (CONTRIBUTING.md's "A
schema change's prose has one home each") - a section below links to both rather than
restating them.

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

## Migration index

One row per revision. "Operator consequence: none" means there is nothing to do beyond
`upgrade head` - that migration's reasoning lives entirely in its own docstring and/or
`data-model.md`, so it gets no section of its own below.

| Revision | Adds | Operator consequence |
|---|---|---|
| [`0001_extensions_and_app_role.py`](../../backend/migrations/versions/0001_extensions_and_app_role.py) | `pg_trgm`, `unaccent`, the `nptc_app` role | Needs a superuser-equivalent DSN; see below and [The asymmetric downgrade](#the-asymmetric-downgrade) |
| [`0002_audit_event.py`](../../backend/migrations/versions/0002_audit_event.py) | `audit_event` (see [`data-model.md`](../architecture/data-model.md#audit_event)) | None |
| [`0003_user_and_user_identity.py`](../../backend/migrations/versions/0003_user_and_user_identity.py) | `app_user`, `user_identity` | See [below](#0003_user_and_user_identitypy) |
| [`0004_audit_event_hash_chain.py`](../../backend/migrations/versions/0004_audit_event_hash_chain.py) | `prev_hash`/`entry_hash` on `audit_event` | See [below](#0004_audit_event_hash_chainpy) |
| [`0005_user_role.py`](../../backend/migrations/versions/0005_user_role.py) | `user_role` | See [below](#0005_user_rolepy), plus first-administrator bootstrap |
| [`0006_catalogue_entry.py`](../../backend/migrations/versions/0006_catalogue_entry.py) | `catalogue_entry` (see [`data-model.md`](../architecture/data-model.md#catalogue_entry-issue-46-fr-03-fr-38)) | None |
| [`0007_designation.py`](../../backend/migrations/versions/0007_designation.py) | `designation` (see [`data-model.md`](../architecture/data-model.md#designation-issue-47-fr-04-fr-24-fr-37-fr-85)) | None - `downgrade()` drops the table outright |

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

## `0004_audit_event_hash_chain.py`

Adds `prev_hash`/`entry_hash` (NFR-10, issue #36 - see
[`data-model.md`](../architecture/data-model.md#the-hash-chain-nfr-10-issue-36-adr-0017))
to `audit_event`, both `TEXT NOT NULL` with no server default and no backfill. There is no
way to invent a hash for a pre-existing row, so **this migration only ever succeeds against
an empty `audit_event` table** - Postgres raises `23502` (not-null violation) otherwise.
Pre-alpha, no write path has ever run against this table, so this has never been a
practical constraint; a deployment that reaches this migration with real audit history
already in place would need a one-off backfill (computing each row's digest in `sequence`
order) before `upgrade head` can succeed, which is not something this migration attempts to
automate.

## `0005_user_role.py`

Adds `user_role` (issue #44, FR-44, FR-01 - see
[`data-model.md`](../architecture/data-model.md#user_role-issue-44-adr-0019)). Its
privilege grants and revokes live in this same migration, following the same reasoning as
`0002_audit_event.py`/`0003_user_and_user_identity.py` above - with one wrinkle worth
flagging: `UPDATE (granted_at)` **is** granted, narrowly, alongside `SELECT, INSERT,
DELETE`. This is not an oversight against "a grant is created or removed, never edited" -
Postgres requires *some* `UPDATE` privilege on a table before it honours `SELECT ... FOR
UPDATE` at all (confirmed against a real container while building this migration), and
`nptc.auth.grants.assert_not_last_administrator`'s row lock (FR-01) depends on exactly
that. `granted_at` is the one column nothing ever writes to after insert, so the
column-level grant costs nothing real while `user_id`/`role`/`granted_by_user_id` stay
immutable at the privilege level.

### Bootstrapping the first administrator

FR-01's last-administrator guard means a fresh deployment can never acquire its first
Administrator through the ordinary, `Principal`-checked path - there is no `Principal` yet
that could hold `role.grant.any`. After `upgrade head` and at least one real login (which
creates the `app_user` row via `nptc.auth.identity._create_user`'s default Provisional
grant), an operator with direct database access runs:

```powershell
uv run python scripts/grant_role.py --username <the user's username> --role administrator
```

This calls the same `nptc.auth.grants.grant_role_unchecked` a first-login Provisional grant
uses - still emits a `user_role.granted` audit event (`granted_by_user_id` null, the one
case that column is nullable for), and is still idempotent. There is no `--force` and no
revoke path through this script; once a second Administrator exists, every further
grant/revoke should go through the ordinary checked functions (`nptc.auth.grants.
grant_role`/`revoke_role`, landing with the P2 user-administration endpoints).

## Testcontainers and Docker

`uv run pytest` from the repository root now needs a **running** Docker daemon, not merely
an installed one - `backend/tests` runs every test against a real, containerized Postgres
(NFR-39). Docker (with Compose) was already a declared prerequisite
([CONTRIBUTING.md](../../CONTRIBUTING.md)); this is the same requirement, just now
exercised by the test suite as well as the local stack.
