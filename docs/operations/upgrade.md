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

## Provisioning the index reconciler's login (issue #54, FR-13)

Filterable-property index generation (`nptc.db.property_reconciler.
reconcile_property_indexes`, `scripts/reconcile_property_indexes.py`) needs its own DDL-
capable role, distinct from both `nptc_app_login` above (which cannot do DDL at all) and
the migration owner (which can `CREATE ROLE`/`DROP TABLE` - too broad a credential to hand
to a runtime reconciliation path). Provision a role scoped to exactly the one privilege it
needs:

```sql
CREATE ROLE nptc_indexer LOGIN PASSWORD '<a real, generated secret>';
GRANT CREATE ON TABLE property_value TO nptc_indexer;
```

(Postgres has no narrower "create index" grant than table-level `CREATE` - see
[ADR-0012](../adr/0012-property-registry-storage-and-validation.md)'s note that this is
exactly why the reconciler's role is never `nptc_app`'s ownership, which would additionally
confer `DROP`/`ALTER`/`TRUNCATE`.)

Set `NPTC_INDEXER_DATABASE_URL` to this role's DSN wherever the reconciliation path runs -
see [`configuration.md`](configuration.md). Leaving it unset is a valid, safe posture
(`IndexerSettings`'s own fail-closed default): reconciliation simply does not run until an
operator configures it, and `filterable` flags on `property_definition` accumulate no
consequence until then. `backend/tests/conftest.py` does not provision this role - the
integration tests that need it (`test_db_property_indexes.py`,
`test_db_property_index_plan.py`) point `NPTC_INDEXER_DATABASE_URL` at the container's own
bootstrap superuser instead, since a real deployment's narrower role has no equivalent
already sitting in the fixture graph.

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

## Testcontainers and Docker

`uv run pytest` from the repository root now needs a **running** Docker daemon, not merely
an installed one - `backend/tests` runs every test against a real, containerized Postgres
(NFR-39). Docker (with Compose) was already a declared prerequisite
([CONTRIBUTING.md](../../CONTRIBUTING.md)); this is the same requirement, just now
exercised by the test suite as well as the local stack.
