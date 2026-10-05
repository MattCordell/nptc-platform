# Load the real baseline catalogue

This guide takes the published SPIA Requesting workbook from RCPA-QAP to a seeded catalogue
in a running platform (FR-70, FR-76). You run it once per deployment, before anyone edits.
Each step says what to run, what a good result looks like, and what to do if it fails.

Use this guide for the real workbook. To see the screens with sample content, use the dev
seed in [`deployment.md`](../deployment.md#load-sample-data-for-evaluation) instead. The dev
seed drops entries the loader refuses. A real baseline is never filtered: every refusal is
a data problem that RCPA-QAP must settle first.

This guide ties together two tools. [`transform.md`](transform.md) covers the transform in
full, and [`seed-baseline.md`](seed-baseline.md) covers the loader in full. Go there for flag
tables, the finding catalogue and the exit codes.

## Before you start

You need all of these:

1. **A running stack whose `migrate` service has exited `0`.** See
   [`deployment.md`](../deployment.md).
2. **An empty catalogue.** The loader refuses anything else (exit `4`). If the stack already
   holds entries, read "Reseeding" in [`seed-baseline.md`](seed-baseline.md#reseeding) before
   you do anything.
3. **`uv` on your machine.** The transform runs on the host. The backend image does not
   contain it.
4. **The published workbook, unmodified.** Keep your own copy. The loader records its file
   name and SHA-256 in the seed record, so a changed file is visible later.
5. **A release name in `YYYY-MM` form,** such as `2026-06`, for the workbook's publication
   month. The seed record keeps it, so choose it before you load.
6. **Network access to the terminology server,** if you run the terminology check in step 1.
   The settings are the `NPTC_TX_*` variables in [`configuration.md`](../configuration.md).

## Step 1: Check the workbook (report only)

A report-only run changes nothing. Add the terminology check, which validates every code
against SNOMED CT-AU and International (FR-52):

```powershell
uv run nptc-transform run --workbook path\to\SPIA-Requesting.xlsx --check-terminology
```

The command writes `report.json` and `report.md` into `transform-report/`. Open `report.md`.
Blocking findings come first.

| Exit code | What it means | What to do |
|---|---|---|
| `0` | No finding blocks the import. | Go to step 3. |
| `1` | At least one finding blocks the import. | Go to step 2. |
| `2` | The command was wrong, or the workbook is unreadable. | Read the message, fix the command or the file, and run again. |
| `3` | The terminology server could not be reached. No report is written. | Check the network and the `NPTC_TX_*` settings, then run again. |

## Step 2: Settle blocking findings at source

The transform never repairs a blocking finding. RCPA-QAP corrects the workbook, and you run
step 1 again on the corrected file. The "Required action" column of the finding table in
[`transform.md`](transform.md#the-report-files-fr-72) says what to ask for, for each finding code.

Repeat steps 1 and 2 until the exit code is `0`.

## Step 3: Settle specimens that have no code

The transform reports a specimen value it cannot match as `SPECIMEN_VALUE_UNMAPPED`. That is
informational, so the transform exits `0`. The loader, however, refuses any entry whose
specimen has no SNOMED CT code (exit `3`).

Look for this finding in `report.md` now, so you do not meet it at step 5. Settle each value
in one of two ways:

- **A terminologist adds the value to the specimen table** (`SPECIMEN_TABLE` in
  `transform/src/nptc_transform/specimen_table.py`, FR-88) with a verified SNOMED CT code.
  This is a code change, so it goes through a pull request.
- **RCPA-QAP corrects the workbook value** to one the table already matches.

Do not guess a code to get past the refusal.

## Step 4: Emit the import dataset

Name the release and ask for the dataset. Keep the terminology check on, so the same
blocking rules apply:

```powershell
uv run nptc-transform run --workbook path\to\SPIA-Requesting.xlsx --check-terminology --emit-dataset --release-name 2026-06
```

A good run exits `0` and writes `transform-report/import-dataset.json` next to the report.
If a finding blocks the import, the run exits `1` and writes no dataset. Return to step 2.

## Step 5: Dry run the load

Copy the dataset into the backend container, then run the loader with `--dry-run`. A dry run
does the whole import and rolls it back, so it finds every refusal and keeps nothing:

```powershell
docker compose -f deploy/compose.yml cp transform-report/import-dataset.json backend:/tmp/import-dataset.json
docker compose -f deploy/compose.yml exec backend python scripts/seed_baseline.py --dataset /tmp/import-dataset.json --dry-run
```

A good result starts with `DRY RUN (rolled back): would have seeded <n> entries`. Check that
`<n>` matches the number of data rows RCPA-QAP expects.

If the loader refuses, nothing was written. Find the message in
[`seed-baseline.md`](seed-baseline.md#refusals-and-how-to-fix-them), settle it at source,
and repeat from step 1. Two refusals deserve a warning:

- **An FR-05 collision (exit `5`).** The transform does not look for these, so the first
  sign is here. Two entries share a term, for example one entry's preferred term is another's
  synonym. RCPA-QAP must decide which term changes (PRD 6.3). The message names both entries.
- **Exit `3` for a specimen.** You skipped step 3. Go back to it.

A dry run still advances the business-key sequence. This leaves a few skipped key numbers and
does no harm (FR-03 allows gaps), so repeat the dry run as often as you need.

## Step 6: Load

Run the same command without `--dry-run`:

```powershell
docker compose -f deploy/compose.yml exec backend python scripts/seed_baseline.py --dataset /tmp/import-dataset.json
```

The loader writes everything in one transaction, so the load either completes or leaves the
database as it was. A good run prints `SEEDED <n> entries as baseline release '<name>'`.

## Step 7: Check the result

1. **Count.** The `SEEDED` line reports the same `<n>` as the dry run.
2. **Audit chain.** Confirm the chain is intact across the seeded writes. See
   [`verify-audit-chain.md`](verify-audit-chain.md).
3. **Public catalogue.** Open <http://localhost:8081/api/v1/catalogue/entries>. It lists the
   entries without a sign-in.
4. **Admin catalogue.** Sign in as an administrator and open
   <http://localhost:8081/admin/catalogue>. Spot-check a few entries against the workbook.
   The database keeps each entry's workbook sheet and row, and the verbatim `Version` and
   `History` cells (`entry_seed_provenance`), so you can trace an entry back to its row.

## Step 8: Keep the evidence

Keep these together with your handover records (NFR-36):

- the workbook you loaded, and its SHA-256;
- the whole `transform-report/` folder, including `report.md` and `import-dataset.json`;
- the output of the `SEEDED` run, which names the release and the source file.

## If something goes wrong

| Situation | What happens | What to do |
|---|---|---|
| The dry run or the load refuses (exit `3` or `5`) | Nothing was written. | Settle the cause at source and repeat from step 1. |
| The load cannot complete (exit `6`) | Nothing was written. The message names only the exception type. | Check that the database is reachable and the credentials are right, then run again. |
| You run the load a second time (exit `4`) | The loader refuses and writes nothing. | This is deliberate. See "Reseeding" in [`seed-baseline.md`](seed-baseline.md#reseeding). |
| You loaded the wrong data into a stack nobody else uses | The entries are in the database. | Delete the database and start again: `docker compose -f deploy/compose.yml down -v`. This destroys all data. |
| You loaded the wrong data into a stack that holds real work | The entries are in the database. | Do not reseed. Restore from a backup taken before the load. |

## What this load does not do

- It does not create a `Release`. The baseline is recorded as a seed record until the release
  feature lands (P4), so FR-76 stays `in-progress` (ADR-0042).
- It makes no terminology calls. The `edition_hint` stays `"unknown"` until the transform's
  terminology-served enrichment lands (ADR-0010).
