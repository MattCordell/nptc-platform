# ADR-0012: Property registry storage and validation

**Status:** Accepted
**Date:** 2026-08-16

## Context

PRD SS6.5 calls the property registry the highest-risk requirement in the brief. Four P1
issues - [#51](https://github.com/MattCordell/nptc-platform/issues/51) (`PropertyDefinition`
model + JSONB `PropertyValue`), [#52](https://github.com/MattCordell/nptc-platform/issues/52)
(per-property JSON Schema validation),
[#54](https://github.com/MattCordell/nptc-platform/issues/54) (automatic index generation)
and [#55](https://github.com/MattCordell/nptc-platform/issues/55) (deprecation / key
immutability) - are each blocked on this ADR. Without it, four PRs each choose a slightly
different shape for the same two tables, and the design gets discovered inside whichever
one lands first.

`backend/src/nptc/registry/` is a one-line docstring stub today. Nothing is being changed;
this is a documentation-only PR that fixes the design so #51-#55 implement a schema rather
than pick one.

Three constraints collide specifically on FR-13 and are why this ADR is long:
[roles.py](../../backend/src/nptc/db/roles.py)'s `nptc_app` is `NOLOGIN` with `USAGE` +
table `SELECT`/`INSERT` only and cannot `CREATE INDEX`; NFR-22 and
[test_sql_parameterisation.py](../../backend/tests/test_sql_parameterisation.py) forbid
runtime data reaching argument 0 of an `execute` call; and
[data-model.md](../architecture/data-model.md) already names #54's generated index names as
the most likely first victim of Postgres's silent 63-byte identifier truncation.

Scope fence: the datatype **handler contract** (FR-77) is issue #137's ADR. This one fixes
the storage envelope and names the seams a handler plugs into.

## Decision

**`property_definition` is a conventional, fully-constrained relational table, not a
document.** Columns, fixed against PRD SS6.5's field table: `id` UUID PK + `index_seq`
BIGINT `GENERATED ALWAYS AS IDENTITY` (mirroring
[audit.py](../../backend/src/nptc/db/models/audit.py)'s `id`/`sequence` pair, for the ACL
reason ADR-0011 proved - identity, not `serial`, so `INSERT` alone suffices without a
separate sequence grant), `key` TEXT NOT NULL UNIQUE with
`CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$')`, `label` TEXT NOT NULL, `datatype` TEXT NOT NULL,
`cardinality` TEXT NOT NULL, `scope` TEXT NOT NULL, `required_for_submission` BOOLEAN NOT
NULL, `required_for_publication` BOOLEAN NOT NULL, the four binding columns
(`binding_target`, `value_set_uri`, `strength`, `edition` - all nullable, populated only when
`datatype = 'code'`), `filterable` BOOLEAN NOT NULL, `origin` TEXT NOT NULL, `status` TEXT
NOT NULL, `display_order` INTEGER NOT NULL, `constraints` JSONB NOT NULL DEFAULT '{}',
`deprecated_at` TIMESTAMPTZ (nullable - set only when `status = 'deprecated'`, tied to it by
a CHECK below), `created_at`/`updated_at` TIMESTAMPTZ NOT NULL, and `row_version` INTEGER NOT
NULL DEFAULT 1. Nullability is stated for every column, not left implicit: a "fully-constrained
relational table" that leaves nullability unstated is a claim the schema doesn't actually
back, and one CHECK below only holds because `datatype` is `NOT NULL` - see next paragraph.

**`datatype` is plain TEXT with no CHECK and no Postgres ENUM; `cardinality`, `scope`,
`origin`, `status` and the binding fields get named CHECKs.** A `CHECK (datatype IN (...))`
is an edit outside the handler module - exactly FR-77's stated failure condition: "if adding
a datatype requires edits in more than the handler module and its tests, the requirement has
not been met." An ENUM has the identical problem (`ALTER TYPE ... ADD VALUE` is a schema
change landing wherever the migration author happens to put it) plus its own transactional
restrictions. CHECK is the right tool everywhere else - `cardinality`, `scope`, `origin`,
`status`, `binding_target` and `strength` are closed, stable vocabularies that are not FR-77's
extension point - and each is table-local, named deterministically by
[base.py](../../backend/src/nptc/db/base.py)'s `NAMING_CONVENTION`, and already covered by
the round-trip reflection fingerprint.

**FR-10's binding is four real columns, not a JSONB sub-document**, with
`CHECK ((datatype = 'code') = (binding_target IS NOT NULL))` making a code-without-a-binding
unrepresentable rather than merely refused by application code that a future write path could
forget to call. This CHECK is only meaningful because `datatype` is `NOT NULL` (previous
paragraph): a nullable `datatype` would let `datatype = 'code'` evaluate to `NULL`, and
Postgres treats a `NULL` CHECK result as a pass, silently reopening exactly the hole this
constraint exists to close. A second, equally load-bearing CHECK,
`CHECK (binding_target IS DISTINCT FROM 'value_set' OR value_set_uri IS NOT NULL)`, makes
`value_set_uri`'s "required when `binding_target = 'value_set'`" (data-model.md) a schema
invariant rather than a sentence a future implementer has to remember to enforce by hand;
`IS DISTINCT FROM` rather than `<>` so a `NULL` `binding_target` (any non-`code` property)
does not make the comparison itself `NULL` and mask a real violation. Open-ended datatype
parameters (a string's max length, a decimal's min/max, an allowed URL scheme list) go in a
separate handler-owned `constraints` JSONB column - #137's ADR owns its interior; this one
only reserves the column.

**`property_value` is one row per value**: `(entry_id, property_key, value JSONB, ordinal)`
per PRD SS6.5, plus a `justification` column for FR-10's extensible-strength case, with
`ordinal` `NOT NULL` `CHECK (ordinal >= 0)` and zero-based (the first value of a
multi-valued property is `ordinal = 0`). **`(entry_id, property_key, ordinal)` is the primary
key**, not a surrogate id plus a separate `UNIQUE` - it is already exactly what every write
and every FK needs to address a value by, and a PK subsumes the uniqueness `property_value`
requires. **The FK targets `property_definition(key)`, not a surrogate id** - FR-12 already
rules out the usual objection to a natural key (that it might change).

That FK does **not**, on its own, make FR-11/FR-12 unconditional. The default `RESTRICT` FK
blocks deleting or renaming a
`property_definition` row only *while a dependent `property_value` row exists* - a
definition with zero recorded values is, as far as the FK is concerned, still deletable and
renameable. What actually makes FR-11/FR-12 unconditional is the column-level privilege
below (no `DELETE` grant at all; `key` excluded from the `UPDATE` grant column list),
independent of whether any value has ever been recorded. The FK's real contribution is a
second, defence-in-depth backstop specifically for a race between a privilege check and a
concurrent insert: state each mechanism as what it actually covers, since #51/#55 read this
paragraph as the specification.

The primary key's uniqueness on `ordinal` closes only the trivial race - two inserts cannot
land on the same ordinal slot for the same entry/property - **it does not enforce
cardinality's upper bound**: a `0..1`/`1..1` property still admits concurrent inserts at
`ordinal = 0` and `ordinal = 1`, both satisfying the key. Expressing "at most one value" at
the schema level needs a predicate conditioned on the property's own cardinality, which a
table-local CHECK cannot see (it cannot query `property_definition`) and a trigger is banned
from expressing (PRD Section 14.1); the only schema-level route is a *generated* partial
unique index per single-valued property (`ON property_value (entry_id) WHERE property_key =
'<literal>'`), which is the same class of per-property, data-dependent DDL problem FR-13's
index already is. This ADR does not resolve it: #52 enforces the upper bound at validation
time and accepts the residual TOCTOU race an application check has; if that race proves to
matter in practice, extending #54's generated-index machinery to cover cardinality (not only
`filterable`) is the shortlisted fix, not a trigger.

