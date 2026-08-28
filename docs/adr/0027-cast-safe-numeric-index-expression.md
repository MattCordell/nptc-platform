# ADR-0027: Cast-safe numeric index expression via a SQL function

**Status:** Accepted
**Date:** 2026-08-27

## Context

Issue #54 (FR-13) generates a database index the moment a property is flagged
filterable. ADR-0012 already fixed the three index shapes this needs, one per
`ValueExpression`: a `jsonb_path_ops` GIN for `code`, an expression btree on
`(value #>> '{}')` for `string`/`url`, and - the shape this ADR is about - an expression
btree over `decimal`/`positiveInt` properties, which ADR-0012 spelled as
`((value #>> '{}')::numeric)`.

That expression is not safe to build. `property_value.value` is `JSONB`, populated by
whatever a `decimal`/`positiveInt` property's handler accepted at write time, and
`nptc.registry.schema`'s validation is not retroactive: a value that conformed under
one version of a property's `constraints` can be narrowed later (a max-length or a
range amendment) without rewriting rows already on record, and a synthetic or
misconfigured handler could in principle write a non-numeric string under either
datatype regardless. `CREATE INDEX` evaluates its expression against every existing
row up front - the moment one retained value is not castable to `numeric`, the
`::numeric` cast raises and the whole `CREATE INDEX` fails outright, for every row, not
just the offending one. `IndexShape.requires_conformance_sweep = True` on both
handlers already flags this risk; the sweep itself is P3, unbuilt (`nptc.jobs` is a
one-line docstring stub), so #54 cannot defer past it - the sweep gating index
generation would mean neither the numeric index shape nor its `EXPLAIN` proof exist
until a much later issue, leaving one of ADR-0012's three shapes untested indefinitely.

This needed the same question ADR-0023 already answered for `nptc_sctid_is_valid`,
asked again on its own facts: does a cast-safety predicate, referenced only from an
index expression, count as the stored business logic PRD §14.1 bans?

## Decision

**Yes to a function, for the same three reasons ADR-0023 already established, plus one
new finding this function's own construction turned up.**

`backend/src/nptc/db/functions.py` defines `nptc_numeric_or_null(v text) RETURNS
numeric`, `LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE`, created by migration `0014`
before any numeric-shaped generated index can reference it:

```sql
CREATE OR REPLACE FUNCTION nptc_numeric_or_null(v text)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
SELECT CASE WHEN pg_input_is_valid(v, 'numeric') THEN v::numeric END
$$;
```

The generated index expression becomes `nptc_numeric_or_null(value #>> '{}')`. A row
whose value cannot be interpreted as `numeric` indexes as `NULL` - the index builds
successfully for every row, and that one row is simply unfindable by a range filter,
which is the correct outcome for a value with no numeric meaning at all. `nptc.
registry.datatypes.decimal`/`positive_int`'s `filter_clause` (issue #54, Phase 3) uses
the identical expression, so the index and the query that would use it can never drift
apart the way two independently-written expressions could.

Three properties keep this narrower than what §14.1 warns against - ADR-0023's own
argument, which applies unchanged:

- **A pure predicate, not behaviour.** No side effects, no write; it only ever appears
  inside an index expression or a `filter_clause`, deciding how a value is interpreted,
  never changing what is stored.
- **Visible, not hidden.** Lives in a versioned `backend/src` module and a migration,
  discoverable exactly the way `nptc_sctid_is_valid`/`nptc_search_text` already are.
