# Configuration

Every environment variable the stack reads, kept in step with
[`deploy/.env.example`](../../deploy/.env.example) as later issues add services that read
more of them (per `CONTRIBUTING.md`'s documentation-impact table).

Copy `deploy/.env.example` to `deploy/.env` and fill in real values before running
`docker compose -f deploy/compose.yml up`. `deploy/.env` is gitignored — never commit real
values (NFR-26).

| Variable | Read by | Default (`.env.example`) | Secret | Local dev value |
|---|---|---|---|---|
| `POSTGRES_USER` | `deploy/compose.yml`'s `postgres` service | `nptc` | No | `nptc` is fine |
| `POSTGRES_PASSWORD` | `deploy/compose.yml`'s `postgres` service | `change-me` | Yes | Any local-only value |
| `POSTGRES_DB` | `deploy/compose.yml`'s `postgres` service | `nptc` | No | `nptc` is fine |
| `POSTGRES_PORT` | `deploy/compose.yml`'s `postgres` service (host port mapping) | `5432` | No | Change only if `5432` is already in use locally |
| `NPTC_DATABASE_URL` | `nptc.settings.DatabaseSettings` (backend) | *(no default - required)* | Yes | A DSN for the app runtime role (`nptc_app` membership) |
| `NPTC_MIGRATION_DATABASE_URL` | `nptc.settings.MigrationSettings` (backend), Alembic (`backend/migrations/env.py`) | *(no default - required)* | Yes | A DSN for the owning role - typically `POSTGRES_USER` in this local stack |
| `KEYCLOAK_ADMIN_USER` | `deploy/compose.yml`'s `keycloak` service | `admin` | No | `admin` is fine |
| `KEYCLOAK_ADMIN_PASSWORD` | `deploy/compose.yml`'s `keycloak` service | `change-me` | Yes | Any local-only value |
| `KEYCLOAK_PORT` | `deploy/compose.yml`'s `keycloak` service (host port mapping) | `8080` | No | Change only if `8080` is already in use locally |
| `NPTC_TX_BASE_URL` | `nptc_shared.terminology` (backend and transform) | `https://tx.ontoserver.csiro.au/fhir` | No | The default is fine; point it at a local Ontoserver to work offline |
| `NPTC_TX_TOKEN` | `nptc_shared.terminology` (backend and transform) | *(empty — anonymous)* | Yes | Leave empty — `tx.ontoserver.csiro.au` accepts anonymous requests |
| `NPTC_TX_TIMEOUT_SECONDS` | `nptc_shared.terminology` (backend and transform) | `30` | No | `30` is fine |
| `NPTC_TX_MAX_RETRIES` | `nptc_shared.terminology` (backend and transform) | `3` | No | `3` is fine |
| `NPTC_TX_CHUNK_SIZE` | `nptc_shared.terminology` batch sweep (FR-52) | `300` | No | `300` is fine; see the tuning note below |
| `NPTC_TX_MAX_CONCURRENCY` | `nptc_shared.terminology` batch sweep (FR-52) | `4` | No | `4` is fine; raise only with the server operator's knowledge |

The `POSTGRES_*` and `KEYCLOAK_*` variables above are read only by `deploy/compose.yml`.
`NPTC_DATABASE_URL` and `NPTC_MIGRATION_DATABASE_URL` are read by `nptc.settings`
(issue #33's first `pydantic-settings` consumer, ADR-0003) — two separate DSNs for two
separate roles, read by two separate settings classes (`DatabaseSettings`,
`MigrationSettings`), never one shared connection string or one combined settings object:
an operator running a migration should never need `NPTC_DATABASE_URL` set too.
`NPTC_MIGRATION_DATABASE_URL` is also what `backend/migrations/env.py` resolves the
migration connection from when nothing hands it a live connection directly (see
[`upgrade.md`](upgrade.md)). Both are required with no default: a missing, empty, or
whitespace-only value raises naming the variable, never silently falling back to a
placeholder a misconfigured deployment could run against for a while (NFR-26).
The `NPTC_TX_*` variables are the first read by Python code —
`nptc_shared.terminology.TerminologyConfig.from_env()` (FR-53), used identically by the
backend and the transform (see [ADR-0003](../adr/0003-terminology-client-in-shared.md)
and [the terminology client architecture doc](../architecture/terminology-client.md)). An
empty or unset `NPTC_TX_TOKEN` means anonymous access and sends no `Authorization`
header; setting it sends a static bearer token, the only auth scheme supported today —
OAuth2 client-credentials is deferred. Retry backoff timings are `TerminologyConfig`
constructor defaults, deliberately not environment variables: they are tuning constants,
not deployment configuration. This table grows as later issues add services that read
their own configuration.

## Tuning the batch sweep (`NPTC_TX_CHUNK_SIZE`, `NPTC_TX_MAX_CONCURRENCY`)

These two *are* environment variables rather than constructor defaults, because FR-52
requires the chunk size to be tuned against the specific terminology server in use and the
concurrency ceiling to be configurable.

- `NPTC_TX_CHUNK_SIZE` is how many codes go into one `ValueSet/$expand` in the bulk status
  pass. A sweep of N codes issues `ceil(N / NPTC_TX_CHUNK_SIZE)` expansions per edition.
  FR-52's stated range is 200–500; the default is its midpoint.
- `NPTC_TX_MAX_CONCURRENCY` bounds the *second* pass only — the individual
  `CodeSystem/$lookup` calls for codes the bulk expansion did not resolve. The chunk
  expansions themselves are sequential (ADR-0005).

Both are validated on load: a value below 1 raises rather than falling back to the default,
because a zero-sized chunk would let a sweep report a catalogue it never checked as clean.

**The defaults are untuned** — a judgement inside FR-52's range, not a measurement.
[ADR-0005](../adr/0005-sweep-chunk-size-and-concurrency-defaults.md) records why, and the
procedure for tuning them against a real instance the first time a seeding transform is run
against one.
