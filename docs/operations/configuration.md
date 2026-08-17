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
| `NPTC_TRUSTED_ISSUERS` | `nptc.settings.AuthSettings` (backend) | *(empty - no issuer trusted)* | No | Comma-separated list of OIDC issuer URLs allowed to auto-link (NFR-05). Leave empty while federation is off (NFR-02) |
| `NPTC_OIDC_ISSUER` | `nptc.settings.AuthSettings` (backend, NFR-07) | *(empty - no verifier can be constructed)* | No | The realm's issuer URL, e.g. `http://localhost:8080/realms/nptc`. Empty is fail-closed: `TokenVerifier.from_settings` refuses to construct rather than accept a token whose issuer was never checked |
| `NPTC_OIDC_AUDIENCE` | `nptc.settings.AuthSettings` (backend, NFR-07) | `nptc-api` | No | Fixed by the committed realm's `nptc-api-audience` mapper (ADR-0014) - only change this alongside the realm |
| `NPTC_JWKS_URL` | `nptc.settings.AuthSettings` (backend, NFR-07) | *(empty - resolved via OIDC discovery)* | No | Set only to skip discovery (air-gapped deployments) - normally left empty |
| `NPTC_JWKS_CACHE_SECONDS` | `nptc.settings.AuthSettings` (backend, NFR-07) | `300` | No | How long `nptc.auth.jwks.SigningKeys` trusts a fetched JWKS before re-checking |
| `NPTC_JWKS_REFRESH_COOLDOWN_SECONDS` | `nptc.settings.AuthSettings` (backend, NFR-07) | `30` | No | An unrecognised `kid` within this many seconds of the last refresh attempt is refused with no HTTP request, so a spray of unknown `kid`s cannot hammer the IdP |
| `KEYCLOAK_ADMIN_USER` | `deploy/compose.yml`'s `keycloak` service | `admin` | No | `admin` is fine |
| `KEYCLOAK_ADMIN_PASSWORD` | `deploy/compose.yml`'s `keycloak` service | `change-me` | Yes | Any local-only value |
| `KEYCLOAK_PORT` | `deploy/compose.yml`'s `keycloak` service (host port mapping) | `8080` | No | Change only if `8080` is already in use locally |
| `NPTC_FRONTEND_BASE_URL` | `deploy/compose.yml`'s `keycloak` service → realm import (`deploy/keycloak/realm/nptc-realm.json`'s `${NPTC_FRONTEND_BASE_URL}` placeholder) | `http://localhost:5173` | No | The default is fine for the Vite dev server; set it to the frontend's real origin in any other deployment |
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

`NPTC_TRUSTED_ISSUERS` gates `nptc.auth.linking.may_auto_link` (issue #42, NFR-05): an
OIDC identity may only be auto-linked to an existing account (matched by verified email)
if its issuer appears, exact-match, in this comma-separated set. The empty default is a
deliberate fail-closed posture, matching this settings module's existing convention of
raising loudly rather than silently defaulting - here that means "no auto-linking at
all" rather than "trust everything" until an operator explicitly names a trusted issuer.
It stays empty while federation is off (NFR-02, no second IdP configured yet).

`NPTC_OIDC_ISSUER` and the other `NPTC_JWKS_*`/`NPTC_OIDC_AUDIENCE` variables configure
`nptc.auth.tokens.TokenVerifier` (issue #43, NFR-07 - see
[the token-verification architecture doc](../architecture/token-verification.md)).
`NPTC_OIDC_ISSUER` follows the same fail-closed posture as `NPTC_TRUSTED_ISSUERS`: empty by
default, and a `TokenVerifier` cannot be constructed from a blank issuer at all, so an
unconfigured deployment refuses every token rather than accepting one whose issuer was never
actually checked. `NPTC_OIDC_AUDIENCE` defaults to `nptc-api` because that value is fixed by the
committed realm's `nptc-api-audience` mapper (ADR-0014), not by a deployment - only change it
alongside a realm change. `NPTC_JWKS_URL` is normally left empty so the JWKS endpoint is
resolved via OIDC discovery against `NPTC_OIDC_ISSUER`; set it explicitly only for an
air-gapped deployment that cannot reach a discovery endpoint. `NPTC_JWKS_CACHE_SECONDS` and
`NPTC_JWKS_REFRESH_COOLDOWN_SECONDS` tune `nptc.auth.jwks.SigningKeys`'s own key cache and its
refresh cooldown against `kid`-spraying; the defaults are untuned constants, not a measurement
against a specific deployment.

## Keycloak realm import

`deploy/keycloak/realm/nptc-realm.json` is the only place the `nptc` realm is defined
(issue #40, [ADR-0014](../adr/0014-keycloak-realm-as-code.md), NFR-03) — there are no
console steps in any runbook, and none should be added. The `keycloak` service in
`deploy/compose.yml` runs `start-dev --import-realm` with `deploy/keycloak/realm/` bind-mounted
read-only at `/opt/keycloak/data/import`; Keycloak imports every realm file found there once,
on startup.

**Import is skipped once the realm already exists.** Keycloak's `--import-realm` only
creates a realm it doesn't already have — editing `nptc-realm.json` and running
`docker compose -f deploy/compose.yml restart keycloak` does **nothing**, since the running
instance already has an `nptc` realm from the previous start. This is the single most likely
point of operator confusion: to pick up an edited realm file, recreate the container instead:

```powershell
docker compose -f deploy/compose.yml up -d --force-recreate keycloak
```

(`docker compose down keycloak` followed by `up -d keycloak` is equivalent, but `down`'s
per-service form needs Compose v2.24+ — on an older v2 it tears down the whole stack. The
single `--force-recreate` command above works on any Compose v2 and says what it means.)

**`${NPTC_FRONTEND_BASE_URL}` is the file's only placeholder.** Keycloak resolves `${VAR}` in
an imported realm file from the container's environment; `deploy/compose.yml` passes the
`NPTC_FRONTEND_BASE_URL` environment variable through for exactly this. It drives the
`nptc-frontend` client's `rootUrl`, `redirectUris`, `webOrigins` and post-logout redirect URI
— the one part of this realm that is genuinely per-deployment. Everything else in the file is
static, which is what makes "identical realm on every clean clone" testable at all
(`backend/tests/test_keycloak_realm.py`).

**What is deliberately absent:** no users (registration is open —
`registrationAllowed: true`, NFR-02 — so there is nothing to seed), no client secrets (both
`nptc-frontend` and `nptc-api` are `publicClient: true`), and no application roles (Keycloak
authenticates; the platform authorises from the internal user record per NFR-07 — see
ADR-0014's first decision). A maintainer who re-exports the realm from a running instance
instead of hand-editing the file risks reintroducing all three — `test_keycloak_realm.py`'s
offline group exists specifically to catch that.

Open registration is paired with `verifyEmail: false`, which means anyone reachable on the
network can self-register with an address nobody confirmed — there is no SMTP anywhere in
this stack to send a verification email in the first place. This is a deliberate, temporary
posture (ADR-0014), not an oversight: harmless only because no authorisation decision in the
platform yet reads from the internal user record this realm feeds. Wiring SMTP and flipping
`verifyEmail: true` is the one change any real (non-local) deployment of this realm must make.

**To change the realm:** edit `deploy/keycloak/realm/nptc-realm.json` directly and recreate
the `keycloak` container as above — never the admin console (NFR-03). Run
`uv run pytest backend/tests/test_keycloak_realm.py` afterwards; its offline group catches a
malformed file immediately, without needing Docker.

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
