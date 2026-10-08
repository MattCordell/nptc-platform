# Seed baseline CLI

`scripts/seed_baseline.py` loads the transform's `import-dataset.json` into an empty
catalogue as the seeded baseline (FR-70, FR-76). Run it once, on a new deployment, after
migrations and before real users edit anything. It is a thin wrapper around
`nptc.catalogue.seed_import.seed_baseline`; no seeding logic lives in the script.

The decision to record the baseline as a seed record, and not as a `Release`, is
[ADR-0042](../../adr/0042-seeded-baseline-representation.md). The two tables it writes are in
[`data-model.md`](../../architecture/data-model.md#seeded-baseline-provenance-issue-329-fr-76-adr-0010-adr-0042).

To load the real workbook, follow the end-to-end guide in [`load-baseline.md`](load-baseline.md).
This page is the reference for the loader itself.

To seed a development stack from the 50-row sample workbook, use `scripts/dev-seed.ps1`
instead. It filters out the entries this loader refuses and then calls it. The transform reports
a collision itself (`DESIGNATION_COLLISION`), so the script first drops the colliding rows from
a temporary copy of the workbook. See
[`deployment.md`](../deployment.md#load-sample-data-for-evaluation). Never filter a real
baseline: the refusals below are the point.

## Before you run it

1. The stack is up and `migrate` has exited `0`. See [`deployment.md`](../deployment.md).
2. The transform has emitted a dataset with no blocking finding:

   ```powershell
   uv run nptc-transform run --workbook path/to/SPIA-Requesting.xlsx --check-terminology --emit-dataset --release-name 2026-06
   ```

   `--check-terminology` is required. It resolves each specimen's SNOMED CT-AU preferred term,
   which the loader stores as the specimen's display. See [`transform.md`](transform.md) for the
   flags and the dataset format.
3. The catalogue is empty. The loader refuses anything else (exit `4`).
4. Optional, and recommended: `NPTC_INDEXER_DATABASE_URL` is set in the process that runs the
   script. The compose `backend` service already has it. From a checkout, export it or pass
   `--indexer-database-url`. Without it the run still seeds, and the indexes wait for
   `scripts/reconcile_property_indexes.py` (see "Generated indexes").

## Usage

In the compose stack, copy the dataset into the backend container and run the script there.
The container already holds the application role's connection string:

```powershell
docker compose -f deploy/compose.yml cp transform-report/import-dataset.json backend:/tmp/import-dataset.json
docker compose -f deploy/compose.yml exec backend python scripts/seed_baseline.py --dataset /tmp/import-dataset.json --dry-run
docker compose -f deploy/compose.yml exec backend python scripts/seed_baseline.py --dataset /tmp/import-dataset.json
```

From a checkout, with `NPTC_DATABASE_URL` set to the application role's DSN and
`NPTC_INDEXER_DATABASE_URL` set to the indexer role's:

```powershell
uv run python scripts/seed_baseline.py --dataset transform-report/import-dataset.json
```

| Flag | Default | Meaning |
|---|---|---|
| `--dataset` | *(required)* | Path to `import-dataset.json`. |
| `--database-url` | *(none)* | DSN to connect with. Falls back to `NPTC_DATABASE_URL`. Use the application role (`nptc_app_login`), not the owner: the loader writes through the same code paths as the API. See [`upgrade.md`](../upgrade.md) for the DSNs. |
| `--indexer-database-url` | *(none)* | DSN of the role that builds the generated indexes. Falls back to `NPTC_INDEXER_DATABASE_URL`. An explicitly empty value is a usage error (exit `2`). With neither set, no index is built. See "Generated indexes". |
| `--dry-run` | off | Runs the whole import, then rolls it back. Use it first: it finds every refusal below without leaving a trace. It never builds an index, because the rolled-back property definitions no longer exist. |

Run `--dry-run` first. A dry run still takes the audit append lock and advances the
business-key sequence, which a rollback cannot undo. That costs a few skipped key numbers
and nothing else (FR-03 allows gaps), so it is safe to repeat.

## What a run writes

Everything happens in one transaction. Either all of it commits, or none of it does.

- One `seed_import` row: the release name and note, the source filename and SHA-256, the
  dataset `schema_version` and the entry count.
- Per entry: the `catalogue_entry` (status from the dataset, normally `active`), its synonyms,
  its one code binding, its discipline, subgroup, specimen and usage-guidance values, and one
  `entry_seed_provenance` row holding the workbook sheet and row and the verbatim `Version`
  and `History` cells.
- The four system property definitions (`discipline`, `subgroup`, `specimen`,
  `usage_guidance`), if they do not exist yet.
- One provisional `subgroup` local code per distinct subgroup label that no existing code
  names (FR-92). Labels match a code's display or code, ignoring case, so a subgroup an
  administrator added first is reused. RCPA-QAP settles the real vocabulary later.
  Labels that differ only in case share one code, and a value named twice in one cell, or in
  two spellings of one code, is stored once.
- One audit event per write, attributed to the system (NFR-08), then a single
  `advance_sequence_past` call, so the next entry the application creates gets a key above the
  highest seeded one.

After the commit, and outside that transaction, the run builds one generated index
(`ix_propval_p<n>_1`) for each of the filterable system properties `discipline`, `subgroup` and
`specimen` (FR-13). See "Generated indexes".

The loader makes no terminology calls. The transform's `--check-terminology` step is the
place to validate codes before you emit the dataset.

## Generated indexes

A filterable property needs an index for fast facets (FR-13). The seed creates the three
filterable system properties, so after the commit the CLI runs the index reconciler once, as the
indexer role (`NPTC_INDEXER_DATABASE_URL`). It prints one `created index: <name>` line per index.
It runs after the commit because `CREATE INDEX CONCURRENTLY` cannot run inside a transaction, and
because the reconciler reads the committed property definitions.

The index step never changes the exit code. The baseline is already committed, and running the
loader again would only exit `4`. Every shortfall is a `WARNING` on standard error that ends with
the fix: run `scripts/reconcile_property_indexes.py`. It is safe to repeat. It reads the committed
definitions and builds whatever is missing.

| Warning | Cause |
|---|---|
| `no property index was built: NPTC_INDEXER_DATABASE_URL is not set` | Neither the variable nor `--indexer-database-url` was given. |
| `property indexes were not built (<ExceptionType>)` | The indexer database was unreachable or refused the credential. The message names only the exception type (NFR-26). |
| `index <name> was not built (<ExceptionType>)` | One index failed, for example for lack of privilege. The others still converge. |
| `property indexes were not built: another reconciliation is in progress` | Another process holds the reconciler's lock. The CLI does not wait. |
| `no index for property '<key>': its datatype has no handler in this build` | A filterable property uses a datatype this build cannot index. |

Facets still work without the indexes, but slowly.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Seeded and committed. With `--dry-run`: the dataset is seedable, and nothing was committed. |
| `2` | Usage error: no DSN from `--database-url` or `NPTC_DATABASE_URL`, or an explicitly empty `--database-url ""`. |
| `3` | The dataset was refused before any write: the file is missing or not JSON, its `schema_version` is not `3`, it does not match the format, or the backend cannot store it. Every problem is listed. See "Refusals and how to fix them". |
| `4` | The catalogue already holds an entry, or a seeding run is already recorded. Nothing was written. See "Reseeding". |
| `5` | A write was refused and everything was rolled back: an FR-05 designation collision, a discipline label with no matching code, or a missing local code system. The message names the entry by business key, preferred term, sheet and row. |
| `6` | Could not complete: the database was unreachable, a credential was refused, or an unexpected failure occurred. The message names only the exception type, never its text, because that can carry connection details (NFR-26). The transaction is rolled back. |

These codes are stable and safe to depend on from a setup script. The index step adds none.

## Refusals and how to fix them

Each fix is a data change followed by a new transform run. The loader never repairs a dataset.

| Message | Cause | Fix |
|---|---|---|
| `specimen '...' has no SNOMED CT code` | A specimen in the dataset has no code. The transform blocks this now (`SPECIMEN_VALUE_UNMAPPED`), so the file came from an older transform or was edited by hand. | Emit the dataset again with the current transform. If it blocks, a terminologist adds the string to the specimen map, or RCPA-QAP corrects the workbook value. |
| `a specimen means any specimen (123038009) but the entry also lists named specimens` | The cell has `Any` beside a named specimen. FR-89 says the any-specimen value stands alone. The transform reports it as `SPECIMEN_ROOT_WITH_OTHERS`. | Keep `Any` alone in the workbook cell, or remove it and list the named specimens. Then run the transform again. |
| `code binding ... has no FSN` | The transform found no FSN cell for the row. | Fill the FSN in the workbook, or check the column mapping. |
| `schema_version is ...` | The dataset came from a different transform version. | Emit it again with the matching transform. |
| `discipline '...' is not a code in the 'discipline' local code system` | The workbook names a discipline the governed code system lacks. | Correct the workbook, or have an administrator add the code (FR-90). |
| `... matches only a deprecated code` | A discipline or subgroup label matches a code that has been deprecated. | Correct the workbook, or have an administrator add an active code. |
| `... matches N active codes` | A label matches more than one active code, by display or code, ignoring case. | An administrator removes the ambiguity in the code system. |
| `... DesignationCollisionError: ... collision(s) against NPTC-nnnnnn (FR-05)` | A term collides with another entry's designation at error severity. The baseline cannot exist until RCPA-QAP resolves it editorially (PRD 6.3). | Resolve it in the workbook, then run the transform again. The message names the entry that failed, and the entry it collided with. The transform reports the same pair as `DESIGNATION_COLLISION` and blocks `--emit-dataset`, so you meet this refusal only if you skipped the transform or built the dataset another way. |
| `the catalogue already holds entries` | Not a data problem. See "Reseeding". | |

## Reseeding

Seeding twice is refused on purpose. Business keys are positional, so a second run could give
one key to a different clinical concept without any error.

`audit_event` is append-only (NFR-09, NFR-10), so there is no supported way to clear a seeded
catalogue in a database that real users have changed. To reseed:

- **A new or demonstration stack:** delete the database, then start the stack again so
  `migrate` rebuilds the schema.

  ```powershell
  docker compose -f deploy/compose.yml down -v
  docker compose -f deploy/compose.yml up -d --build
  ```

  This destroys all catalogue data. Create the first administrator again afterwards.
- **A database that holds real work:** do not reseed. Restore from a backup taken before the
  seed, then re-run the steps above on that copy.

## After a run

1. Confirm the count against the dataset: the report line reads `SEEDED <n> entries`.
2. Confirm the indexes: three `created index:` lines, and no `WARNING` about indexes. If there is
   one, follow "Generated indexes".
3. Verify the audit hash chain across the seeded writes. See
   [`verify-audit-chain.md`](verify-audit-chain.md).
4. Open the catalogue at <http://localhost:8081/admin/catalogue> and check a few entries.

FR-76 stays `in-progress` until P4 turns the seed record into a `Release`; this run does not
close it.
