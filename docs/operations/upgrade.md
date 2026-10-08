# Database migrations and upgrade notes

Covers running Alembic migrations against a real deployment and the out-of-band steps an
operator does that migrations deliberately do not automate. This document owns
*operational* facts only - a precondition, a manual step, a non-obvious downgrade order.
See [`data-model.md`](../architecture/data-model.md) for the schema shape, and each
migration's own module docstring for the design rationale behind it (CONTRIBUTING.md's "A
schema change's prose has one home each") - a section below links to both rather than
restating them.

## Running migrations

**In the compose stack you run nothing.** The one-shot `migrate` service runs
`alembic upgrade head`, then [provisions the app role's login](#provisioning-the-app-roles-login),
every time you run `docker compose -f deploy/compose.yml up -d --build`. The `backend`
service starts only after `migrate` exits successfully. See [`deployment.md`](deployment.md).

To run Alembic yourself against any database, for example while developing a migration:

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

## Migration index

One row per revision. An operator consequence of `None` means there is nothing to do
beyond `upgrade head` - that migration's reasoning lives entirely in its own docstring
and/or `data-model.md`, so it gets no section of its own below.

| Revision | Adds | Operator consequence |
|---|---|---|
| [`0001_extensions_and_app_role.py`](../../backend/migrations/versions/0001_extensions_and_app_role.py) | `pg_trgm`, `unaccent`, the `nptc_app` role | Needs a superuser-equivalent DSN (above); see [The asymmetric downgrade](#the-asymmetric-downgrade) |
| [`0002_audit_event.py`](../../backend/migrations/versions/0002_audit_event.py) | `audit_event` (see [`data-model.md`](../architecture/data-model.md#audit_event)) | None |
| [`0003_user_and_user_identity.py`](../../backend/migrations/versions/0003_user_and_user_identity.py) | `app_user`, `user_identity` | See [below](#0003_user_and_user_identitypy) |
| [`0004_audit_event_hash_chain.py`](../../backend/migrations/versions/0004_audit_event_hash_chain.py) | `prev_hash`/`entry_hash` on `audit_event` | See [below](#0004_audit_event_hash_chainpy) |
| [`0005_user_role.py`](../../backend/migrations/versions/0005_user_role.py) | `user_role` | See [below](#0005_user_rolepy), plus first-administrator bootstrap |
| [`0006_catalogue_entry.py`](../../backend/migrations/versions/0006_catalogue_entry.py) | `catalogue_entry` (see [`data-model.md`](../architecture/data-model.md#catalogue_entry-issue-46-fr-03-fr-38)) | None |
| [`0007_designation.py`](../../backend/migrations/versions/0007_designation.py) | `designation` (see [`data-model.md`](../architecture/data-model.md#designation-issue-47-fr-04-fr-24-fr-37-fr-85)) | None - `downgrade()` drops the table outright |
| [`0008_code_binding.py`](../../backend/migrations/versions/0008_code_binding.py) | `code_binding` (see [`data-model.md`](../architecture/data-model.md#code_binding-issue-48-fr-06-fr-08-fr-82-fr-83)) | None - `downgrade()` drops the table outright |
| [`0009_collision_detection.py`](../../backend/migrations/versions/0009_collision_detection.py) | `designation.term_key`/`catalogue_entry.preferred_term_key`, `designation_collision_acknowledgement`, `ix_code_binding_one_active_entry_per_code` (see [`data-model.md`](../architecture/data-model.md#collision-detection-issue-49-fr-05-fr-08)) | See [below](#0009_collision_detectionpy) - backfills the two key columns from existing rows before adding `NOT NULL` |
| [`0010_property_definition_and_value.py`](../../backend/migrations/versions/0010_property_definition_and_value.py) | `property_definition`, `property_value` (see [`data-model.md`](../architecture/data-model.md#property-registry-issue-51-fr-09-fr-10-fr-11-fr-12)) | None |
| [`0011_local_code_systems.py`](../../backend/migrations/versions/0011_local_code_systems.py) | `local_code_system`, `local_code`, `local_code_snomed_map`, plus their seed data (see [`data-model.md`](../architecture/data-model.md#local-code-systems-and-the-advisory-snomed-map-issue-56-fr-90-fr-91-fr-92)) | None |
| [`0012_catalogue_search_indexes.py`](../../backend/migrations/versions/0012_catalogue_search_indexes.py) | `nptc_search_text`, two GIN trigram indexes | See [below](#0012_catalogue_search_indexespy) - a standing `REINDEX` obligation if the `unaccent` dictionary ever changes |
| [`0013_property_definition_local_code_system_key.py`](../../backend/migrations/versions/0013_property_definition_local_code_system_key.py) | `property_definition.local_code_system_key` (see [`data-model.md`](../architecture/data-model.md#property-registry-issue-51-fr-09-fr-10-fr-11-fr-12)) | See [below](#0013_property_definition_local_code_system_keypy) - backfills the new column on any database that already ran `seed_system_properties` before adding the `NOT NULL`-when-bound `CHECK` |
| [`0014_numeric_or_null_function.py`](../../backend/migrations/versions/0014_numeric_or_null_function.py) | `nptc_numeric_or_null` (see [`data-model.md`](../architecture/data-model.md#automatic-index-generation-issue-54-fr-13)) | See [below](#0014_numeric_or_null_functionpy) - downgrading past it requires no reconciler-built numeric-shaped index to still exist |
| [`0015_hybrid_search_indexes.py`](../../backend/migrations/versions/0015_hybrid_search_indexes.py) | `nptc_search_document`, `nptc_search_query`, four GIN full-text indexes, two GIN trigram indexes, one btree | See [below](#0015_hybrid_search_indexespy) - a second standing `REINDEX` obligation, this one on the `english` text search configuration |
| [`0016_code_binding_retired_at.py`](../../backend/migrations/versions/0016_code_binding_retired_at.py) | `code_binding.retired_at`, `ck_code_binding_retired_at` (see [`data-model.md`](../architecture/data-model.md#code_binding-issue-48-fr-06-fr-08-fr-82-fr-83)) | See [below](#0016_code_binding_retired_atpy) - backfills existing retired rows from `updated_at` before adding the `NOT NULL`-when-retired `CHECK` |
| [`0017_code_binding_system_code_index.py`](../../backend/migrations/versions/0017_code_binding_system_code_index.py) | `ix_code_binding_system_code` (see [`data-model.md`](../architecture/data-model.md#code_binding-issue-48-fr-06-fr-08-fr-82-fr-83)) | None |
| [`0018_validation_finding.py`](../../backend/migrations/versions/0018_validation_finding.py) | `validation_finding`, `ix_audit_event_entity_type_entity_id_sequence` on `audit_event` (see [`data-model.md`](../architecture/data-model.md#validation_finding-fr-18-fr-45-fr-55-issue-141)) | See [below](#0018_validation_findingpy) - the new `audit_event` index is a blocking build |
| [`0021_seed_import_provenance.py`](../../backend/migrations/versions/0021_seed_import_provenance.py) | `seed_import`, `entry_seed_provenance` (see [`data-model.md`](../architecture/data-model.md#seeded-baseline-provenance-issue-329-fr-76-adr-0010-adr-0042)) | None to upgrade. To populate them, run the [seed baseline runbook](runbooks/seed-baseline.md) once on a new deployment |
| [`0022_terms_acceptance.py`](../../backend/migrations/versions/0022_terms_acceptance.py) | `terms_acceptance` (see [`data-model.md`](../architecture/data-model.md#terms_acceptance-nfr-45-nfr-47-adr-0043)) | See [below](#0022_terms_acceptancepy) - every existing user must accept the current terms before their next contribution |
| [`0023_specimen_binding_includes_root.py`](../../backend/migrations/versions/0023_specimen_binding_includes_root.py) | The `specimen` binding `<<123038009` (see [`data-model.md`](../architecture/data-model.md)) | See [below](#0023_specimen_binding_includes_rootpy) - re-emit any dataset made before this release |
| [`0024_retire_specimen_unconstrained.py`](../../backend/migrations/versions/0024_retire_specimen_unconstrained.py) | Drops `catalogue_entry.specimen_unconstrained` (see [`data-model.md`](../architecture/data-model.md#catalogue_entry-issue-46-fr-03-fr-38)) | See [below](#0024_retire_specimen_unconstrainedpy) - converts the flag to the specimen root first |
| [`0025_property_index_owner_role.py`](../../backend/migrations/versions/0025_property_index_owner_role.py) | The `nptc_property_index_owner` role, which takes over ownership of `property_value` (see [`data-model.md`](../architecture/data-model.md#automatic-index-generation-issue-54-fr-13)) | See [below](#0025_property_index_owner_rolepy) - a non-superuser migration role needs membership of the new role |

## Provisioning the app role's login

Migrations create the `nptc_app` role (`NOLOGIN`) and grant it the privileges the
application needs - they deliberately do **not** create a `LOGIN` role or set a password
anywhere (NFR-26: no secrets committed to the repository). The login role is created after
the migration has run, by `nptc.db.provision_login`:

```powershell
uv run python -m nptc.db.provision_login
```

The command connects with `NPTC_MIGRATION_DATABASE_URL`, then creates `nptc_app_login` with
the password in `NPTC_APP_DB_PASSWORD` and makes it a member of `nptc_app`. It is safe to
repeat: a second run sets the password to the current value, which is also how you rotate
it. The compose `migrate` service runs this command for you.

`NPTC_DATABASE_URL` (the application's own runtime DSN) then authenticates as
`nptc_app_login`. `backend/tests/conftest.py` calls the same function inside the disposable
test container, with an obviously-synthetic local-only password - never real credentials,
and never anything committed.

If you manage database roles by other means, you can skip the command and run the
equivalent SQL yourself, once:

```sql
CREATE ROLE nptc_app_login LOGIN PASSWORD '<a real, generated secret>';
GRANT nptc_app TO nptc_app_login;
```

## Provisioning the index reconciler's login (issues #54 and #274, FR-13)

After a registry write commits, the API builds or drops the index a `filterable` property needs
(`nptc.db.property_reconciler_dispatch`, `nptc.db.property_reconciler`). It does this as its own
login, `nptc_indexer`. It does not use `nptc_app_login`, which cannot run DDL. It does not use the
migration owner either, which can `CREATE ROLE` and `DROP TABLE`, far more than an index needs.

**The compose stack provisions this for you.** The `migrate` service runs
`python -m nptc.db.provision_login`. That command creates `nptc_indexer` with the password in
`NPTC_INDEXER_DB_PASSWORD` and makes it a member of `nptc_property_index_owner`. Compose gives
the `backend` service the matching `NPTC_INDEXER_DATABASE_URL`. Run the command again to rotate
the password. A `deploy/.env` from before this change makes compose stop with "required variable
NPTC_INDEXER_DB_PASSWORD is missing a value". Copy the line from `deploy/.env.example`.

**What the login can do, and why.** Postgres has no "create index" privilege. `CREATE INDEX`
needs ownership of the table, and Postgres also checks `CREATE` on the schema that will hold the
index. Migration `0025_property_index_owner_role.py` therefore creates a `NOLOGIN` role,
`nptc_property_index_owner`. That role owns `property_value`, holds `CREATE` on schema `public`
and holds `SELECT` on `property_definition`, which the reconciler reads. `nptc_indexer` is a
member of it.

So the login can create and drop indexes on `property_value`. It can also alter, drop and
truncate `property_value`, and create new objects in `public`, because ownership and schema
`CREATE` carry those rights. It cannot read or change any other table.
`backend/tests/test_indexer_role.py` asserts both halves. An earlier version of this page said
`GRANT CREATE ON TABLE property_value` was enough. That privilege does not exist.

**If you manage database roles yourself**, run this once after `alembic upgrade head`:

```sql
CREATE ROLE nptc_indexer LOGIN PASSWORD '<a real, generated secret>';
GRANT nptc_property_index_owner TO nptc_indexer;
```

**Who runs migrations after 0025.** The migration role no longer owns `property_value`. A
superuser migration role, such as the compose `POSTGRES_USER`, needs nothing more. A
non-superuser migration role must be a member of `nptc_property_index_owner` to run 0025's
`ALTER TABLE ... OWNER TO`, and to alter `property_value` in any later migration. Before you
upgrade to 0025, create the role and grant it to the migration role:

```sql
CREATE ROLE nptc_property_index_owner NOLOGIN;
GRANT nptc_property_index_owner TO <migration role>;
```

Migration 0025 skips creating a role that already exists.

**Leaving `NPTC_INDEXER_DATABASE_URL` unset** is still a valid, safe setup (`IndexerSettings`
fails closed). A registry write then logs a warning and builds no index. Run
`scripts/reconcile_property_indexes.py` to converge, as the
[runbook](runbooks/reconcile-property-indexes.md) describes.

`backend/tests/conftest.py` does not provision `nptc_indexer`. `test_db_property_indexes.py` and
`test_db_property_index_plan.py` point `NPTC_INDEXER_DATABASE_URL` at the container's bootstrap
superuser. `test_indexer_role.py` provisions the real login.

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

In the compose stack, run it inside the `backend` container instead:

```powershell
docker compose -f deploy/compose.yml exec backend python scripts/grant_role.py --username <the user's username> --role administrator
```

This calls the same `nptc.auth.grants.grant_role_unchecked` a first-login Provisional grant
uses - still emits a `user_role.granted` audit event (`granted_by_user_id` null, the one
case that column is nullable for), and is still idempotent. There is no `--force` and no
revoke path through this script; once a second Administrator exists, every further
grant/revoke should go through the ordinary checked functions (`nptc.auth.grants.
grant_role`/`revoke_role`, landing with the P2 user-administration endpoints).

## `0009_collision_detection.py`

Adds `designation.term_key`/`catalogue_entry.preferred_term_key` (issue #49, FR-05 - see
[`data-model.md`](../architecture/data-model.md#collision-detection-issue-49-fr-05-fr-08)),
`designation_collision_acknowledgement`, and `ix_code_binding_one_active_entry_per_code`.
Unlike `0004_audit_event_hash_chain.py` above, the two new key columns **do** backfill: for
every pre-existing `designation`/`catalogue_entry` row, the migration computes
`nptc_shared.similarity.collision_key(term)` in Python (the same function a fresh write
uses) and writes it before the column becomes `NOT NULL` - so a deployment upgrading with
real catalogue content already in place never has to backfill by hand. Pre-alpha, no seed
data has ever been loaded, so this backfill has never had a real row to act on in practice.

## `0012_catalogue_search_indexes.py`

Adds the `nptc_search_text` normalisation function and the two GIN trigram indexes the
public catalogue search matches through (issue #142 - see
[`data-model.md`](../architecture/data-model.md#search-normalisation-and-the-trigram-indexes-issue-142-fr-14-fr-15)
and [ADR-0024](../adr/0024-catalogue-search-and-pagination.md)). No table, no column, no
grant changes, and nothing to backfill: `upgrade head` is all that is required.

**One standing operator obligation.** `nptc_search_text` is declared `IMMUTABLE`, which
is honest only for a *fixed* `unaccent` dictionary definition. Both trigram indexes
store values produced by that dictionary, so if its rule file ever changes underneath a
running database, the stored index entries stop corresponding to what the function now
returns - and search silently starts missing rows rather than failing. In practice that
can happen two ways:

- a PostgreSQL major upgrade shipping a revised `unaccent.rules`, or
- a deployment substituting its own rules (`ALTER TEXT SEARCH DICTIONARY unaccent
  (RULES = ...)`, or a replaced rules file).

After either, reindex both:

```sql
REINDEX INDEX CONCURRENTLY ix_catalogue_entry_preferred_term_trgm;
REINDEX INDEX CONCURRENTLY ix_designation_term_trgm;
```

Since `0015`, **four more indexes are built over the same `unaccent` dictionary** and
must be reindexed at the same time - see
[`0015_hybrid_search_indexes.py`](#0015_hybrid_search_indexespy) for the full list and
for the second, independent obligation that migration introduces.

`CONCURRENTLY` so search stays available while it runs; drop it if the maintenance
window allows an exclusive lock. Nothing detects a stale index automatically - which is
exactly why this obligation is written down here rather than left implicit in the
`IMMUTABLE` marking.

`downgrade()` drops both indexes and then the function, in that order (the reverse of
`upgrade()`), since each index expression depends on the function.

## `0013_property_definition_local_code_system_key.py`

Adds `property_definition.local_code_system_key` (issue #52, FR-09/FR-10 - see
[`data-model.md`](../architecture/data-model.md#property-registry-issue-51-fr-09-fr-10-fr-11-fr-12)).
Backfills before the new `local_code_system_key_required` CHECK is created: any database
that has already run `seed_system_properties` holds `discipline`/`subgroup` rows with
`binding_target = 'local_code_system'` and `local_code_system_key IS NULL` (the column did
not exist when those rows were seeded, and `seed_system_properties` skips a row it has
already seeded, so it never revisits them on a later run). The migration sets
`local_code_system_key = key` for exactly those rows - correct because bootstrap seeds both
`discipline`'s and `subgroup`'s governed `local_code_system.key` identical to the property's
own key. A database whose `discipline`/`subgroup` definition was hand-edited to bind some
other `local_code_system` is not something this backfill can recover automatically; correct
it manually before upgrading, or accept that it will be backfilled to the standard value.

## `0014_numeric_or_null_function.py`

Adds `nptc_numeric_or_null` (issue #54, FR-13 - see
[`data-model.md`](../architecture/data-model.md#automatic-index-generation-issue-54-fr-13)
and [ADR-0027](../adr/0027-cast-safe-numeric-index-expression.md)). No table, no column, no
grant changes, and nothing to backfill: `upgrade head` is all that is required. Creates no
index itself - the reconciler builds a property's index at runtime, once it is flagged
filterable, referencing this function.

**Downgrade obligation, the mirror of the trigram indexes' obligation above.** Postgres
tracks a dependency from a generated expression index to this function, so `downgrade()`
(`DROP FUNCTION`) fails if a reconciler-built `decimal`/`positiveInt` index still exists.
Unlike `0012`'s own indexes, these are not migration-managed, so this migration cannot drop
them itself before dropping the function. Before downgrading past `0014`, reconcile every
numeric-shaped filterable property back to `filterable = false` (or drop the generated
index by hand) first.

## `0015_hybrid_search_indexes.py`

Adds the `nptc_search_document`/`nptc_search_query` function pair and the seven indexes
that bring the stored `fsn`, the stored `au_preferred_term` and the SNOMED code into the
search, alongside the full-text half of the ranking (issue #138 - see
[`search.md`](../architecture/search.md) and
[ADR-0029](../adr/0029-hybrid-full-text-and-trigram-search.md)). No table, no column, no
grant changes, and nothing to backfill: `upgrade head` is all that is required.

**Expect a longer `upgrade head` than the migrations before it.** Seven indexes are
built over four expression-indexed columns on tables that already hold data, and the four
GIN full-text indexes are the slowest of them. On an empty or freshly-seeded database
this is seconds; on a populated catalogue, budget for it and take the maintenance window
rather than running it against live traffic. The indexes are created non-concurrently
(an Alembic migration runs in a transaction, and `CREATE INDEX CONCURRENTLY` cannot),
so each takes a lock that blocks writes to its table for the duration.

**A second standing operator obligation**, independent of `0012`'s. `nptc_search_document`
is declared `IMMUTABLE`, which is honest only for a *fixed* `english` text search
configuration - its stemmer (the `english_stem` Snowball dictionary) and its stopword
list. The four full-text indexes store lexemes produced by that configuration, so if it
changes underneath a running database the stored entries stop corresponding to what the
function now returns, and search silently starts missing rows rather than failing. The
realistic triggers are:

- a PostgreSQL major upgrade shipping a revised Snowball stemmer or stopword file, or
- a deployment altering the configuration (`ALTER TEXT SEARCH CONFIGURATION english ...`,
  or a replaced `english.stop`).

After either, reindex the four full-text indexes:

```sql
REINDEX INDEX CONCURRENTLY ix_catalogue_entry_preferred_term_fts;
REINDEX INDEX CONCURRENTLY ix_designation_term_fts;
REINDEX INDEX CONCURRENTLY ix_code_binding_fsn_fts;
REINDEX INDEX CONCURRENTLY ix_code_binding_au_preferred_term_fts;
```

**The `unaccent` obligation from `0012` now covers six indexes, not two.** Both function
families normalise through `nptc_search_text`, so a change to the `unaccent` dictionary
invalidates the two new trigram indexes and all four full-text indexes as well as
`0012`'s original pair. After an `unaccent` change, reindex `0012`'s two plus:

```sql
REINDEX INDEX CONCURRENTLY ix_code_binding_fsn_trgm;
REINDEX INDEX CONCURRENTLY ix_code_binding_au_preferred_term_trgm;
```

...and the four full-text indexes above. The two obligations are separate because their
triggers are separate: a stemmer change does not touch the trigram indexes, and an
`unaccent` change touches everything.

`ix_code_binding_code` is a plain btree over a stored column and is unaffected by either.

`downgrade()` drops all seven indexes and then the two functions, in that order (the
reverse of `upgrade()`), since four of the index expressions depend on
`nptc_search_document`.

## `0016_code_binding_retired_at.py`

Adds `code_binding.retired_at` (issue #140, FR-17 - see
[`data-model.md`](../architecture/data-model.md#code_binding-issue-48-fr-06-fr-08-fr-82-fr-83)),
the timestamp the FR-17 exact-code lookup routes use to tie-break a code that two
different entries each hold as a *retired* binding. Backfills before
`ck_code_binding_retired_at` is created: any pre-existing row with `status =
'retired'` gets `retired_at` set from its own `updated_at` (the closest available
approximation of when it was actually retired, since the column did not exist
before this migration), so an upgrading deployment with real retired bindings
already in place never violates the new CHECK on `upgrade head`. A fresh retirement
after this migration writes `retired_at` via `func.now()` (the database clock, not
the application clock), independent of this one-time backfill.

## `0018_validation_finding.py`

Adds `validation_finding` (issue #141, FR-18/FR-45/FR-55 - see
[`data-model.md`](../architecture/data-model.md#validation_finding-fr-18-fr-45-fr-55-issue-141))
and, on the pre-existing `audit_event` table, `ix_audit_event_entity_type_entity_id_sequence`
(PR #278 review): the index the public FR-19 history endpoint needs, since `audit_event` had
none of its own before this beyond `sequence`'s `UNIQUE`.

**Expect this index's build to block writes, the same obligation `0015` records for its own
seven.** `audit_event` is the one table in this schema that grows without bound and is never
truncated (NFR-10's hash chain), so it is also the migration most likely to take a long time
to build an index over on an established deployment. The index is created non-concurrently -
an Alembic migration runs in a transaction, and `CREATE INDEX CONCURRENTLY` cannot - so it
holds a lock that blocks every write to `audit_event`, and therefore every state-changing
write path in the application (NFR-08), for as long as the build takes. Size the maintenance
window against `audit_event`'s current row count before upgrading a deployment with real
traffic history; on an empty or freshly-seeded database this is seconds.

## `0022_terms_acceptance.py`

Adds `terms_acceptance` (NFR-45, ADR-0043 - see
[`data-model.md`](../architecture/data-model.md#terms_acceptance-nfr-45-nfr-47-adr-0043)). It
creates an empty table, so the upgrade itself is instant and needs no data step.

**Every existing user must accept the current terms before their next contribution.** None has
an acceptance row, so once the API at this revision runs, it refuses every contribution with a
403 whose body carries `code: terms_acceptance_required` until that user accepts. Reads,
sign-in, and the accept request itself stay open. The SPA shows each such user a full-page
**Accept the terms of use** gate on their next signed-in page load, so no operator step is
needed. Any other client accepts by calling `POST /api/v1/auth/terms/acceptance` with the
current `version` from `GET /api/v1/auth/terms`.

The API reads the terms files named by `NPTC_TERMS_CURRENT_VERSION` and refuses to start if
that version has no file. See [`configuration.md`](configuration.md).

The downgrade drops the table, which discards every recorded acceptance.

## `0023_specimen_binding_includes_root.py`

Widens the system `specimen` property's binding from `<123038009` to `<<123038009`, and
removes its `forbidden_codes: ["Any"]` constraint (FR-88, FR-89, ADR-0044). `Any` is now the
specimen code `123038009`, which `<123038009` refuses because it selects descendants only. The
migration updates one row and is instant.

**The update is guarded.** It matches the system `specimen` definition only while it still holds
the old binding, so a binding that an administrator changed is kept. If you changed it, set the
new binding by hand. The migration leaves `row_version` alone and writes no audit event, as
migration 0013's backfill did.

**Re-emit any import dataset made before this release.** The dataset format moves to
`schema_version` 2 and loses the `specimen_unconstrained` field, so the loader refuses a version 1
file and names the version. Run the current transform again (see
[`runbooks/transform.md`](runbooks/transform.md)).

The downgrade restores the old binding and constraint. It does not remove the root from an entry
that already holds it, so downgrade only an empty catalogue or a rehearsal database.

## `0024_retire_specimen_unconstrained.py`

Drops `catalogue_entry.specimen_unconstrained` (FR-89, ADR-0044). "Accepts any specimen" is now
the `specimen` value `123038009`, held alone.

**The flag converts before the column goes.** Each entry marked `true` that holds no specimen
value gets one: `{"system": "http://snomed.info/sct", "code": "123038009", "display": "Any"}`.
An entry that holds named specimens and the flag (the old rule refused that pair, but a seeded or
direct-SQL row could hold it) keeps its named specimens, and the flag is lost. The upgrade logs a
warning that gives the count and the business keys (the first 50) before it drops the column, so
read the migrate output and review those entries. The conversion is raw SQL, so it writes **no audit event** and leaves `row_version`
alone, as migrations 0009, 0013 and 0023 do. The entry's history shows no event for it. Run the
migration with the backend stopped, which the compose `migrate` service already guarantees.

**The upgrade stops with a message** if an entry needs the conversion and the `specimen`
property definition does not exist. The seed loader creates that definition before it writes any
entry, so a catalogue that holds entries has it.

**The API changes with it.** Entry responses lose `specimen_unconstrained`, and `PATCH
/catalogue/entries/{business_key}` takes `{status, reason, expected_row_version}` and refuses any
other field. A client built for the old shape must change. Entries edited through the API now
record "any specimen" by writing the root as the one specimen value.

The downgrade re-adds the column and re-grants `nptc_app` its `UPDATE` on it. An entry whose only
specimen is the root becomes flagged again, and that value is removed, which restores the old
rule that an entry holds the flag or specimens, never both. An entry that holds the root beside
named specimens (the new rule refuses that pair) is left as it is.

## `0025_property_index_owner_role.py`

Creates the `NOLOGIN` role `nptc_property_index_owner` and makes it the owner of
`property_value` (FR-13). It also grants the role `SELECT` on `property_definition` and `CREATE`
on schema `public`. The reconciler's login, `nptc_indexer`, is a member of it. See
[Provisioning the index reconciler's login](#provisioning-the-index-reconcilers-login-issues-54-and-274-fr-13)
for why Postgres needs all three, and for what the login can then do.

**Who runs the upgrade.** A superuser migration role, such as the compose `POSTGRES_USER`, needs
nothing more. A non-superuser migration role must be a member of `nptc_property_index_owner`
before 0025 runs, because `ALTER TABLE ... OWNER TO` needs it. Create the role by hand and grant
it to the migration role first. The migration skips creating a role that already exists. The same
membership is needed for any later migration that alters `property_value`.

**Existing grants are unchanged.** `nptc_app` keeps the privileges it had on `property_value`.

The downgrade revokes the two grants and returns ownership of `property_value` to the role that
runs it. It does not drop the role, for the reason given under
[The asymmetric downgrade](#the-asymmetric-downgrade).

## Testcontainers and Docker

`uv run pytest` from the repository root now needs a **running** Docker daemon, not merely
an installed one - `backend/tests` runs every test against a real, containerized Postgres
(NFR-39). Docker (with Compose) was already a declared prerequisite
([CONTRIBUTING.md](../../CONTRIBUTING.md)); this is the same requirement, just now
exercised by the test suite as well as the local stack.
