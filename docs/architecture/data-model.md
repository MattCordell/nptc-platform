# Data model

The database baseline landed with issue #33 (ADR-0011) - Alembic migrations targeting real
SQLAlchemy metadata, the `pg_trgm`/`unaccent` extensions #138's search depends on, a
least-privilege role model, and a testcontainers integration harness. Everything below
describes what exists today; later issues (#35, #36, #42, #46-#48, #52, #54, #55, #138)
extend it rather than replace it.

This document owns schema *shape* - columns, types, constraints, indexes - plus reasoning
that is genuinely architectural (spans multiple tables or issues). *Why* a single
migration is built the way it is lives in that migration's own docstring; *operational*
facts an operator must act on live in [`upgrade.md`](../operations/upgrade.md) - see
CONTRIBUTING.md's "A schema change's prose has one home each".

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
| `backend/src/nptc/db/models/catalogue_entry.py` | The `catalogue_entry` table (issue #46) |
| `backend/src/nptc/db/models/designation.py` | The `designation` table (issue #47) |
| `backend/src/nptc/db/models/code_binding.py` | The `code_binding` table (issue #48) |
| `backend/src/nptc/db/functions.py` | `nptc_sctid_is_valid`, the database-level Verhoeff check (issue #48, ADR-0023) |
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
tests) - see the `audit_event` section below for what each of the two catches.

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

The table NFR-08 is built on. `prev_hash`/`entry_hash` (NFR-10, the hash chain) landed
with issue #36 (ADR-0017); `scripts/verify_audit_chain.py`, the operator CLI wrapping
`nptc.audit.verification.verify_chain`, landed with #38 (see
`docs/operations/runbooks/verify-audit-chain.md`). The append-only re-assertion after a
downgrade/upgrade round-trip (part of #35's acceptance criteria) was already satisfied by
`backend/tests/test_db_round_trip.py`'s reflection fingerprint, which folds
`information_schema.role_table_grants` into its comparison: a grant present before the
round-trip and gone after it makes `before` and `after` differ, so a grant that
*disappears* across a re-migration is exactly the fingerprint's strongest case, not a blind
spot. The same file's `test_app_role_is_still_refused_update_after_round_trip`,
`..._delete_after_round_trip` and `..._truncate_after_round_trip` tests add a *behavioural*
confirmation on top: real UPDATE/DELETE/TRUNCATE statements, run as the genuinely separate
`nptc_app_login` login (never a superuser connection), against the schema produced by an
actual `downgrade base` -> `upgrade head` round-trip. That stays meaningful independently of
what columns `_fingerprint` happens to query - a grant present in both fingerprints but
ineffective for some other reason would still fail these tests - whereas a grant that was
never made in the first place is the fingerprint's own blind spot, and is instead what
`backend/tests/test_db_audit_privileges.py`'s fresh-database refusal tests cover.

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
| `prev_hash` | `TEXT` | `NOT NULL`, `CHECK (prev_hash ~ '^[0-9a-f]{64}$')` - predecessor's `entry_hash`, or `GENESIS_HASH` (64 `0`s) for the first row |
| `entry_hash` | `TEXT` | `NOT NULL`, `UNIQUE`, `CHECK (entry_hash ~ '^[0-9a-f]{64}$')` - this row's own digest |

### The hash chain (NFR-10, issue #36, ADR-0017)

Each row's `entry_hash` is a SHA-256 digest, computed by
`nptc.audit.hashing.compute_entry_hash`, over a canonical JSON encoding of the row's own
content plus `prev_hash`:
`json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, UTF-8
encoded - the same canonicalisation idiom `transform/src/nptc_transform/report_writer.py`
already uses. A `UUID`/`INET` value is normalised to its string form and a `datetime` to
UTC with a fixed 6-digit microsecond field, so the same instant hashes identically
regardless of the timezone attached to it in memory.

**Field coverage.** The digest covers every column above except two, an exclusion set that
is explicit and tested (`test_audit_hashing.py::test_digest_covers_every_meaningful_column`
derives the expected set from `AuditEvent.__table__.columns` minus the exclusion, rather
than trusting a hand-maintained list not to drift):

- `entry_hash` - the digest itself, so it cannot be an input to its own computation.
- `sequence` - `GENERATED ALWAYS AS IDENTITY`, so its value is unknowable before the
  `INSERT` and cannot be folded into that row's own digest. Ordering is still
  tamper-evident without it: verification walks rows in `sequence` order and each row's
  `prev_hash` must equal its predecessor's `entry_hash`, so a deleted or reordered row
  breaks the chain regardless of `sequence` itself never being hashed. Switching to
  `GENERATED BY DEFAULT` was rejected (ADR-0017): it would reintroduce the separate
  `GRANT USAGE ON SEQUENCE` issue #33 deliberately designed away and let a caller override
  the value.

**Genesis.** The first row's `prev_hash` is `nptc.audit.hashing.GENESIS_HASH` (64 `0`s) -
there is no predecessor for it to point to.

**The append writer** (`nptc.audit.writer.append_audit_event`) is the only sanctioned way
to insert a row. It acquires `pg_advisory_xact_lock` first, to serialise concurrent
appends - two concurrent appenders would otherwise both read the same chain tail and fork
it, the principal failure mode the chain exists to catch. The lock needs no grant (it is
role-agnostic) and is transaction-scoped, releasing automatically on commit or rollback -
not a trigger or stored function, so PRD Section 14.1 is untouched. `occurred_at` comes
from `SELECT clock_timestamp()` (the database clock, never the client - NFR-08), not
`now()`, since `now()` is fixed at transaction start and two events in one transaction
would otherwise share a timestamp. Immediately after `INSERT`, the writer re-reads the row
and recomputes its digest from what Postgres actually stored, raising
`AuditChainWriteError` on any divergence (e.g. a JSONB round-trip normalisation) rather
than letting it surface as an unexplained chain break months later.

**Verification** (`nptc.audit.verification.verify_chain`) streams rows in `sequence`
order, `SELECT`-only, and reports the *first* row where either `prev_hash` no longer
matches the previous row's `entry_hash`, or the row's own recomputed digest no longer
matches its stored `entry_hash`. An empty table and a single-row chain both verify
successfully - explicit acceptance criteria, not accidents of the implementation. It
deliberately does **not** assert:

- **`sequence` contiguity** - a rolled-back transaction legitimately burns an identity
  value, so gaps are expected, not evidence of tampering. Deletion is instead caught by
  the linkage itself: the successor's `prev_hash` no longer matches the previous
  *surviving* row's `entry_hash`.
- **`occurred_at` monotonicity** - `clock_timestamp()` can step backwards across a clock
  adjustment.

**Known limit** (see ADR-0017): an attacker holding table-owner credentials can recompute
the entire chain from the point of edit forward, since nothing here is anchored outside
the database itself. An unanchored chain detects casual tampering, not a determined
rewrite; periodic off-box publication of the head hash would be the mitigation, and remains
out of scope. `scripts/verify_audit_chain.py` (#38) wraps `verify_chain` with an
operator-facing CLI and stable exit codes; it does not close this limit either.

**A distinct gap: tail truncation.** Deleting the most recent N rows off the end of the
chain, rather than editing a row in the middle, leaves a table that still verifies
`ok=True` - `verify_chain` walks forward from genesis and has nothing left after the
truncation point to detect a break against. This is cheaper for an attacker than the
"recompute forward from an edit" limit above, and a different failure mode, not a
restatement of it. `ChainVerification` now carries a `head_hash` (the last accepted
`entry_hash` from the same walk), and `scripts/verify_audit_chain.py` compares it - and the
verified record count - against operator-supplied `--expected-head-hash`/
`--expected-record-count` flags, exiting `4` on a mismatch. This closes the gap only for a
run given that expectation; there is still no automatically-maintained, off-box anchor
store, so a run given neither flag remains as blind to truncation as `verify_chain` alone.

**`append_audit_event` requires `READ COMMITTED` isolation.** The advisory lock (above)
serialises *execution* of concurrent appenders, but under `REPEATABLE READ`/`SERIALIZABLE`
a transaction's snapshot is fixed before it acquires the lock, so a blocked appender that
gets its turn after the previous holder commits can still read a stale tail and fork the
chain - the lock alone is insufficient above `READ COMMITTED`. `append_audit_event` checks
`current_setting('transaction_isolation')` immediately after acquiring the lock and raises
`nptc.audit.writer.AuditIsolationLevelError` unless it is `'read committed'`, rather than
risking a silent fork under a caller's stricter isolation level.

### Field-level before/after (NFR-08, issue #37, ADR-0018)

`before`/`after` are never hand-built by call-site code: `nptc.audit.recording.record_change`
diffs a mapped instance via its own SQLAlchemy attribute history (`nptc.audit.diffing.
diff_instance`), and `record_snapshot_change` diffs a pair of plain snapshots
(`diff_snapshots`) for content with no ORM instance to read history from (e.g. a future JSONB
property bag). Both raise `AuditNoOpError` on an empty diff rather than emit nothing silently -
see ADR-0018 for the full flush-ordering caveat this exists to catch loudly.

**Payload shape.** `before`/`after` are flat JSON objects: one key per changed field (never one
per column - a field nobody touched is completely absent, not present as `null`), each value
normalised by `nptc.audit.serialisation.normalise_json_value` (`SCTID` to its `str` value never
a number - FR-06; `Decimal` to `str`, never `float`; `UUID`/`ipaddress` to `str`; `datetime` to
UTC with a fixed 6-digit microsecond field; recursion into nested mappings/lists). Unlike
`nptc.audit.hashing`'s own normalisation (which must stay total - see below), this normalisation
**raises** on anything it does not recognise, on a NaN/±Inf float, on a non-`str` mapping key,
and on a `str` containing a NUL byte (`U+0000`) - Postgres `jsonb` cannot store one at all, and
ADR-0017 already flagged this as becoming live "once a future caller puts real catalogue content
through this path". `CREATED` yields `before is None`; `DELETED` yields `after is None`.

**The `_redacted` reserved key (NFR-16/NFR-17, PRD OI-15).** A field a model's policy declares
*withheld* is never recorded by value, only by name, under `_redacted`, in both `before` and
`after` - so a change to it is never invisible in the log merely because its value must not be
recorded. `_redacted` has a leading underscore precisely so it can never collide with a real
column name; `nptc.audit.policy.AuditFieldPolicy` refuses any declared field name starting with
`_` for the same reason.

**Allowlist + deny-list, layered (`nptc.audit.policy`).** A model declares
`__audit_fields__` (recorded in full), `__audit_withheld_fields__` (changed-by-name only), and
`__audit_ignored_fields__` (never appears in a diff at all) as `ClassVar`s; `policy_for` (cached)
combines them with the model's real column set from `sqlalchemy.inspect`. A deny-list regex
(`password`, `secret`, `token`, `api_key`, `session_id`, etc.) is checked at policy *construction*
time against every `auditable`/`withheld` name regardless of which of those two lists it was
declared under - an allowlist alone would let a credential-shaped column be pasted straight into
it, and a deny-list alone fails open the moment a new credential-shaped name isn't
pattern-matched. **Every real column must land in exactly one of the three groups, or
construction fails**: without this, a model could declare a policy covering only some of its
columns and leave the rest silently un-audited, and a column added later to an already-classified
model would silently escape auditing by default instead of failing a test. A model with no
`__audit_fields__` at all fails closed (`MissingAuditPolicyError`); a model deliberately never
diffed (`AuditEvent` itself - diffing the log is circular) sets `__audit_fields__ = None` plus a
mandatory `__audit_exempt_reason__`.
`User.__audit_fields__ = {status, closed_at}`, `withheld = {username, display_name,
organisation}`, `ignored = {id, created_at, updated_at}`; `UserIdentity.__audit_fields__ =
{email_verified}`, `withheld = {issuer, subject, email}`, `ignored = {id, user_id, linked_at}`
(`subject` is the OIDC `sub`, NFR-04's own no-escape column - `UserIdentity`'s emit sites are
deferred to a follow-up issue against #43/#44, so this policy exists ahead of anything calling
it, not because it is exercised yet).

**`active_history=True` is required on every auditable/withheld column, and `policy_for`
enforces it.** Without it, SQLAlchemy only knows an attribute's prior value if it was already
loaded before being reassigned - an expired-but-unread attribute reassigned directly leaves
`load_history()` with nothing to report, silently turning a real change into `before: None`
instead of the true prior value. `policy_for` raises if a declared column lacks
`active_history=True`, at policy-construction time - see ADR-0018 for how this was found (an
in-memory `test_audit_diffing.py` reproduction, not a hypothetical).

**Strict vs. total normalisation, and why both exist.** `nptc.audit.hashing._normalise` must
stay total: `compute_entry_hash` also runs over rows read back *from* Postgres (this writer's
own write-time self-check, and `verify_chain`), where raising on unfamiliar content would turn a
verifiable chain into an unverifiable one. `normalise_json_value`'s callers, by contrast, are
about to write a *new*, permanent payload (NFR-09: no UPDATE, no DELETE) - silently stringifying
an unexpected value there is exactly the content loss NFR-08 exists to prevent. `hashing.
_normalise` still recurses into `Mapping`/`list`/`tuple` itself (rather than delegating the whole
structure to `normalise_json_value`) and only delegates leaf-level typing - falling back to
`str(value)` per leaf, not per container, on the exceptions `normalise_json_value` raises. This
preserves this function's pre-issue-#37 behaviour exactly, where one unrecognised nested value
fell back individually without discarding its siblings; delegating the whole recursion would have
silently stringified an entire container for a single unfamiliar leaf. One known, currently
unreachable exception: a NaN/±Inf `float` was tolerated as-is before this issue and now falls back
to `str()` at the leaf, since `normalise_json_value` rejects it - no field this codebase writes
today is ever a `float`, so this cannot yet occur. `test_audit_hashing.py` carries a golden-vector
digest test (computed independently from the literal pre-issue-#37 implementation, not derived
from the refactored code) proving this refactor moved no existing hash.

**No second, unaudited write path (`test_audit_write_path_guard.py`'s `audit-diff-bypass`
rule).** Outside `nptc.audit` itself, a call to `append_audit_event` carrying a `before=`/
`after=` keyword whose value is not the literal `None` is a violation - deliberately narrower
than banning `append_audit_event` outside `nptc.audit` entirely, since a diff-free event (a
future `release.published`, NFR-12's `audit.exported`) is legitimate and must stay directly
callable. What is not legitimate is hand-building a diff instead of calling `record_change`/
`record_snapshot_change`, which is exactly the per-endpoint reimplementation this rule exists to
close off. A companion model-coverage test (`test_audit_redaction.py`) walks every mapped class
and asserts it resolves a policy or carries an explicit exemption.

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
FR-44 requires permission checks, never role-name checks. Role grants are the `user_role`
table below (issue #44, ADR-0019).

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

## `user_role` (issue #44, ADR-0019)

A granted role, and who granted it. `nptc.auth.permissions.Permission`/`Role`/
`ROLE_PERMISSIONS` (the PRD §4.7 matrix itself) are code, not database rows - see
ADR-0019 for why; this table holds only the *grants*.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` |
| `user_id` | `UUID` | `NOT NULL`, FK to `app_user.id` |
| `role` | `TEXT` | `NOT NULL`, `CHECK IN ('observer','provisional','member','reviewer','administrator')` - deliberately excludes `'anon'`, which is never a grantable row |
| `granted_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |
| `granted_by_user_id` | `UUID` | Nullable FK to `app_user.id` - `NULL` only for the one-time bootstrap grant (`scripts/grant_role.py`) |

`UNIQUE (user_id, role)` - one grant per (user, role) pair; no separate index on `user_id`
alone, since this unique constraint's leading column already serves that lookup (unlike
`user_identity`, whose unique is on `(issuer, subject)` and needed its own
`ix_user_identity_user_id`).

**Revocation is a hard `DELETE`, never a `revoked_at` tombstone.** The append-only,
hash-chained `audit_event` table is already the permanent history of every grant and
revocation; a `revoked_at` column would be a second, mutable history able to disagree with
the one that must win. NFR-17's tombstone posture protects *identifying personal data*,
which a role grant is not.

**Privileges: `SELECT, INSERT, DELETE`, plus column-level `UPDATE (granted_at)` only** -
not the blanket no-`UPDATE`-at-all a role-is-never-edited posture would first suggest.
Postgres requires *some* `UPDATE` privilege on a table before it honours `SELECT ... FOR
UPDATE` at all (confirmed against a real container while building this), and
`nptc.auth.grants.assert_not_last_administrator`'s row lock (FR-01, below) depends on
exactly that. `granted_at` is the one column nothing ever writes to after insert, so this
satisfies Postgres's requirement while `user_id`/`role`/`granted_by_user_id` - the columns
that would actually rewrite "who granted this, and when" - stay immutable at the
privilege level.

**FR-01's last-administrator guard** (`nptc.auth.grants.assert_not_last_administrator`) is
an application check, not a database constraint - Postgres cannot express "at least one row
across the whole table satisfies X" as a `CHECK`/`UNIQUE`/`EXCLUDE`, and PRD §14.1 /
ADR-0011 forbid business logic in triggers. It locks every `user_role` row naming
`'administrator'` for an active user (`FOR UPDATE OF ur`) inside the caller's transaction
before permitting a revocation, closure, or suspension to proceed - the row lock is what
makes two concurrent revocations of the last two administrators resolve to exactly one
survivor rather than zero (proven directly, with two real concurrent connections, in
`backend/tests/test_grants.py`).

**Default grant on registration.** `nptc.auth.identity._create_user` grants
`Role.PROVISIONAL` to every newly-created `app_user`, inside the same `SAVEPOINT` as the
identity insert (PRD §4.3: a new user *is* Provisional) - a real row, not an implicit
default, so a dashboard can answer "what roles does this user hold" with one query.

## `catalogue_entry` (issue #46, FR-03, FR-38)

The platform's central entity - see PRD §6.2. This table holds only the **core
columns** (structural, constrained, indexed); registry properties (#51-#55) and
designations/code bindings (#47/#48) are separate tables layered on top.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()`. Internal, never exposed in exports. |
| `business_key` | `TEXT` | `NOT NULL`, `UNIQUE`, `CHECK (business_key ~ '^NPTC-[0-9]{6,}$')`. Immutable (FR-03). |
| `preferred_term` | `TEXT` | `NOT NULL` |
| `status` | `TEXT` | `NOT NULL DEFAULT 'draft'`, `CHECK IN ('draft','active','deprecated','withdrawn')` - `TEXT` + `CHECK`, not a native `ENUM`, matching `app_user.status`'s own precedent (`ALTER TYPE ... ADD VALUE` cannot run in a transaction, and autogenerate mishandles the create/drop pair on downgrade) |
| `specimen_unconstrained` | `BOOLEAN` | `NOT NULL DEFAULT false` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |
| `row_version` | `INTEGER` | `NOT NULL DEFAULT 1`. See "Optimistic locking" below. |
| `preferred_term_key` | `TEXT` | `NOT NULL DEFAULT ''`, indexed. FR-05's comparison key - see "Collision detection" below. |

### `business_key` minting and immutability (FR-03)

`business_key` is minted in Python (`nptc.catalogue.entries.allocate_business_key`),
not as a column `server_default`: the format (`NPTC-` plus a zero-padded sequence
value) has exactly one source of truth this way, shared by the mint path, the
seed-reconciliation path, and the `CHECK` constraint's own regular expression. A
dedicated `catalogue_entry_business_key_seq` sequence backs it - not
`GENERATED ALWAYS AS IDENTITY` (unlike `audit_event.sequence`), because the
application must read the next value and format it into `NPTC-######` *before* the
row exists. Unlike an identity column's backing sequence, a plain sequence's
`nextval()`/`setval()` run with the *inserting* role's own privileges, so `nptc_app`
needs an explicit `GRANT USAGE, SELECT, UPDATE ON SEQUENCE ...` (`UPDATE`, not
`USAGE` alone, is what Postgres actually requires to run `setval` - confirmed
against a real container).

**Reconciliation with the P0 seed (ADR-0010).** The transform mints its own
`business_key`s deterministically and positionally for the seeded baseline; the
backend does not re-mint them. `nptc.catalogue.entries.advance_sequence_past` moves
the sequence past the highest seeded key after import, and is written to never move
it *backwards* (a stale, lower reconciliation call is a no-op) - moving it backwards
unconditionally would silently reissue keys already minted since the last
reconciliation, which is precisely the defect FR-03 exists to prevent.

**Immutability and never-reused are database invariants, not application
convention**, at two independent layers:

- The app role's column-level `UPDATE` grant on `catalogue_entry`
  (`nptc.db.roles.GRANT_CATALOGUE_ENTRY_UPDATE_SQL`) excludes `id`, `business_key`
  and `created_at` - the same trick `app_user`'s own grant plays for `id`/
  `created_at`. `row_version` is deliberately *inside* the grant (see below).
- There is **no `DELETE`/`TRUNCATE` grant on `catalogue_entry` at all**
  (`REVOKE_CATALOGUE_ENTRY_DELETE_SQL`) - deprecation/withdrawal is a `status`
  transition, never a row removal. Combined with `UNIQUE (business_key)` and a
  minting sequence that is monotonic and never rolled back by the application, no
  key is ever freed to be reissued.

A `@validates("business_key")` guard on the ORM model is a second, Python-level
layer that fails loudly on a reassignment attempt rather than surfacing only as an
opaque `InsufficientPrivilege` at flush time; the database grant above is the real
guarantee.

### Optimistic locking (FR-38, NFR-38 test 8)

`row_version` is owned by exactly one write path: SQLAlchemy's mapper-level
`version_id_col` on `CatalogueEntry`'s mapped `UPDATE` - never a trigger (PRD §14.1
bans business logic in triggers/functions), never a manual bump, never
database-generated (Postgres has no built-in per-row version counter). This is the
same doctrine ADR-0012 already fixed for `property_definition.row_version`; a Core
`sqlalchemy.update(...)`/`delete(...)` statement against `catalogue_entry` bypasses
`version_id_col` enforcement even though it still goes through the ORM `Session` -
`backend/tests/test_sql_parameterisation.py`'s AST guard (`VERSIONED_TABLE_MODELS`)
fails the build on exactly that shape, under `backend/src` only (a migration's own
one-off backfill is a legitimate bulk statement; a domain write path reaching for
one is not).

`nptc.catalogue.entries.save_entry` enforces FR-38 at two layers, deliberately:

1. An explicit precondition check against the caller's `expected_row_version`,
   *before* any attribute is mutated. This is the path that can build a useful
   conflict report, because both the caller's stale view and the current row are in
   hand uncorrupted - the report names which submitted fields actually differ from
   the current stored value, plus the row's current version and the most recent
   change's attribution.
2. `version_id_col` itself, as the backstop for a genuine race between two callers
   who both pass check 1 and then interleave between load and flush - surfaced as
   SQLAlchemy's own `StaleDataError`, caught inside a per-entry `session.begin_nested()`
   savepoint so only that entry's attempted write rolls back, not the caller's whole
   transaction.

**A rejected save never leaves an audit event behind.** Layer 1 raises before
`nptc.audit.recording.record_change` is ever called. Layer 2's `StaleDataError` is
raised by the `session.flush()` inside `nptc.audit.writer.append_audit_event`'s own
append sequence, which runs *before* that function ever constructs or adds an
`AuditEvent` row - so the savepoint rollback discards only the attempted (and
never-persisted) `UPDATE`. The savepoint must be opened before any attribute is
mutated, not after: opening one autoflushes already-pending state, and doing that
after mutating an entry's attributes would flush the change straight to the
database and clear SQLAlchemy's attribute history before the diff is ever taken -
turning every save into a spurious empty-diff failure regardless of whether it
actually conflicts.

`nptc.catalogue.entries.save_entries` applies a batch of updates through this same
per-entry path (one `save_entry` call, one savepoint, per entry) rather than a
single Core bulk statement - the seam #63's bulk reclassify is meant to call instead
of inventing one under deadline.

## `designation` (issue #47, FR-04, FR-24, FR-37, FR-85)

Catalogue-side designations - synonyms and non-en-AU preferred-term variants - see PRD
§6.3. FR-05's collision detection (issue #49) is layered on top of the rows this
table creates - see "Collision detection" below.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` |
| `entry_id` | `UUID` | `NOT NULL`, FK to `catalogue_entry.id`. Immutable - see below. |
| `term` | `TEXT` | `NOT NULL`, `CHECK (length(btrim(term)) > 0)`. Cleaned at entry (FR-63) - see below. |
| `term_key` | `TEXT` | `NOT NULL DEFAULT ''`, indexed. FR-05's comparison key - see "Collision detection" below. |
| `use` | `TEXT` | `NOT NULL DEFAULT 'synonym'`, `CHECK IN ('preferred','synonym')` |
| `language` | `TEXT` | `NOT NULL DEFAULT 'en-AU'`, `CHECK` against a BCP-47 well-formedness regex |
| `status` | `TEXT` | `NOT NULL DEFAULT 'active'`, `CHECK IN ('active','retired')` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |

**There is no `length` column, anywhere (FR-85/FR-24)** - computed `@property`, never
storable or editable; see `0007_designation.py`'s docstring for why. PRD §6.5 defines
`Length` as the *RCPA/catalogue* preferred
term's character count, which lives on `catalogue_entry.preferred_term` (issue #46),
never on a `designation` row (see "Where the preferred term lives" below) -
`CatalogueEntry.length` is therefore the field FR-85 actually publishes.
`Designation.length` applies the same computation to a designation's own `term` (a
synonym or a non-en-AU preferred variant), for the same reason, but is a distinct,
non-authoritative figure. Both are bare Python `@property`s computed by
`nptc.catalogue.term_hygiene.preferred_term_length`
(`len(nptc_shared.text.normalise_for_comparison(term))`), with deliberately no
setter. PRD §6.5's migration note is the test that actually matters here: because the
current `=LEN()` formula counts a trailing non-breaking space, cleaning that
whitespace reduces the published length for roughly one entry in five - covered
directly on `CatalogueEntry.length` in `backend/tests/test_catalogue_designations.py`.

### Where the preferred term lives (not duplicated)

There are three preferred-term-shaped strings in this platform, in three different
places with three different edit postures:

| String | Home | Editable? |
|---|---|---|
| RCPA/catalogue preferred term | `catalogue_entry.preferred_term` (issue #46) | Yes - user-maintained, exists before any code binding is created |
| SNOMED CT-AU preferred term | `code_binding.au_preferred_term` (issue #48, below) | No - stored exactly as served (FR-82) |
| SNOMED CT Fully Specified Name | `code_binding.fsn` (issue #48, below) | No - as served, semantic tag intact (FR-82) |

`designation` holds only the first kind, and only its non-en-AU variants - the
catalogue's own en-AU preferred term stays exactly where issue #46 put it,
`catalogue_entry.preferred_term`, never duplicated into a row here.
`ck_designation_no_en_au_preferred` (`NOT (use = 'preferred' AND language = 'en-AU')`)
makes that a database invariant, not a convention: a non-en-AU catalogue-authored
preferred variant (e.g. `use='preferred', language='mi-NZ'`) is still permitted. See
`docs/adr/0022-designation-storage.md` for the full reasoning and the rejected
alternatives (mirroring the preferred term into both tables; dropping
`catalogue_entry.preferred_term` entirely).

A SNOMED CT-served label is never written into `designation` - doing so would
destroy FR-82's as-served guarantee and make an unchangeable label editable through
this table's write path. Code bindings (#48) are what makes the served labels
visible to the platform at all.

## `code_binding` (issue #48, FR-06, FR-08, FR-82, FR-83)

The terminology server's served labels for a `catalogue_entry` - see PRD §6.4.
FR-84's subsumption check (issue #48's sweep-level neighbour) is layered on top of
the rows this table creates and is not implemented here. FR-08's blocking severity
(one active code, bound to at most one entry) *is* implemented here - see
"Collision detection" below.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` |
| `entry_id` | `UUID` | `NOT NULL`, FK to `catalogue_entry.id`. Immutable - see below. |
| `system` | `TEXT` | `NOT NULL DEFAULT 'http://snomed.info/sct'`, `CHECK` not-blank. Immutable - see below. |
| `code` | `TEXT` | `NOT NULL`. **Never numeric, at any layer (FR-06).** `CHECK (nptc_sctid_is_valid(code))` - format (`^[0-9]{6,18}$`) and Verhoeff, both at the database layer. Immutable - see below. |
| `fsn` | `TEXT` | `NOT NULL`, `CHECK` not-blank. Stored exactly as served, semantic tag intact (FR-82) - no cleaning hook of any kind. |
| `au_preferred_term` | `TEXT` | Nullable (not every edition serves an AU preferred term), `CHECK` not-blank when present. Stored exactly as served (FR-82). |
| `edition_hint` | `TEXT` | `NOT NULL DEFAULT 'unknown'`, `CHECK IN ('au','int','unknown')` |
| `status` | `TEXT` | `NOT NULL DEFAULT 'active'`, `CHECK IN ('active','retired')` |
| `replaced_by_binding_id` | `UUID` | Nullable, self-FK to `code_binding.id`. Only settable while retiring (see below). |
| `retirement_reason` | `TEXT` | Nullable. Mandatory exactly when `status = 'retired'` (see below). |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |

### FR-82: stored exactly as served, no cleaning hook

Unlike `designation.term`/`catalogue_entry.preferred_term`, `fsn` and
`au_preferred_term` have **no `@validates` hook at all** - not `clean_term`, not any
whitespace normalisation. A stored value that has been transformed cannot be
distinguished from one that has not, and that ambiguity is the entire source of
FR-83's tag-stripping hazard (below); storing as served makes every value's state
unambiguous by construction. `backend/tests/test_catalogue_bindings.py` asserts this
structurally: `nptc.db.models.code_binding`'s source has no reference to
`clean_term`/`normalise_for_comparison`/`strip_semantic_tag`.

### FR-06: the database enforces format and Verhoeff, not just Python

`ck_code_binding_code` calls `nptc_sctid_is_valid(code)` - a `LANGUAGE sql IMMUTABLE`
function (`nptc.db.functions`, migration 0008) that folds the Verhoeff D5 tables the
same way `nptc_shared.sctid.has_valid_check_digit` does in Python, because a `CHECK`
constraint permits no subquery or CTE and the fold cannot be spelled as a plain
inline expression. See `docs/adr/0023-database-level-sctid-validation.md` for why a
database function is the right shape here and not the kind of hidden business logic
PRD §14.1 warns against, and `backend/tests/test_db_sctid_function.py` for the parity
test that keeps it from silently diverging from the Python implementation.

### FR-08: retired, never deleted

- `ck_code_binding_retirement_reason`: `(status = 'retired') = (retirement_reason IS
  NOT NULL AND length(btrim(retirement_reason)) > 0)` - mandatory on retirement,
  forbidden while active.
- `ck_code_binding_replaced_by_requires_retired`: `replaced_by_binding_id IS NULL OR
  status = 'retired'` - a binding can only name a successor once it is itself
  retired.
- `ck_code_binding_no_self_supersession`: a binding cannot name itself as its own
  replacement.
- `ix_code_binding_one_active_per_entry` - `UNIQUE (entry_id) WHERE status = 'active'`
  - at most one active binding per entry.
- `ix_code_binding_one_active_entry_per_code` (issue #49) - `UNIQUE (system, code)
  WHERE status = 'active'` - the code side of the same invariant: one active code
  cannot be bound to two *different* entries. See "Collision detection" below.

Grants: `SELECT, INSERT` at table level, column-level `UPDATE (fsn,
au_preferred_term, edition_hint, status, replaced_by_binding_id, retirement_reason,
updated_at)` - excluding `entry_id`, `system` and `code`, so rebinding to a different
concept is a retire-and-replace, never an in-place edit - and no `DELETE`/`TRUNCATE`
grant at all. `fsn`/`au_preferred_term` remain updatable so the FR-45 validation
sweep can refresh a drifted served label from the terminology server; that is a
refresh from the wire, never a re-derivation.

### FR-83: the semantic tag strip has exactly one call site

`nptc.exports.semantic_tag.render_display_term` is the one place a served FSN's
final parenthesised group is ever removed. It wraps
`nptc_shared.terminology.strip_semantic_tag` (which exists separately for FR-97's
seeding-time reconciliation, and deliberately returns its input unchanged rather than
raising when there is no trailing group) with the two defensive assertions FR-83
requires of an export that runs unattended: the input must end with a parenthesised
group, or the value is not a served FSN and the export fails loudly (`NotAServedFSNError`)
rather than publish it; the result must be non-empty (`EmptyDisplayTermError`).
`391483001`'s FSN, `"Microscopy (acid fast bacilli) (procedure)"`, is the regression
fixture - it renders as `"Microscopy (acid fast bacilli)"`. A second application of
the rule to *that* output would not raise - `"(acid fast bacilli)"` is itself a
valid-looking trailing group, so it would silently over-strip to `"Microscopy"` - which
is exactly why FR-83 makes the no-double-strip guarantee structural (a served,
never-transformed `fsn` column feeding exactly one call site) rather than something
`render_display_term` could ever detect from its input alone. `backend/tests/
test_catalogue_bindings.py` asserts structurally, across `backend/src`, `transform/src`
and `shared/src`, that the strip is referenced from no module outside `nptc.exports` -
except the two pre-existing FR-97 seeding-reconciliation sites (ADR-0006) and the
shared package's own re-export of the functions themselves.

### Two partial unique indexes

- `ix_designation_one_active_preferred_per_entry_language` - `UNIQUE (entry_id, language)
  WHERE status = 'active' AND use = 'preferred'` - at most one active preferred
  designation per `(entry_id, language)`.
- `ix_designation_no_duplicate_active_term` - `UNIQUE (entry_id, term_key, language)
  WHERE status = 'active'` (re-keyed on `term_key` in issue #49; originally `term`) -
  no duplicate active synonym under FR-05's comparison fold, not merely a byte-for-byte
  duplicate.

Both are scoped to `status = 'active'` so a retired row never blocks a fresh one from
being added under the same term. See `0007_designation.py`'s docstring for why these are
enforced at the database layer rather than by application convention.

## Collision detection (issue #49, FR-05, FR-08)

Three severities, three different postures - `nptc.catalogue.collisions`'s own module
docstring is the authoritative account; this section is the schema-shape summary.

**Error - the save is rejected.** A synonym that exactly matches another live entry's
preferred term, or the symmetric case (a preferred term matching another live entry's
active synonym or preferred term) - FR-05 only states the first direction, but the
PRD's own A.5 fixture is symmetric: whichever side is edited second creates the
identical ordering hazard. `nptc.catalogue.collisions.assert_no_error_collisions` runs
before any row is constructed in every write path of `nptc.catalogue.designations` and
`nptc.catalogue.entries`, so a rejected save leaves no audit event - the same
precondition-before-mutation posture FR-38's optimistic locking already holds.

**Warning - the save is permitted.** The same synonym on multiple live entries (PRD
A.5's `'ADA2'`, genuinely attached to three adenosine deaminase entries disambiguated
by specimen). Never raised - `nptc.catalogue.collisions.warning_collisions` is a query
a caller (#149's edit screen) asks, and `acknowledge_collision` (gated on
`Permission.VALIDATION_ACKNOWLEDGE`, FR-44) records that the warning has been seen and
accepted for one entry, so it does not recur on that entry's next save.

**Blocking - unrepresentable.** FR-08's other half: one active SNOMED code bound to at
most one entry across the whole catalogue, not merely per entry -
`ix_code_binding_one_active_entry_per_code` (above) is the database invariant;
`nptc.catalogue.bindings.create_binding`'s `CodeBindingCodeAlreadyBoundError` is the
pre-insert domain error. No acknowledgement path: a code is either free or it isn't.

### Error severity is check-then-insert, so it takes an advisory lock

Unlike the blocking severity above, FR-05's error check has no cross-row, cross-table
`UNIQUE` index to fall back on - "no two live rows, across either of two tables, share
this key" is not expressible as plain DDL, and a trigger is not the answer (PRD
§14.1). `assert_no_error_collisions` therefore takes the same precaution
`nptc.audit.writer.append_audit_event` already does for its own read-then-write race:
`pg_advisory_xact_lock(hashtext(key))`, acquired before the comparison queries run,
serialises exactly the transactions contending for the *same* key (an unrelated key
hashing to the same lock only costs extra, harmless serialisation, never a false
negative) and releases automatically at commit/rollback. This relies on
`nptc.db.session.REQUIRED_ISOLATION_LEVEL` already pinning every connection to `READ
COMMITTED`, the same guarantee `append_audit_event`'s own runtime check re-verifies for
its higher-stakes NFR-10 purpose - collision detection trusts the connection-level
setting rather than re-checking it itself.

### The comparison key: casefolded, punctuation-folded, not merely whitespace-cleaned

FR-05 requires normalising case, Unicode whitespace *and punctuation* before
comparing - strictly more than `nptc.catalogue.term_hygiene.clean_term`'s own
whitespace-only fold (`nptc_shared.text.normalise_for_comparison`, which deliberately
preserves case and punctuation for FR-82's own reasons). `nptc_shared.similarity.
collision_key` composes that module's existing `tokenise`/`token_key` primitives
instead of adding a second implementation: punctuation and whitespace both become
token separators, and each token is casefolded - so `'17-OHP'`/`'17 OHP'` collide,
`'ADA2'`/`'ada2'` collide, but `'AntiDNA'` and `'Anti-DNA'` do not silently merge into
one token.

The key is **stored and indexed**, not recomputed per comparison:
`designation.term_key` and `catalogue_entry.preferred_term_key` are written by the
same `@validates` hook that already cleans the underlying term
(`Designation._validate_term`, `CatalogueEntry._validate_preferred_term`), so the two
can never drift apart, and `nptc.catalogue.collisions` reads them via a plain indexed
equality lookup (`ix_designation_term_key`, `ix_catalogue_entry_preferred_term_key`)
rather than scanning every entry on every save. Both carry `server_default = ''` -
not a correct key for any real term, only so a raw `INSERT` that bypasses the ORM
(every `backend/tests/test_db_*.py` constraint/privilege test) still satisfies
`NOT NULL`; every write through `Designation`/`CatalogueEntry` themselves always
supplies the real, computed value.

### Candidate scope: `draft` and `active`, not only `active`

FR-05's own wording is "a different *active* entry", but this is deliberately wider:
`deprecated`/`withdrawn` entries never collide (the PRD's own acceptance criterion),
while a `draft` entry colliding with another live entry is exactly the same hazard the
moment either is published - catching it before that point is strictly safer than the
literal reading. `nptc.catalogue.collisions._LIVE_STATUSES` is the one place this is
spelled out.

### `designation_collision_acknowledgement` (warning-severity acknowledgement)

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` |
| `entry_id` | `UUID` | `NOT NULL`, FK to `catalogue_entry.id` |
| `term_key` | `TEXT` | `NOT NULL`, `CHECK` not-blank |
| `language` | `TEXT` | `NOT NULL DEFAULT 'en-AU'`, `CHECK` against the same BCP-47 regex `designation.language` uses |
| `acknowledged_by_user_id` | `UUID` | Nullable FK to `app_user.id` - `NULL` for a system-attributed acknowledgement |
| `reason` | `TEXT` | `NOT NULL`, `CHECK` not-blank |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL`, `now()` |

`ix_designation_collision_ack_entry_term_language` - `UNIQUE (entry_id, term_key,
language)` - scopes an acknowledgement to the entry it was made against, not to the
term key alone: a fourth entry later joining an already-acknowledged group (PRD A.5's
`'ADA2'`) still warns once, on its own save, rather than silently inheriting another
entry's editorial decision. Grants: `SELECT, INSERT` only - `UPDATE, DELETE, TRUNCATE`
are revoked, since an acknowledgement is a record of a decision at a point in time,
never edited or withdrawn (withdrawal is out of scope for #49).

**Deliberately not PRD §6.1's `ValidationFinding` lifecycle** (`open` / `acknowledged`
/ `resolved` / `superseded`). That entity is P3 (`nptc.validation` is still a
placeholder module); FR-05's own acknowledgement requirement cannot wait on it. This
table is narrow and purpose-built for exactly one finding shape, and is expected to be
subsumed by `ValidationFinding` once it lands, not to sit alongside it indefinitely.

### Term hygiene at entry (FR-63)

`nptc.catalogue.term_hygiene.clean_term` (called from both `CatalogueEntry`'s own
`@validates("preferred_term")` hook and `Designation`'s own `@validates("term")` hook -
one shared function, since FR-85's published length depends on the same cleaning
having happened on both fields) collapses every normalisable space - a non-breaking
space, a narrow no-break space, PRD Appendix A.1 - to an ordinary space and strips the
edges, via the same `nptc_shared.text.normalise_for_comparison` the P0 transform and
FR-05 collision detection share (ADR-0001). Anything that survives that pass - a
zero-width space, a bidi override, a genuine control character - has no single
correct repair, so it is rejected (`TermCleaningError`) rather than silently
dropped, quoting the offending character escaped (`escape_invisible`), never raw
(NFR-38 test 2).

`Designation.language` is validated the same way, at both layers:
`nptc_shared.language.is_well_formed_language_tag` backs both the model's own
`@validates("language")` hook (raising `DesignationLanguageError`) and
`ck_designation_language`'s `CHECK` constraint, the latter built from
`LANGUAGE_TAG_PATTERN.pattern` rather than hand-copied so the two can never silently
diverge (`backend/tests/test_db_designation.py::
test_designation_language_check_matches_the_shared_pattern` pins this).

### FR-04: synonyms are rows, never a delimited string

There is no delimited-string column anywhere in this table - each synonym is its own
row, added via `nptc.catalogue.designations.add_synonyms`. The backend does not
reimplement the delimiter-splitting logic: `transform/src/nptc_transform/
cell_defects.split_synonyms` (already the ADR-0001-shared implementation for the P0
seed import) is what turns a spreadsheet cell like `'ADA RBC, ADA red cells'` or
`'Zovirax;;Cyclir'` into the individual strings this table's rows are built from.

### Never `DELETE`d, only retired

A designation that stops being current moves to `status='retired'` (mirroring
`CatalogueEntryStatus.WITHDRAWN`'s own precedent), never removed. Grants:
`SELECT, INSERT` at table level, column-level `UPDATE (term, use, language, status,
updated_at)` - excluding `entry_id`, so a designation is retired and re-created on a
different entry, never reparented - and no `DELETE`/`TRUNCATE` grant at all. See
`0007_designation.py`'s docstring for the reasoning.

### FR-37: every write requires a changelog note

`nptc.catalogue.changelog.validate_changelog_note` is the server-side authority every
write path in this table (and `catalogue_entry`'s own `create_entry`/`save_entry`/
`save_entries`) calls before any mutation: an empty note, one matching a
low-information list (`'update'`, `'fix'`, `'.'`, and neighbours), or one shorter
than ten characters after normalisation is refused. There is no exemption - the
ADR-0010 seeded-import path supplies `SEED_IMPORT_NOTE`, a real sentence that passes
validation on its own merits.

## Property registry (issue #51, FR-09, FR-10, FR-11, FR-12)

`property_definition`/`property_value` land with #51, per ADR-0012's design record - see that
ADR for the full reasoning, including the rejected alternatives (runtime DDL, classic EAV)
and the FR-13 index executor's still-open question, which stays open until #54. #52 (JSON
Schema validation), #54 (automatic index generation) and #55 (deprecation/key immutability
workflow) still build on top of what is described here.

`property_definition` is a conventional relational table, not a document:

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK |
| `index_seq` | `BIGINT` | `NOT NULL`, `GENERATED ALWAYS AS IDENTITY`, used only to build a truncation-proof generated index name (never the property key) |
| `key` | `TEXT` | `NOT NULL`, `UNIQUE`, `CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$')`, immutable (FR-12) |
| `label` | `TEXT` | `NOT NULL`. Human-facing, changeable |
| `datatype` | `TEXT` | `NOT NULL`. No CHECK, no ENUM - FR-77's handler-module extension point; #137's ADR owns the valid set and how it is checked at write time |
| `cardinality` | `TEXT` | `NOT NULL`, CHECK against `0..1` / `1..1` / `0..*` / `1..*` |
| `scope` | `TEXT` | `NOT NULL`, CHECK against `submission` / `maintenance` / `both` (PRD SS6.5) |
| `required_for_submission` | `BOOLEAN` | `NOT NULL` |
| `required_for_publication` | `BOOLEAN` | `NOT NULL` |
| `binding_target` | `TEXT` | Nullable. `value_set` or `local_code_system` (FR-10); `NULL` unless `datatype = 'code'`, and a second `CHECK` (`binding_fields_require_target`) closes the reverse direction: `value_set_uri`/`strength`/`edition` must all be `NULL` whenever `binding_target` is |
| `value_set_uri` | `TEXT` | Nullable. `CHECK` requires it when `binding_target = 'value_set'` |
| `strength` | `TEXT` | Nullable. `required` / `extensible` / `example` (FR-10) |
| `edition` | `TEXT` | Nullable. Which SNOMED edition the value set resolves against - unconstrained text, no vocabulary CHECK (ADR-0012 does not fix this vocabulary), but still subject to `binding_fields_require_target` above |
| `constraints` | `JSONB` | `NOT NULL DEFAULT '{}'`. Handler-owned datatype parameters, this table only reserves the column - interior validation is #137's ADR |
| `filterable` | `BOOLEAN` | `NOT NULL`. Will drive #54's index generation (FR-13) |
| `origin` | `TEXT` | `NOT NULL`. `system` or `admin` |
| `status` | `TEXT` | `NOT NULL DEFAULT 'active'`. `active` or `deprecated` - no delete (FR-11) |
| `display_order` | `INTEGER` | `NOT NULL` |
| `deprecated_at` | `TIMESTAMPTZ` | Nullable. `CHECK` ties it to `status = 'deprecated'` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `row_version` | `INTEGER` | `NOT NULL DEFAULT 1`. Will be the cache key (with `key`) for #52's in-process JSON Schema memoisation - owned by exactly one write path, the ORM's `version_id_col` on this table's mapped `UPDATE` (see ADR-0012), and covered by `test_sql_parameterisation.py`'s `VERSIONED_TABLE_MODELS` guard alongside `catalogue_entry.row_version` |

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
upper bound at validation time. `property_value.entry_id` carries a real FK to
`catalogue_entry(id)`: ADR-0012 flagged this FK as unavailable when it was written, but #46
landed first, so migration 0010 adds it directly rather than deferring it to a follow-on
migration. `property_key` also gets its own plain btree index
(`ix_property_value_property_key`) - it is only the *second* column of the composite PK, so
without a standalone index both FK-side maintenance on `property_definition` and a "which
entries use this property" lookup (#55's deprecation workflow) would be a sequential scan.

`nptc_app` gets `UPDATE` at column level on `property_definition`, excluding `key`, `id`,
`index_seq`, `origin` and `created_at`, and no `DELETE` grant at all (FR-11's unconditional
form) - this is FR-12 as a database invariant, not an ORM convention. `property_value` gets
ordinary `SELECT`/`INSERT`/`UPDATE`/`DELETE`, since removing a value is normal editing, not
the case FR-11 protects against.

The four `origin = 'system'` properties - `discipline`, `subgroup`, `specimen`,
`usage_guidance` - are seeded by `nptc.registry.bootstrap.seed_system_properties`, an
idempotent application-level function that inserts through `PropertyDefinition`'s own mapped
`INSERT`, never `op.bulk_insert` (ADR-0012): a data migration would bypass the validation a
real write goes through. It is safe to call repeatedly - it re-checks existing `key`s against
the database on every call - which is also FR-09's own acceptance test for this issue: adding
a property is ordinary row data, so nothing about seeding (or an administrator adding a fifth
property afterwards) requires a migration, a restart, or a deployment.

FR-13's generated indexes (`ix_propval_p{index_seq}_{slot}`, see the truncation caveat above)
will be excluded from Alembic autogenerate and this file's own round-trip fingerprint via an
`include_object` hook in `env.py` when #54 lands - without it, the first index #54 creates
would fail the downgrade/upgrade comparison in `test_db_round_trip.py`.

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
