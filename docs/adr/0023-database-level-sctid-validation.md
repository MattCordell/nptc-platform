# ADR-0023: Database-level SCTID validation via a SQL function

**Status:** Accepted
**Date:** 2026-08-21

## Context

Issue #48 adds `code_binding.code`, the first column in the platform actually bound
to a live SNOMED CT identifier. FR-06 (MUST) is explicit about what that column's own
constraint has to enforce: "The database column MUST carry a check constraint
enforcing `^\d{6,18}$` **plus Verhoeff check-digit validation**." `nptc_shared.sctid`
already implements both halves in Python (`has_valid_format`, `has_valid_check_digit`,
the `SCTID` dataclass) - that is FR-06's application layer, and it is not what this
ADR is about. This ADR is about the database layer FR-06 separately requires.

A Postgres `CHECK` constraint's expression permits no subquery and no CTE. The
regex half (`code ~ '^[0-9]{6,18}$'`) is a plain inline expression, exactly like
`ck_designation_language` or `ck_catalogue_entry_business_key`. The Verhoeff half is
not: it is a fold over the SNOMED-standard Verhoeff D5 dihedral-group tables, one
table lookup per digit, carrying a running checksum from one digit to the next. There
is no way to spell "look up `_D[checksum][_P[position % 8][digit]]` eighteen times,
carrying state forward" as a single boolean expression with no intermediate value -
only as a loop, a recursive query, or a function.

PRD §14.1 is direct about why that should give us pause: "Business logic in database
triggers or functions... is invisible to the test suite, invisible in code review,
and it will be the thing nobody can find in two years." `nptc.db.roles.
CREATE_APP_ROLE_SQL`'s own comment already reads that ban narrowly, as targeting
"stored server-side logic... that runs on every future write", and treats its own
one-shot `DO $$` role-creation block as outside that ban for exactly that reason. This
issue needed the same question answered explicitly, in a versioned decision, rather
than assumed by analogy: does a validation *predicate*, referenced from a `CHECK`,
count as the kind of business logic §14.1 means?

## Decision

**Yes to a function, no to anything with side effects or hidden behaviour.**
`backend/src/nptc/db/functions.py` defines `nptc_sctid_is_valid(code text) RETURNS
boolean`, `LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE`, created by migration 0008
before `code_binding` (whose own `ck_code_binding_code` references it) and referenced
by nothing else. `code_binding.code` stays `TEXT` throughout - this function's
argument and return type carry no risk of the numeric-coercion defect FR-06 exists to
eliminate.

Three properties keep this narrower than what §14.1 is actually warning against:

- **It is a pure predicate, not behaviour.** No side effects, no write, nothing that
  runs "on every future write" the way a trigger would - it only ever appears inside a
  `CHECK`, deciding whether a row is valid, never changing what gets stored.
- **It is visible, not hidden.** It lives in a versioned `backend/src` module and a
  migration, discoverable and reviewable exactly the way `nptc.db.roles`'s grant SQL
  already is - not typed once into a psql session and then invisible to `git log`.
- **It cannot silently diverge from the Python implementation it mirrors.**
  `backend/tests/test_db_sctid_function.py` asserts, over an exhaustive corpus (every
  PRD sample SCTID, boundary lengths, single-digit perturbations of known-valid codes,
  and format junk), that `nptc_sctid_is_valid` agrees with `has_valid_format(v) and
  has_valid_check_digit(v)` for every candidate. A future edit to either side that lets
  them disagree fails CI - the two-years-from-now discoverability problem §14.1 warns
  about is answered by a test, not by hoping nobody touches the SQL.

The `_D`/`_P` tables inside the function are a direct, values-only transliteration of
`nptc_shared.sctid._D`/`_P` (Postgres 1-based array indexing is the only structural
difference), so the two are provably the same table, not independently re-derived and
merely hoped to agree.

## Rejected alternatives

### Regex-only `CHECK`, Verhoeff left to the application layer

Enforce `code ~ '^[0-9]{6,18}$'` at the database layer (matching
`ck_catalogue_entry_business_key`'s own precedent exactly) and rely on
`nptc.catalogue.bindings.create_binding`'s `SCTID(code)` construction for the check
digit.

Rejected: FR-06's own wording is "plus Verhoeff check-digit validation" at the
database column, not only at the service boundary - and the gap is not academic. Any
future bulk-load or backfill path that writes `code_binding` rows without going
through `create_binding` (a seeded import, a data-fix script, a later admin bulk-edit
feature) would have no check-digit protection at all, silently accepting a
transposed-digit SCTID. `requirements.yaml`'s own FR-06 note already flagged this
exact gap as outstanding ("DB check constraint... still owed") before this issue.

### An unrolled inline expression, no function

Spell the eighteen-digit fold as one very long nested `CASE`/array-literal expression
directly inside the `CheckConstraint`, avoiding a function definition entirely.

Rejected: this does not actually avoid the concern §14.1 raises - it is the same
Verhoeff logic, just spelled once per column instead of once per database, and
strictly *less* reviewable (a many-kilobyte inline expression buried in a migration's
`CheckConstraint` string, versus a named, documented, independently-tested function).
It also cannot be reused if a second SCTID-bearing column is ever added elsewhere in
the schema; the function can.

### A trigger that validates on `INSERT`/`UPDATE`

Validate in a `BEFORE INSERT OR UPDATE` trigger instead of a `CHECK`.

Rejected outright: this is precisely the shape §14.1 names - behaviour that runs on
every future write, rather than a constraint a `CHECK` already models more directly.
A `CHECK` constraint is itself declarative schema, inspectable via `\d code_binding`
and asserted by `test_db_migrations.py`'s `compare_metadata`; a trigger is an
additional, separately-discoverable object with no such test coverage in this
codebase's existing patterns.

## Consequences

- This is the repository's first database function. Any future column needing a
  predicate a `CHECK` cannot express inline should follow this same shape - an
  `IMMUTABLE`, side-effect-free function in `nptc.db.functions`, with an exhaustive
  parity test against its Python equivalent - rather than each such case relitigating
  §14.1 from scratch.
- `backend/tests/test_db_round_trip.py`'s fingerprint must include `pg_proc`, or a
  downgrade/upgrade cycle that silently drops and fails to recreate the function would
  pass unnoticed.
- FR-06 is fully implemented from this issue on: string end-to-end in Python
  (`nptc_shared.sctid`, existing), enforced at the database layer (this ADR). FR-07 -
  the spreadsheet export's explicit text formatting - remains a separate, later piece
  of the same requirement, owned by the export renderer.