- **Cannot silently diverge from its one caller's expectation.** `backend/tests/
  test_db_numeric_or_null_function.py` and the datatype handler parity test (Phase 3)
  both assert the function and `filter_clause` agree, so a future edit to either side
  that lets them disagree fails CI.

### The finding this function's own construction produced

`pg_input_is_valid` - the built-in this wraps - is itself declared `STABLE`, not
`IMMUTABLE` (verified directly against `postgres:18.6`, the version `deploy/compose.yml`
pins: `SELECT provolatile FROM pg_proc WHERE proname = 'pg_input_is_valid'` returns
`s`). Declaring `nptc_numeric_or_null` `IMMUTABLE` around a `STABLE` primitive is only
sound because the target type here is the single fixed literal `'numeric'`, never a
caller-supplied value - `pg_input_is_valid`'s blanket `STABLE` marking covers every
possible target type it can be asked to validate against, some of which genuinely are
session- or catalog-state-sensitive (an `enum` whose members can change, a
`timestamptz`-adjacent type whose interpretation depends on the session's time zone).
Numeric literal parsing has no such dependency: it does not consult `search_path`, the
session time zone, or any mutable catalog state. This is exactly `nptc_search_text`'s
own precedent (`nptc.db.functions`, migration `0012`): the one-argument `unaccent(text)`
is `STABLE` because it resolves its dictionary through `search_path`, but the
two-argument form with an explicit, fixed dictionary is genuinely immutable for that
fixed definition. `nptc_numeric_or_null` makes the same trade for a fixed target type
rather than a fixed dictionary.

Should this ever need revisiting - if a future Postgres version's `pg_input_is_valid`
turns out to have some numeric-specific state-dependency this ADR did not anticipate -
the documented fallback is a `LANGUAGE plpgsql` function with an `EXCEPTION WHEN others
THEN RETURN NULL` block around `v::numeric`. Not used here because it opens a
subtransaction per row on every index build and every insert; `pg_input_is_valid`
avoids that cost entirely.

### `IndexShape.requires_conformance_sweep` is not made redundant by this

The field stops being an index-generation precondition (which this ADR's whole point is
that it must not be) and becomes what it should always have been: a signal to the P3
conformance sweep that this datatype can carry values on record that are syntactically
present but not semantically numeric. The sweep's job is to *report* such rows to an
administrator so the underlying data can be corrected; it was never the index's job to
wait for that report before it could exist at all.

## Alternatives considered

### Gate numeric index generation on a zero-finding P3 conformance sweep

ADR-0012 itself named this as the other option: require the sweep to have passed with
zero outstanding findings for a property before generating its numeric index shape.

Rejected. The sweep is P3 and unbuilt (`nptc.jobs` is a one-line docstring stub, and
ADR-0012's own alternatives-rejected table already forbids #54 from pulling the P3 job
queue forward). Gating on it would mean the third of ADR-0012's three index shapes ships
dead and untested - no seeded system property is `decimal`/`positiveInt`, so the gap
would not even be visible until an administrator added one, at which point indexing
would silently do nothing rather than fail loudly. A cast-safe expression means all
three shapes are real, tested, and load-bearing from this issue on.

### A `plpgsql` exception-block function as the default, not just the documented fallback

Reject `pg_input_is_valid` up front and always wrap `v::numeric` in `LANGUAGE plpgsql`
with `EXCEPTION WHEN others THEN RETURN NULL`.

Rejected as the default: a `plpgsql` exception handler opens a subtransaction for every
invocation, which means every row of a `CREATE INDEX` build and every future `INSERT`
into a numeric-shaped filterable property pays that cost. `pg_input_is_valid` performs
the same validity check via the type's own input function machinery, with no
subtransaction. Kept as the documented fallback (above) rather than discarded outright,
in case a future Postgres version changes `pg_input_is_valid`'s behaviour in a way this
ADR's "no numeric-specific state dependency" argument no longer holds for.

## Consequences

- This is the repository's third database function, following `nptc_sctid_is_valid`
  (ADR-0023) and `nptc_search_text` (ADR-0024). `nptc.db.functions`'s own module
  docstring names all three explicitly and restates that none is a licence for the next
  one - each still needs its own argument.
- `backend/tests/test_db_round_trip.py`'s fingerprint already includes `pg_proc`
  (ADR-0023's consequence), so a downgrade/upgrade cycle that silently drops and fails to
  recreate this function is caught for free.
- Downgrading past migration `0014` fails if a reconciler-built numeric-shaped
  generated index still exists (Postgres tracks a dependency from the index to the
  function) - documented on the migration itself and in `docs/operations/upgrade.md`,
  since these indexes are reconciler-managed rather than migration-managed and so cannot
  be dropped by the migration's own `downgrade()` the way `0012` drops its own indexes
  before dropping `nptc_search_text`.
- FR-13 is fully implemented from issue #54 on: all three of ADR-0012's index shapes are
  real, generated, and exercised by `test_db_property_index_plan.py`'s `EXPLAIN` proof -
  none is deferred to a later issue as untested scaffolding.