Note the ordering dependency: `catalogue_entry` lands with #46-#48, so #51's `entry_id` FK
target does not exist yet when #51 is implemented - #51 records this as an open item (add
the FK in a follow-on migration once `catalogue_entry` exists) rather than silently dropping
it.

**JSON Schema is derived from the definition row by the handler, memoised in-process against
`(key, row_version)`, and persisted nowhere.** `row_version` rather than `key` alone as the
cache key is load-bearing: keying on `key` alone would serve the stale schema until restart,
reintroducing through the back door the restart FR-09 forbids in front. A narrowing amendment
(e.g. tightening `strength` from `extensible` to `required`) re-validates every affected value
synchronously at save time and reports the count to the administrator; non-conforming values
already on record are retained, not rejected retroactively, and surface through the existing
FR-10 sweep rather than a bespoke migration-time check. **No per-value schema version pin** -
that would put N live schema versions in the catalogue simultaneously and make every reader
(export, search, sweep) special-case which version a given value was validated against,
which is the exact failure the whole design exists to avoid.

**`row_version` is owned by exactly one write path: SQLAlchemy's mapper-level optimistic
concurrency (`version_id_col`) on `PropertyDefinition`'s ORM-mapped `UPDATE`** - not a
migration, not a manual bump, and not database-generated (Postgres has no built-in per-row
version counter, and a trigger-based one is banned by PRD Section 14.1). This only holds if
every amendment to a `property_definition` row goes through that one mapped `UPDATE`: the
bootstrap seeding command only `INSERT`s (so `row_version` starts at its `DEFAULT 1` and is
never at risk on first creation), but a future admin-side raw-SQL fix, or a Core-style bulk
update (`session.execute(update(PropertyDefinition)...)`, which goes through the ORM's
`Session` but bypasses `version_id_col` enforcement all the same), would bump the row without
bumping the counter - reintroducing FR-09's restart-shaped staleness through a different
door. Issue #52 states this as a hard rule (amendments MUST go through the mapped,
identity-map-loaded `UPDATE`, never a `sqlalchemy.update(...)` Core construct, against this
table) and its own tests assert `row_version` increments on every amendment path that exists
at that point, since there is no static guard analogous to NFR-22's that can currently tell a
Core-style bulk update of `property_definition` from any other table's.

