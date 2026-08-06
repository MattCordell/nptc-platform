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
| `KEYCLOAK_ADMIN_USER` | `deploy/compose.yml`'s `keycloak` service | `admin` | No | `admin` is fine |
| `KEYCLOAK_ADMIN_PASSWORD` | `deploy/compose.yml`'s `keycloak` service | `change-me` | Yes | Any local-only value |
| `KEYCLOAK_PORT` | `deploy/compose.yml`'s `keycloak` service (host port mapping) | `8080` | No | Change only if `8080` is already in use locally |
| `NPTC_TX_BASE_URL` | `nptc_shared.terminology` (backend and transform) | `https://tx.ontoserver.csiro.au/fhir` | No | The default is fine; point it at a local Ontoserver to work offline |
| `NPTC_TX_TOKEN` | `nptc_shared.terminology` (backend and transform) | *(empty — anonymous)* | Yes | Leave empty — `tx.ontoserver.csiro.au` accepts anonymous requests |
| `NPTC_TX_TIMEOUT_SECONDS` | `nptc_shared.terminology` (backend and transform) | `30` | No | `30` is fine |
| `NPTC_TX_MAX_RETRIES` | `nptc_shared.terminology` (backend and transform) | `3` | No | `3` is fine |

The `POSTGRES_*` and `KEYCLOAK_*` variables above are read only by `deploy/compose.yml`.
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
