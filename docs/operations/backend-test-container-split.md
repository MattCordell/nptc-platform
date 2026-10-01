# Which backend tests need a database

This note records why 1,143 backend tests carry the `integration` marker, and how many of
them could run without a container. Read it before you try to shrink the container-bound
set, so you do not repeat the work. The investigation is issue #363.

## Short answer

Few tests are worth moving, and moving them saves little time.

- 1,143 of 1,980 collected backend items are marked `integration` (58%).
- 72 of the 1,143 are database-independent in substance. Only 3 of them need no code change
  beyond dropping the marker, and dropping it would make the fast subset slower on Windows
  (see [Timings](#timings)).
- A further 115 HTTP tests are database-independent in substance. They need a no-database
  app builder that does not exist yet, and 69 of them also need a stubbed principal.
- The other 956 items verify SQL behaviour, or read back rows written through the real
  write path. They stay.

The recommendation is to move nothing now. Moving 185 of the 187 candidates would save an
estimated 74 seconds of a 978-second serial run. The other two are CLI tests that save nothing
until their connect wait is fixed. Three tests that wait for a refused connection cost 391
seconds on the measured machine, so fixing them is the larger lever. The
[Timings](#timings) section has the evidence.

## How the counts were taken

| | |
|---|---|
| Date | 2026-10-01 |
| Commit | `08a3573` (`main`) |
| Collected | 1,980 items in `backend/tests` |
| `integration` | 1,143 items in 77 files |
| Not marked | 837 items |

Reproduce the totals and the per-file counts:

```powershell
uv run pytest backend/tests --collect-only -q -m integration
```

Each parametrised case is one item, because `-m` selects collected items. Issue #363's
"908 of 1,429" counted test functions instead.

Since #397 the marker is derived from fixture use, so tests that reach the container
through `app_db` are marked and sit inside the 1,143. Before that change 65 such tests
were unmarked.

## Criteria

A test **needs a database** when either condition holds:

1. Its assertion depends on something only Postgres does: a constraint, privilege, trigger,
   SQL function, extension (`pg_trgm`, `unaccent`), index plan, lock or isolation
   behaviour, sequence or migration.
2. It reads back rows written through the real write path. Every write path takes
   `pg_advisory_xact_lock` through `nptc.audit.writer` as its first step. A service write
   test is therefore not movable unless its assertion fires before that call.

A test is **database-independent in substance** when the outcome is decided before any
statement runs (a guard clause, request validation, token verification, a stub proxy) and
the assertion reads no database state.

When the two readings conflict, the test stays. `NFR-39` and ADR-0011 rule out sqlite or any
other substitute, so the only way to move a test is to prove it never touches the database.

## Categories

| Category | Meaning | Items | Share |
|---|---|---:|---:|
| (a) SQL is the subject | Constraints, grants, `pg_trgm`, `unaccent`, index plans, row versioning, locks, sequences, SQL functions, migrations, audit-chain tamper detection | 342 | 30% |
| (b) Persistence-bound logic | Service or auth logic that reads back stored rows, where no Postgres feature is the subject | 300 | 26% |
| (c) HTTP routes | The real `create_app()` with real identity resolution and real rows (`api_app_support.py`) | 480 | 42% |
| (d) Keycloak | OIDC container tests. They need Keycloak, not Postgres | 8 | 1% |
| (e) Other | CLI and compose checks, and one harness self-check | 13 | 1% |
| Total | | 1,143 | 100% |

Of the 480 HTTP items, 19 are really about a Postgres feature reached through an endpoint:
11 in `test_api_public_search.py` (trigram search, accent folding, score ties, statement
count), 4 in `test_api_catalogue_bindings.py`, 2 in `test_api_catalogue_designations.py`,
and one each in `test_api_registry_properties.py` and `test_api_catalogue_properties.py`
(partial unique indexes and savepoint rollback).

Each category comes from reading every test body and the guard order of the service under
test. The claims that decide movability were then re-checked against the code (see
[Spot checks](#spot-checks)).

## Per-file counts

### HTTP tests (category c)

"No override" counts candidates that only need a stand-in for `get_session`. "Principal
override" counts candidates that also need `current_principal` replaced.

| File (`backend/tests/test_api_...`) | Items | SQL subject | No override | Principal override |
|---|---:|---:|---:|---:|
| `catalogue_designations` | 83 | 2 | 3 | 13 |
| `catalogue_bindings` | 43 | 4 | 1 | 9 |
| `catalogue_admin_listing` | 43 | 0 | 4 | 0 |
| `registry_properties` | 37 | 1 | 6 | 7 |
| `public_search` | 32 | 11 | 0 | 0 |
| `catalogue_properties` | 30 | 1 | 2 | 3 |
| `terminology` | 22 | 0 | 1 | 19 |
| `registry_property_values` | 22 | 0 | 1 | 5 |
| `public_status_filter` | 17 | 0 | 0 | 0 |
| `catalogue_entries` | 17 | 0 | 1 | 2 |
| `public_catalogue` | 16 | 0 | 1 | 0 |
| `code_lookup` | 15 | 0 | 7 | 0 |
| `catalogue_admin_read` | 15 | 0 | 2 | 1 |
| `audit_search` | 14 | 0 | 1 | 5 |
| `audit_export` | 14 | 0 | 1 | 4 |
| `public_response_hygiene` | 11 | 0 | 0 | 1 |
| `error_mapping` | 11 | 0 | 6 | 0 |
| `auth_session` | 11 | 0 | 7 | 0 |
| `public_entry_history` | 9 | 0 | 0 | 0 |
| `public_finding_indicator` | 8 | 0 | 0 | 0 |
| `catalogue_admin_length_report` | 7 | 0 | 1 | 0 |
| `settings_wiring` | 3 | 0 | 1 | 0 |
| Total | 480 | 19 | 46 | 69 |

### All other tests

Files are under `backend/tests/` and named `test_<name>.py`. "Cand." is the number of
database-independent candidates.

| File | Items | (a) | (b) | (d) | (e) | Cand. |
|---|---:|---:|---:|---:|---:|---:|
| `audit_chain` | 8 | 8 | 0 | 0 | 0 | 0 |
| `audit_diff_write_path` | 8 | 2 | 6 | 0 | 0 | 2 |
| `audit_search` | 10 | 0 | 10 | 0 | 0 | 0 |
| `audit_tamper_detection` | 15 | 15 | 0 | 0 | 0 | 0 |
| `auth_account_closure` | 9 | 0 | 9 | 0 | 0 | 0 |
| `auth_authenticate` | 2 | 0 | 2 | 0 | 0 | 1 |
| `auth_identity_audit` | 12 | 0 | 12 | 0 | 0 | 0 |
| `auth_identity_resolution` | 16 | 1 | 15 | 0 | 0 | 0 |
| `catalogue_bindings` | 18 | 2 | 16 | 0 | 0 | 9 |
| `catalogue_business_key` | 8 | 5 | 3 | 0 | 0 | 0 |
| `catalogue_collisions` | 25 | 3 | 22 | 0 | 0 | 4 |
| `catalogue_designations` | 37 | 8 | 29 | 0 | 0 | 8 |
| `catalogue_entries_length_log` | 13 | 0 | 13 | 0 | 0 | 0 |
| `catalogue_entry_length` | 1 | 0 | 1 | 0 | 0 | 0 |
| `catalogue_length_report` | 8 | 4 | 4 | 0 | 0 | 0 |
| `catalogue_local_codes` | 31 | 4 | 27 | 0 | 0 | 9 |
| `catalogue_optimistic_locking` | 23 | 6 | 17 | 0 | 0 | 0 |
| `catalogue_property_value_sources` | 33 | 0 | 33 | 0 | 0 | 19 |
| `catalogue_property_values` | 37 | 0 | 37 | 0 | 0 | 2 |
| `db_audit_privileges` | 5 | 5 | 0 | 0 | 0 | 0 |
| `db_bootstrap` | 6 | 2 | 4 | 0 | 0 | 1 |
| `db_catalogue_entry` | 9 | 9 | 0 | 0 | 0 | 0 |
| `db_code_binding` | 26 | 26 | 0 | 0 | 0 | 0 |
| `db_code_binding_index_plan` | 1 | 1 | 0 | 0 | 0 | 0 |
| `db_collision_acknowledgement` | 9 | 9 | 0 | 0 | 0 | 0 |
| `db_designation` | 24 | 24 | 0 | 0 | 0 | 0 |
| `db_local_code` | 29 | 29 | 0 | 0 | 0 | 0 |
| `db_migrations` | 3 | 2 | 0 | 0 | 1 | 0 |
| `db_numeric_or_null_function` | 15 | 15 | 0 | 0 | 0 | 0 |
| `db_property_definition` | 15 | 15 | 0 | 0 | 0 | 0 |
| `db_property_index_plan` | 4 | 4 | 0 | 0 | 0 | 0 |
| `db_property_indexes` | 15 | 14 | 0 | 0 | 1 | 1 |
| `db_property_value` | 5 | 5 | 0 | 0 | 0 | 0 |
| `db_round_trip` | 5 | 5 | 0 | 0 | 0 | 0 |
| `db_sctid_function` | 22 | 22 | 0 | 0 | 0 | 0 |
| `db_search_index` | 27 | 27 | 0 | 0 | 0 | 0 |
| `db_user_model` | 8 | 8 | 0 | 0 | 0 | 0 |
| `db_user_privileges` | 7 | 7 | 0 | 0 | 0 | 0 |
| `db_user_role_privileges` | 7 | 7 | 0 | 0 | 0 | 0 |
| `deploy_compose` | 1 | 0 | 0 | 0 | 1 | 0 |
| `grant_role_cli` | 3 | 0 | 0 | 0 | 3 | 0 |
| `grants` | 11 | 1 | 10 | 0 | 0 | 2 |
| `keycloak_jwt` | 1 | 0 | 0 | 1 | 0 | 0 |
| `keycloak_pkce_login` | 6 | 0 | 0 | 6 | 0 | 0 |
| `keycloak_realm` | 1 | 0 | 0 | 1 | 0 | 0 |
| `label_provenance` | 1 | 0 | 1 | 0 | 0 | 0 |
| `lock_ordering` | 14 | 14 | 0 | 0 | 0 | 0 |
| `mfa_required` | 3 | 0 | 3 | 0 | 0 | 0 |
| `principal_derivation` | 7 | 0 | 7 | 0 | 0 | 2 |
| `property_registry_no_migration_required` | 1 | 1 | 0 | 0 | 0 | 0 |
| `provision_login` | 6 | 5 | 0 | 0 | 1 | 0 |
| `registry_definitions` | 19 | 0 | 19 | 0 | 0 | 10 |
| `search_ranking` | 17 | 17 | 0 | 0 | 0 | 0 |
| `validation_finding_model` | 10 | 10 | 0 | 0 | 0 | 0 |
| `verify_audit_chain_cli` | 6 | 0 | 0 | 0 | 6 | 2 |
| Total | 663 | 342 | 300 | 8 | 13 | 72 |

Where a file mixes categories, the split is by test using the criteria above. Some file
names mislead:

- `test_lock_ordering.py` holds 14 items, all kept in (a). Eleven check that a rejected
  changelog note issues no lock statement, which looks like a guard clause. The assertion
  reads the statements the connection actually issued, so the test stays (see
  [Candidates that stay](#candidates-that-stay)).
- `test_search_ranking.py` sounds like ranking logic. All 17 marked items drive
  `pg_trgm` and full-text search over HTTP.
- `test_db_bootstrap.py` is mostly service logic (4 of 6), not SQL behaviour.
- `test_catalogue_property_value_sources.py` is (b) throughout, but 19 of 33 items depend
  only on a stub terminology client.
- `test_api_terminology.py` is a stub-terminology proxy. 20 of 22 items never read a catalogue row.
- `test_keycloak_*.py` need Keycloak and no Postgres.

## Candidates that could move

These are the tests that are database-independent in substance. Moving any of them costs
a code change, and the saving is small ([Timings](#timings)).

### Non-HTTP candidates (72)

| Tier | Items | What moving it takes | Weakens coverage? |
|---|---:|---|---|
| A | 3 | Drop `@pytest.mark.integration`. These tests never request a container. Two of them wait 131 s each on Windows, so fix the connect timeout first ([Timings](#timings)). | No |
| B | 42 | Build a transient ORM object and an unbound or failing-on-use `Session`, because the guard raises before any session call. | No |
| C | 5 | Test the model property on a transient model, as `test_catalogue_entry_length.py` already does. | Slightly: the service path is no longer exercised |
| D | 3 | Weak case. The setup needs a flush, or an in-memory attribute check replaces a column check. | Yes |
| E | 19 | Call the session-free helpers in `property_value_sources` directly. Keep one database-backed list test and one resolve test. | Slightly: dispatch and definition loading go untested |

Tier A holds `test_get_indexer_engine_raises_when_not_configured` and the two
`test_verify_audit_chain_cli.py` tests that connect to an unreachable address.

Tier B by file: `registry_definitions` 10, `catalogue_local_codes` 9, `catalogue_bindings` 8,
`catalogue_collisions` 3, `catalogue_designations` 4, `catalogue_property_values` 2,
`grants` 2, `principal_derivation` 2, `audit_diff_write_path` 1, `auth_authenticate` 1.

### HTTP candidates (115)

All 115 are category (c) tests whose outcome is decided before the first query: 401 for a
missing or bad credential, request-body or path validation (422), a route body that only
raises, or a stub terminology proxy.

- **46 need only a no-database stand-in for `get_session`.** They are anonymous 401s,
  public-route validation, token-verification failures, and the test-only error route.
- **69 also need `current_principal` replaced** with an in-memory principal holding one
  permission. The principal is a precondition in those tests, not the subject.

Neither stand-in exists today. `build_api_test_app` takes a real connection, and
`api_app_support.py` keeps identity resolution real on purpose.

### Candidates that stay

- The 11 `test_lock_ordering.py` rejected-note tests. A fake session that fails on any call
  proves a stricter claim than "no lock statement reached the wire". The rule for this note is
  that a speed gain is not worth weakening coverage of locks, constraints, triggers or row
  versioning, so a lock-ordering test stays when in doubt.
- Two borderline HTTP tests that validate first but then read the row back to prove nothing
  was written: `test_patch_entry_with_no_reason_is_422` and
  `test_bulk_save_with_no_reason_is_422_before_touching_any_entry`.
- Every authenticated 403 and MFA step-up test. The principal's database-derived permission
  set is the subject there.

## Spot checks

These claims decide whether a test is movable. Each was checked against the code.

- The two Tier A CLI tests carry a hand-written `@pytest.mark.integration` and request no
  container fixture. They pass a bogus DSN to `verify.main`.
- `current_principal` returns `ANONYMOUS` without a query when no bearer token is present
  (`backend/src/nptc/api/dependencies.py`), so the anonymous 401 tests are decided before
  the session is used.
- `api_app_support.py` overrides `get_session` onto the fixture's connection, so a no-database
  variant needs a new builder.
- The `test_lock_ordering.py` rejected-note tests assert on statements captured from a real
  connection (`_captured_lock_statements`), which is why they stay.

## Timings

One serial run of each command on 2026-10-01, at commit `08a3573`. The machine runs
Windows 11 with Docker and 14 logical cores. Nothing was repeated, so treat differences of a
few seconds as noise.

```powershell
uv run pytest backend/tests -m "not integration" -q --durations=0
uv run pytest backend/tests -q --durations=0
```

| Run | Result | Wall time |
|---|---|---:|
| Fast subset (`-m "not integration"`) | 829 passed, 8 skipped | 194 s |
| Full backend run | 1,972 passed, 8 skipped | 978 s |

### Where the test time goes

The per-test timings sum to 970 s. The other 8 s of the 978 s wall time is not attributed to any
test, for example collection.

| Bucket | Seconds | Share of 970 s |
|---|---:|---:|
| Three tests that wait for a refused connection | 391 | 40% |
| Keycloak test files (8 integration items) | 153 | 16% |
| Everything else | 426 | 44% |

The three tests connect to `127.0.0.1:1` and expect a failure:

- `test_provision_login.py::test_cli_failure_prints_the_error_type_but_not_the_dsn_or_password`
  (not marked `integration`, so it also sits in the fast subset)
- `test_verify_audit_chain_cli.py::test_could_not_connect_exits_3`
- `test_verify_audit_chain_cli.py::test_missing_dbapi_driver_exits_3_not_1`

Each takes about 130 s. `psycopg.connect` to that address took 130.1 s without a
`connect_timeout` and 3.0 s with `connect_timeout=3`. A plain socket connect to the same
address failed in 2.1 s. The same wait reproduced when two of the tests ran alone. This was
measured on Windows only. A Linux runner may refuse the connection at once, which would fit
CI's roughly 8-minute runs against 16 minutes on the measured machine.

`test_missing_dbapi_driver_exits_3_not_1` used a bare `postgresql://` URL and claimed to test a
missing `psycopg2`. SQLAlchemy resolves a bare URL to `psycopg`, which is installed, so the
test reached a real connection attempt and waited. It now names `postgresql+psycopg2://`, which
fails at once with `ModuleNotFoundError` and so tests what its name says.

The other two tests now carry `?connect_timeout=3` in their DSN and take about 3 s each. After
that change, the fast subset of `backend/tests` took about 55 to 66 s on the measured machine,
against 194 s before. The same run on unchanged code took 181 s.

Before the fix, 130 s of the fast subset's 194 s was the one unmarked test. Moving the two Tier A
CLI tests into the fast subset would then have added about 260 s. With the timeout it adds
about 6 s.

### What the container costs

Starting Postgres and applying migrations took 4.8 s, taken from the setup time of the first
test in the run. One Keycloak test's setup took 46.7 s, and two more Keycloak tests took 54 s
and 50 s of call time. Container start is therefore not the main cost of the Postgres tests.
Among the 1,031 integration items that reported a time above 5 ms, the median cost is 0.49 s
per item (call, setup and teardown together).

### What moving the candidates would save

Estimated as candidate count times the mean cost per item in the same file. HTTP tests average
0.60 s per item. In the files that hold most non-HTTP candidates the mean is 0.04 to 0.06 s
per item, except that `test_db_property_indexes.py` averages 0.14 s.

| Candidate set | Items | Estimated saving |
|---|---:|---:|
| Non-HTTP, excluding the two CLI tests | 70 | 4 s |
| HTTP, no override | 46 | 27 s |
| HTTP, with principal override | 69 | 43 s |
| Total | 185 | 74 s (8% of 978 s) |

The HTTP figures are upper bounds, because a no-database app still pays to build the app.
The two CLI tests are not counted. Moving them saves nothing until the connect wait is fixed.

## Recommendation

Move no tests now.

- **Movable set:** 187 items, which is 16% of the 1,143. Of these, 3 need no code change, 42
  need a validator or session change, and 115 need a no-database HTTP builder. The other 27
  are weaker or partial moves (tiers C, D and E).
- **Saving:** about 74 s of 978 s on the measured machine, from 185 candidates. The two CLI
  tests save nothing until their connect wait is fixed. The full run's wall time barely moves.
- **Larger lever (done in #399):** the three refused-connection tests now fail fast. That removes
  about 380 s from the full run and about 130 s from the fast subset on Windows. It changes no
  assertion.

## Follow-up levers (not built)

None is part of issue #363. Sizes are estimates.

| Lever | Effect | Size |
|---|---|---|
| Add `connect_timeout` to the three refused-connection tests | Done in #399. Removes about 380 s from a full Windows run, and makes the Tier A move safe | S |
| Extract pure validators for Tier B | Moves 42 items out of the container set and makes the guards testable alone. Saves about 2 s | M |
| No-database app builder for HTTP candidates | Moves up to 115 items for at most about 70 s. 46 need no principal override | M |
| Postgres template database | Not justified: container start was 4.8 s | M |
| Container reuse between runs | Not justified for the same reason | S |
| `pytest-xdist` | Already in `CLAUDE.md`: `-n auto --dist loadscope` | none |