**FR-13 index strategy.** The PRD's single-vs-multi-valued split does not survive the
row-per-value shape (multi-valuedness is rows, not a JSON array); the distinction that
actually determines the DDL is the **datatype's operator class**. Three concrete shapes,
each a **partial** index predicated on `property_key = '<literal>'` so one property's index
never scans another's rows:

- GIN with `jsonb_path_ops` for object-valued `code` properties (e.g. a coded value stored as
  `{"code": "...", "system": "..."}`), supporting containment queries.
- An expression index on `(value #>> '{}')` for `string`/`url` properties, supporting
  equality and prefix filtering over the extracted scalar text.
- An expression index on `((value #>> '{}')::numeric)` for `decimal`/`positiveInt`
  properties, supporting range predicates.

Fix:

- **Names never contain the property key**: `ix_propval_p{index_seq}_{slot}`, where the
  fixed 12-byte prefix (`ix_propval_p`) plus `index_seq`'s worst case of 19 digits (a signed
  64-bit `BIGINT`'s maximum magnitude) plus a 1-byte separator plus a single-digit `slot`
  (`1` for the primary partial index, `2` for the composite-btree fallback below, should #54
  need both for one property) is at most 33 bytes - provably under both the 39-byte target
  and Postgres's 63-byte identifier limit by construction, not by a length check. The key
  itself goes in `COMMENT ON INDEX`, resolving data-model.md's caveat for #54 specifically.
  The `key` regex CHECK's own 63-character allowance is a key-hygiene limit, unrelated to
  what makes the generated index names safe - that is the `index_seq` scheme above, which
  doesn't reference `key` at all - so a future relaxation of the regex is not a truncation
  risk to this naming scheme.
