# Filterable-property index reconciliation CLI

`scripts/reconcile_property_indexes.py` converges the indexes `property_value` actually
carries against every `property_definition` row's own desired index - the automatic index
generation FR-13 requires (backlog issue
[#54](https://github.com/MattCordell/nptc-platform/issues/54)), so making a property
searchable never depends on someone remembering to write a migration. It is a thin
operator wrapper around `nptc.db.property_reconciler.reconcile_property_indexes`; no new
reconciliation logic lives here.

The three index shapes ([ADR-0012](../../adr/0012-property-registry-storage-and-validation.md),
[ADR-0027](../../adr/0027-cast-safe-numeric-index-expression.md)), the naming scheme
(`ix_propval_p{index_seq}_{slot}`), and the reconciler's own desired-state design are
documented in [`data-model.md`](../../architecture/data-model.md#automatic-index-generation-issue-54-fr-13).
This runbook covers only the operator-facing CLI.

## Usage

```powershell
uv run python scripts/reconcile_property_indexes.py
uv run python scripts/reconcile_property_indexes.py --database-url postgresql+psycopg://nptc_indexer:change-me@localhost:5432/nptc
uv run python scripts/reconcile_property_indexes.py --dry-run
```

| Flag | Default | Meaning |
|---|---|---|
| `--database-url` | *(none)* | DSN to reconcile with. Falls back to `NPTC_INDEXER_DATABASE_URL` if not given. Must be a role that can `CREATE`/`DROP INDEX` on `property_value` - see [`upgrade.md`](../upgrade.md#provisioning-the-index-reconcilers-login-issue-54-fr-13) for provisioning one. |
| `--dry-run` | off | Reports what would change without executing any DDL. |

## When to run it

- After flagging a property `filterable` (or un-flagging one), if
  `NPTC_INDEXER_DATABASE_URL` is not configured in the API process itself - this is the
  "converge now" path for that deployment shape.
- On a schedule, as a safety net: the reconciler repairs an index a failed
  `CREATE INDEX CONCURRENTLY` left `indisvalid = false`, which nothing else notices on its
  own.
- With `--dry-run`, before a maintenance window, to see what a real run would do.

Idempotent and safe to run repeatedly or concurrently - a `pg_try_advisory_lock` guards
the whole run, so two overlapping invocations converging on the same state simply have one
report `SKIPPED` rather than racing or erroring.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Converged - either there was nothing to do, or (a real, non-`--dry-run` run) drift was found and fixed. "Found and fixed" is success here, not a failure code, since fixing it is the whole point of running the command. Also returned when another reconciliation was already in progress (`SKIPPED`). |
| `1` | `--dry-run` only: drift was found and reported, but nothing was executed. Never returned by a real run. |
| `2` | Usage error: no DSN resolvable from `--database-url`/`NPTC_INDEXER_DATABASE_URL`, or an explicitly-empty `--database-url ""`. |
| `3` | Could not complete: the database was unreachable, the credential lacked the privilege to `CREATE`/`DROP INDEX`, the connection dropped mid-run, or any other environment problem - not a finding about drift. The message names only the exception's type, never its full text, since that can carry connection details (NFR-26/NFR-35). |

These codes are stable and safe to depend on from a scheduled check.

## What the output lines mean

```
CREATED: ix_propval_p7_1
DROPPED: ix_propval_p12_1
REBUILT (was invalid): ix_propval_p9_1
REPAIRED COMMENT: ix_propval_p9_1
OK: no drift - every filterable property's index is already converged
```

- `CREATED` - a filterable property had no index; one was built.
- `DROPPED` - an index existed for a property that is no longer filterable (or whose
  `index_shape()` now returns `None`) - AC 3's "un-flagging removes the index" made
  concrete.
- `REBUILT (was invalid)` - a previous `CREATE INDEX CONCURRENTLY` failed partway,
  leaving an index that existed by name but was never usable
  (`pg_index.indisvalid = false`, invisible to `pg_indexes`). The reconciler drops and
  rebuilds it.
- `REPAIRED COMMENT` - the `COMMENT ON INDEX` carrying the property key (the only place
  that key appears - index *names* never contain it, see ADR-0012) was missing or stale.
  Repaired in place; the index itself is not rebuilt.
- `SKIPPED` - another reconciliation was already holding the advisory lock. Nothing was
  attempted; re-run later if needed.
- `--dry-run` prefixes each line with `WOULD` (`WOULD CREATE`, `WOULD DROP`, ...) and
  executes nothing.

## No output beyond the summary line means nothing to do

An idle deployment where every filterable property's index is already converged prints
only the `OK:` line and exits `0`. This is the expected steady state, not a sign the
reconciler isn't working - most invocations, most of the time, should look like this.