- **Safe rendering in three layers**, the load-bearing one being the `key` regex CHECK
  above, which makes NFR-22's "validated against the registry" a database invariant rather
  than a hopeful application check; composition via `psycopg.sql.Identifier`/`Literal`
  rather than string formatting; and the static guard below as a backstop over both.
- **The NFR-22 static guard needs a fourth rule, not an exemption** - `.format()` at
  argument 0 permitted only for a `sql.SQL(<string literal>)` receiver where every argument
  is itself a `sql.Identifier`/`Literal`/`SQL` call, in one named module (the DDL executor
  #54 introduces). Strictly narrower than today's blanket ban; a `# noqa`-style exemption is
  a hole that widens the moment a second, less careful call site copies it.
- **Generated indexes must be excluded from Alembic autogenerate and the round-trip
  fingerprint** via an `include_object` hook matching `^ix_propval_p\d+_[12]$` - this is the
  concrete reason the naming scheme is a strict regex rather than a loose convention.
- **Executor deferred to #54**, stated as an open question with what is already ruled out
  recorded so it is not relitigated: granting `nptc_app` ownership (Postgres has no grantable
  "create index" privilege short of ownership, which also confers `DROP`/`ALTER`/`TRUNCATE` -
  turning a least-privilege runtime role into the schema owner for one DDL operation), and
  running it inline in a request handler (`CREATE INDEX CONCURRENTLY` cannot run inside a
  transaction block, so inline execution either drops `CONCURRENTLY` - a read-blocking lock
  on `property_value` - or splits the handler across an implicit autocommit boundary
  mid-request). The two surviving candidates - a separate indexer process, or a background
  task in the API process on its own autocommit connection - trade off where the
  schema-modifying credential lives (a second deployable versus a second connection
  pool inside the existing one); #54 chooses between them, not from scratch.
- Flag as a claim **#54 must prove by `EXPLAIN`, not assume**: that rendering the property
  key as a literal (rather than a bind parameter) is what makes the partial index usable at
  all under a generic plan, with the `(property_key, <expr>)` composite btree written down as
  the fallback if it is not (`slot = 2` above). Also flag that at ~5,000 entries the planner
  may legitimately prefer a sequential scan, so #54's fixture needs a selective predicate and
  must not reach for `enable_seqscan = off` to force the issue.
- **The numeric expression index presupposes cast-safe values - #54 must not assume it.**
  `((value #>> '{}')::numeric)` fails `CREATE INDEX` outright the moment one retained
  `decimal`/`positiveInt` value on record is not castable to `numeric`, and the retention
  rule above (non-conforming values are kept, not rejected retroactively after a narrowing
  amendment) is exactly what can produce one. #54 either requires a datatype-conformance
  sweep to have passed with zero outstanding findings for that property before generating
  this index shape, or the expression itself must be cast-safe. The same applies if a
  datatype amendment (`string` -> `decimal`) is ever permitted on a property already
  carrying values.

**FR-11/FR-12 enforcement: column-level privilege is what makes both unconditional, never a
trigger** - the FK above is a secondary backstop, conditional on a dependent value existing,
not the mechanism itself. `nptc_app` gets `UPDATE` at column level on every
`property_definition` column except `key`, `id`, `index_seq`, `origin` and `created_at`
(never table-level `UPDATE`, which would supersede the column list) - FR-12 reduces to
"`INSERT` may set `key`, `UPDATE` may not touch it", failing with `42501` regardless of what
the ORM or a future contributor believes. No `DELETE` grant at all on `property_definition`,
for FR-11's unconditional form (below). `DELETE` **is** granted on `property_value` -
removing a specimen from an entry is ordinary editing, not the case FR-11 protects against.
The API layer refuses a delete attempt first with an actionable message (#55's AC, PRD
SS17.2.5); the privilege is the backstop that proves the negative.

Six tests, one refusal per test function for the `25P02` reason ADR-0011 recorded (a
privilege error aborts the surrounding transaction, so a second assertion in the same
function would be masked): `test_delete_property_definition_refused` (FR-11),
`test_update_key_refused` (FR-12), `test_update_id_refused`, `test_update_origin_refused`,
`test_update_created_at_refused` - each asserting `42501`. `test_update_index_seq_refused` is
the sixth and is a different kind of test: `index_seq` is `GENERATED ALWAYS AS IDENTITY`, so
Postgres refuses an `UPDATE` targeting it with `428C9` regardless of any grant, before the
privilege check is ever reached. It still belongs in this suite (it documents that
`index_seq` is immutable), but it asserts `428C9`, not `42501`, and does not exercise the
column-level grant at all - folding it into one of the other five would assert the wrong
mechanism.

**FR-11 is implemented in its stronger, unconditional form**: no `DELETE` grant on
`property_definition` at all. The PRD's conditional test ("has it appeared in a published
export?") is never asked, so it can never be got wrong.

**`status` and `deprecated_at` are linked by a CHECK too**:
`CHECK ((status = 'deprecated') = (deprecated_at IS NOT NULL))` - the same
make-it-unrepresentable principle FR-10's binding CHECK applies, applied here to FR-11's
deprecation state, so a deprecated definition with no recorded deprecation timestamp (or an
active one carrying a stale one) cannot exist.

**Cardinality's lower bound is enforced at the `required_for_submission` /
`required_for_publication` gate**, not on every write, so a draft with holes stays saveable
(FR-24). The upper bound is application-enforced by #52 at validation time, backed by - not
fully closed by - the primary key's uniqueness on `ordinal`; see the `property_value`
paragraph above for exactly what that constraint does and does not close.

**The four `origin = 'system'` properties (Discipline, Subgroup, Specimen, Usage guidance)
are seeded by an idempotent application bootstrap command, not `op.bulk_insert`** - a data
migration bypasses the handler, the schema derivation and the binding validation, so it
could seed a definition the running application would itself reject, contradicting PRD
SS6.5's dogfooding claim and #51's own AC that the four built-in properties travel the same
storage code path as an admin-defined one. `Length` is absent from the registry entirely
(FR-85): it is computed in the export and presentation layers only, never stored.

### Rejected alternatives

| Alternative | Why not |
|---|---|
| Runtime DDL (an administrator's UI action alters the schema) | Migration history stops describing the schema; autogenerate proposes dropping every admin-created column; backups stop being interchangeable; the ORM mismatches until restart - violating FR-09 twice. |
| Classic Entity-Attribute-Value | Every value stringified, so range predicates become uncastable, every constraint becomes application code with no backstop, and every export self-joins once per column - PRD SS6.5's "every export a special case." |
| Postgres ENUM for `datatype` | Adding a datatype (FR-77) needs `ALTER TYPE ... ADD VALUE`, a schema change with its own transactional restrictions, landing wherever the migration author puts it - exactly the rebuild FR-77 rules out. |
| `CHECK (datatype IN (...))` | Forces every new datatype's admission through an `ALTER TABLE ... DROP/ADD CONSTRAINT` outside the handler module - the precise violation FR-77 names. |
| Binding as a JSONB sub-document | A code-without-a-binding stays representable, merely refused by application code a future write path can forget to call, instead of being unrepresentable at the schema level. |
| A surrogate FK (`property_value.property_key` referencing `property_definition.id`) | FR-12 already rules out the usual objection to a natural key; a surrogate adds a join to resolve every value's key and turns FR-11/FR-12 back into application logic instead of referential integrity. |
| JSON Schema documents as files on disk | Drifts from the definition row the moment either is edited without the other, with no transactional guarantee tying a file write to the database commit that changed the definition. |
| An admin-authored `json_schema` column | Lets an administrator hand-write invalid JSON Schema directly, and duplicates what is already derivable from `datatype`/`cardinality`/`binding`/`constraints` - two sources of truth that can disagree. |
| A handler-populated `json_schema` cache column | Reintroduces the staleness problem the in-process `(key, row_version)` memoisation exists to avoid - a column-based cache needs its own invalidation trigger (banned) or its own bump logic duplicating `row_version`. |
| A per-value schema version pin | N live schema versions in the catalogue simultaneously, and every reader special-cases which version a value was validated against - the failure this design exists to avoid. |
| One generic whole-table GIN index | Can't express a numeric range predicate for `decimal`/`positiveInt` properties, and mixes selectivity across unrelated properties in one index, defeating the planner's ability to reason about any one property's filter. |
| Granting `nptc_app` ownership of `property_value` | Postgres has no grantable "create index" privilege short of ownership, which also confers `DROP`/`ALTER`/`TRUNCATE` - turns a least-privilege runtime role into the schema owner for one DDL operation. |
| Running the index DDL inline in a request handler | `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block; inline execution either drops `CONCURRENTLY` (a read-blocking lock) or splits the handler across an implicit autocommit boundary mid-request. |
| Building `nptc/jobs/`'s general SKIP LOCKED queue now | The DDL executor is a single, infrequent, schema-privileged operation, not a stream of application jobs - pulls forward work #54 (and the queue's real consumer, P3) doesn't need yet. |
| `ix_property_value_<key>` index names | The key is exactly data-model.md's caveat's likely first victim of 63-byte truncation - a property key long enough to be a good label collides silently with the table-name-based prefix, and two truncated names can collide with each other. |
| A `# noqa`-style guard exemption instead of a fourth NFR-22 rule | An exemption is a hole that widens - the next dynamic-DDL call site copies the exemption rather than the narrower rule it was meant to encode. |
| A trigger for FR-11/FR-12 | PRD Section 14.1 bans business logic in database triggers/functions - invisible to tests and review - and a trigger is bypassable by the table owner in the same way a privilege grant is not. |
| `op.bulk_insert` for the four system properties | A data migration bypasses the handler, the schema derivation and the binding validation entirely, so it can seed a definition the running application would itself reject. |

## Consequences

- #51, #52, #54 and #55 each get a named deliverable from this ADR instead of choosing their
  own shape: #51 the column list, nullability, the `(entry_id, property_key, ordinal)` PK and
  the `entry_id` FK ordering note; #52 the `(key, row_version)` memoisation contract, the
  single `row_version`-owning write path and its test, and cardinality's upper bound as an
  application-enforced rule rather than a schema-closed one; #54 the three index shapes, the
  numeric shape's cast-safety precondition, and the executor shortlist; #55 the six
  privilege-refusal tests (five asserting `42501`, one asserting `428C9` for the generated
  `index_seq` column, which the other five's mechanism does not cover). FR-09's "no migration,
  no restart, no deployment" is this ADR's own acceptance test for #51: it must be verified
  against a running application (add a definition, observe it usable with no migration run
  and no process restart), not inferred from the schema being capable of it in principle.
- data-model.md's 63-byte truncation caveat gains a resolution for #54 specifically (the
  `ix_propval_p{index_seq}_{slot}` scheme is truncation-proof by construction), while
  standing as written for every other future long name.
- `test_sql_parameterisation.py` gains a fourth rule (the narrow `sql.SQL(...)` /
  `Identifier`/`Literal` exemption for the DDL executor module) and new positive-control
  cases alongside it.
- `test_db_round_trip.py`'s fingerprint must additionally reflect
  `information_schema.column_privileges` - the existing `role_table_grants` query is
  table-level and is blind to FR-12's column-level `UPDATE` grant - and must filter generated
  indexes (the `include_object`-excluded names) out of the comparison, or the fingerprint
  would fail the moment #54 creates the first one.
- `backend/migrations/env.py` gains the `include_object` hook; `nptc.db.roles` extends its
  constants pattern to the new column-level grant and the `property_value` grants.
- FR-09 through FR-13 stay `planned` - this ADR is a design record, not evidence of
  implementation (ADR-0002's distinction).
- #137 inherits three named seams to plug a datatype handler into: the `datatype` TEXT
  column with no CHECK/ENUM, the handler-owned `constraints` JSONB column, and the JSON
  Schema derivation/memoisation interface keyed on `(key, row_version)`.
