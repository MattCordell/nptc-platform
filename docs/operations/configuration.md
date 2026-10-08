# Configuration

Every environment variable the stack reads, kept in step with
[`deploy/.env.example`](../../deploy/.env.example) as later issues add services that read
more of them (per `CONTRIBUTING.md`'s documentation-impact table).

Copy `deploy/.env.example` to `deploy/.env` and fill in real values before running
`docker compose -f deploy/compose.yml up -d --build` (see [`deployment.md`](deployment.md)).
`deploy/.env` is gitignored — never commit real values (NFR-26).

Compose does not pass `deploy/.env` wholesale to the containers. It sets each service's
variables explicitly, so the `backend` service never receives the owner's
`NPTC_MIGRATION_DATABASE_URL`. Compose also builds the two database URLs itself, with host
`postgres`, from `POSTGRES_*`, `NPTC_APP_DB_PASSWORD` and `NPTC_INDEXER_DB_PASSWORD`. The
`NPTC_DATABASE_URL` and
`NPTC_MIGRATION_DATABASE_URL` lines in `deploy/.env` point at `localhost` and serve only an
API you run on your own machine ([`local-development.md`](local-development.md)).

| Variable | Read by | Default (`.env.example`) | Secret | Local dev value |
|---|---|---|---|---|
| `POSTGRES_USER` | `deploy/compose.yml`'s `postgres` service | `nptc` | No | `nptc` is fine |
| `POSTGRES_PASSWORD` | `deploy/compose.yml`'s `postgres` service | `change-me` | Yes | Any local-only value |
| `POSTGRES_DB` | `deploy/compose.yml`'s `postgres` service | `nptc` | No | `nptc` is fine |
| `POSTGRES_PORT` | `deploy/compose.yml`'s `postgres` service (host port mapping) | `5432` | No | Change only if `5432` is already in use locally |
| `NPTC_APP_DB_PASSWORD` | `deploy/compose.yml`'s `migrate` and `backend` services; `nptc.settings.AppLoginSettings` (`nptc.db.provision_login`) | `change-me` | Yes | The password of the `nptc_app_login` database role. Required by compose, with no fallback. Keep it free of `@ : / ? # %`, because compose places it in a database URL; `migrate` refuses such a value and names the variable. When you run the API on your own machine, `NPTC_DATABASE_URL` must carry the same password |
| `NPTC_INDEXER_DB_PASSWORD` | `deploy/compose.yml`'s `migrate` and `backend` services; `nptc.settings.IndexerLoginSettings` (`nptc.db.provision_login`) | `change-me` | Yes | The password of the `nptc_indexer` database role, which builds the indexes filterable properties need (FR-13). Required by compose, with no fallback. The same character rule as `NPTC_APP_DB_PASSWORD`. `migrate` refuses a bad value and names the variable. When you run the API on your own machine, put the same password in `NPTC_INDEXER_DATABASE_URL` |
| `NPTC_DATABASE_URL` | `nptc.settings.DatabaseSettings` (backend) | *(no default - required)* | Yes | A DSN for the app runtime role (`nptc_app` membership). Compose builds this itself for the `backend` service |
| `NPTC_MIGRATION_DATABASE_URL` | `nptc.settings.MigrationSettings` (backend), Alembic (`backend/migrations/env.py`) | *(no default - required)* | Yes | A DSN for the owning role - typically `POSTGRES_USER` in this local stack. Compose gives it to the `migrate` service only |
| `NPTC_AUDIT_VERIFY_DATABASE_URL` | `nptc.settings.AuditVerifySettings` (backend, issue #38) | *(empty - falls back to `NPTC_DATABASE_URL`)* | Yes, when set | Optional DSN for `scripts/verify_audit_chain.py` - point this at a read-only replica or a restored backup, since `verify_chain` only ever issues `SELECT`s. See [the runbook](runbooks/verify-audit-chain.md) |
| `NPTC_INDEXER_DATABASE_URL` | `nptc.settings.IndexerSettings` (backend, issue #54, FR-13); `scripts/seed_baseline.py` | *(empty - reconciliation disabled)* | Yes, when set | The DSN the API uses to build and drop the indexes filterable properties need, after a registry write commits. It must name a role that owns `property_value` (never `nptc_app`, which cannot do DDL, or the migration owner, which can do far more than this needs). Compose builds it for the `backend` service from `NPTC_INDEXER_DB_PASSWORD`. The seed CLI reads it too, to build the system properties' indexes after it commits. Empty is valid: a registry write then logs a warning and builds no index, the seed CLI prints a warning and builds none, and you run [the runbook](runbooks/reconcile-property-indexes.md)'s command instead. See [the provisioning steps](upgrade.md#provisioning-the-index-reconcilers-login-issues-54-and-274-fr-13) |
| `NPTC_TRUSTED_ISSUERS` | `nptc.settings.AuthSettings` (backend) | *(empty - no issuer trusted)* | No | Comma-separated list of OIDC issuer URLs allowed to auto-link (NFR-05). Leave empty while federation is off (NFR-02) |
| `NPTC_OIDC_ISSUER` | `nptc.settings.AuthSettings` (backend, NFR-07) | *(empty - no verifier can be constructed)* | No | The realm's issuer URL, as your browser reaches Keycloak, e.g. `http://localhost:8080/realms/nptc`. In the compose stack it must keep the browser-facing host, because Keycloak writes the request host into a token's `iss` claim. If you change `KEYCLOAK_PORT`, change this too. Empty is fail-closed: `TokenVerifier.from_settings` refuses to construct rather than accept a token whose issuer was never checked |
| `NPTC_OIDC_AUDIENCE` | `nptc.settings.AuthSettings` (backend, NFR-07) | `nptc-api` | No | Fixed by the committed realm's `nptc-api-audience` mapper (ADR-0014) - only change this alongside the realm |
| `NPTC_JWKS_URL` | `nptc.settings.AuthSettings` (backend, NFR-07) | *(empty - resolved via OIDC discovery)* | No | Set only to skip discovery (air-gapped deployments) - normally left empty. Must be the direct URL: PyJWT rejects redirects. The compose stack fixes it for `backend` at `http://keycloak:8080/realms/nptc/protocol/openid-connect/certs`, the internal address, so the API never needs to reach the browser-facing issuer host |
| `NPTC_JWKS_CACHE_SECONDS` | `nptc.settings.AuthSettings` (backend, NFR-07) | `300` | No | How long `nptc.auth.jwks.SigningKeys` trusts a fetched JWKS before re-checking |
| `NPTC_JWKS_REFRESH_COOLDOWN_SECONDS` | `nptc.settings.AuthSettings` (backend, NFR-07) | `30` | No | An unrecognised `kid` within this many seconds of the last refresh attempt is refused with no HTTP request, so a spray of unknown `kid`s cannot hammer the IdP |
| `NPTC_MFA_ACR_VALUES` | `nptc.settings.AuthSettings` (backend, issue #44, NFR-06) | `2` | No | Comma-separated `acr` claim values that satisfy mandatory-MFA-for-administrators (`nptc.auth.principal.principal_for`). Must match the committed realm's `nptc loa-2 condition` authenticator config (`loa-condition-level`) - see [the permissions architecture doc](../architecture/permissions.md) |
| `KEYCLOAK_ADMIN_USER` | `deploy/compose.yml`'s `keycloak` service | `admin` | No | `admin` is fine |
| `KEYCLOAK_ADMIN_PASSWORD` | `deploy/compose.yml`'s `keycloak` service | `change-me` | Yes | Any local-only value |
| `KEYCLOAK_PORT` | `deploy/compose.yml`'s `keycloak` service (host port mapping) | `8080` | No | Change only if `8080` is already in use locally |
| `NPTC_FRONTEND_BASE_URL` | `deploy/compose.yml`'s `keycloak` service → realm import (`deploy/keycloak/realm/nptc-realm.json`'s `${NPTC_FRONTEND_BASE_URL}` placeholder), **and** `nptc.settings.ApiSettings` (backend, issue #41) | `http://localhost:8081` | No | Must equal `http://localhost:<NPTC_WEB_PORT>` for the compose stack. Set `http://localhost:5173` to use the Vite dev server instead ([`local-development.md`](local-development.md)), and recreate `keycloak`; set the frontend's real origin in any other deployment. Since #41 it also names the single browser origin the API accepts cross-origin: ADR-0021 has the browser hold the access token and call the API directly, so this is load-bearing rather than cosmetic. One value deliberately, so the origin Keycloak redirects to and the origin the API accepts cannot drift apart |
| `NPTC_WEB_PORT` | `deploy/compose.yml`'s `web` service (host port mapping) | `8081` | No | Change only if `8081` is in use. Update the port in `NPTC_FRONTEND_BASE_URL` to match. The default avoids `5173`, so the stack and `pnpm dev` can coexist on one machine |
| `NPTC_FSN_SEMANTIC_TAG` | `nptc.settings.ApiSettings` (backend, issue #144, FR-98) | `intact` | No | Declares whether every binding's `fsn` has its semantic tag intact or stripped (`nptc.api.labels.fsn_provenance`). `"intact"` is the only value accepted today - no binding's `fsn` is stripped on the read path, so `"stripped"` is refused at settings-construction time rather than silently making the served payload lie about what it serves. It does not govern the `fsn` on a public list or search row, which is always stripped (FR-83) and declared `stripped`. A placeholder for FR-66's own export configuration, which does not exist yet (P4) |
| `NPTC_TERMS_CURRENT_VERSION` | `nptc.settings.ApiSettings` (backend, NFR-45, NFR-47, ADR-0043) | *(blank, meaning the version packaged with the release)* | No | Names the terms file `backend/src/nptc/terms/versions/<version>.md` that every contributor must have accepted. A version is a zero-padded date such as `2026-10-06`. The API refuses to start when the named version has no file, because contributions would be refused with nothing to show the user. Moving to a new version means adding a new file and naming it here: users then accept it before their next contribution. Never edit a published file. Setting this to an earlier version also asks every user to accept again, because the check is equality, not order. The first file is temporary placeholder text. An empty or whitespace-only value counts as unset. The variable name is not checked: a typo such as `NPTC_TERMS_CURENT_VERSION` is dropped silently and leaves the packaged version in force |
| `NPTC_MAX_PREFERRED_TERM_LENGTH` | `nptc.settings.ApiSettings` (backend, issue #152, FR-86) | *(blank, meaning unset - no warning ever produced)* | No | The preferred-term length past which the designation-amendment route warns, without ever blocking the save. Unset is the default and must stay the default: no maximum has been nominated yet (PRD open item OI-1) - see [`GET /catalogue/admin/preferred-term-length-distribution`](#the-fr-87-length-distribution-report) for the report that informs choosing one. An empty or whitespace-only value counts as unset, so a blank `NPTC_MAX_PREFERRED_TERM_LENGTH=` line in a compose file or `.env` is safe. A value below 1, or one that is not a whole number, is refused when the API starts. The variable name is not checked: a typo such as `NPTC_MAX_PREFERRED_TERM_LENGHT` is dropped silently and leaves the maximum unset |
| `NPTC_ANON_RATE_LIMIT_REQUESTS` | `nptc.settings.ApiSettings` (backend, FR-22, NFR-24) | *(blank, meaning 600)* | No | How many requests one anonymous client address may make in each window. See [Anonymous rate limit](#anonymous-rate-limit-fr-22-nfr-24). A value below 1 or a non-whole number stops the API at start-up. The variable name is not checked: a typo is dropped silently and leaves the default in force |
| `NPTC_ANON_RATE_LIMIT_WINDOW_SECONDS` | `nptc.settings.ApiSettings` (backend, FR-22, NFR-24) | *(blank, meaning 60)* | No | The length of that window in seconds. A caller refused with a 429 can try again when the window closes, and the `Retry-After` header says how many seconds that is. The same validation as the line above |
| `NPTC_BULK_ARTEFACTS_URL` | `nptc.settings.ApiSettings` (backend, FR-22) | *(blank, meaning the documentation page on bulk retrieval)* | No | Where the body of every 429 tells the caller to fetch the whole catalogue instead. An absolute `http(s)` URL, or a path on the same origin that starts with a single `/`. Set it to the release location once releases are published (FR-21). Anything else stops the API at start-up |
| `NPTC_TRUSTED_PROXIES` | `deploy/compose.yml`'s `backend` service; `nptc.settings.ApiSettings` (backend, NFR-24) | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` in compose; empty in the API itself | No | The addresses allowed to tell the API a caller's real address through `X-Forwarded-For`. Comma-separated addresses or CIDR ranges. Empty means the header is never read. Compose passes it with `-` rather than `:-`, so setting it to an explicit empty value turns the trust off instead of restoring the default. A malformed entry stops the API at start-up. See [Anonymous rate limit](#anonymous-rate-limit-fr-22-nfr-24) |
| `VITE_OIDC_ISSUER` | `frontend/src/auth/config.ts` (browser, issue #41, NFR-01); compose passes it to the `web` image build | `http://localhost:8080/realms/nptc` - required at build time | No | The realm's issuer URL, as reachable **from the browser** rather than from inside the compose network. Inlined into the built bundle by Vite, so changing it means rebuilding the `web` image; the sign-in flow throws, naming this variable, if it is unset |
| `VITE_OIDC_CLIENT_ID` | `frontend/src/auth/config.ts` (browser, issue #41, NFR-01); compose passes it to the `web` image build | `nptc-frontend` - required at build time | No | The realm's public client. Must match `nptc-frontend` in the committed realm (ADR-0014). Rebuild the `web` image after changing it |
| `VITE_API_BASE_URL` | `frontend/src/api/use-api-client.ts` (browser) | *(unset - same origin)* | No | The API's origin when it differs from the web app's. Unset in the compose stack, where Caddy proxies `/api`. Set `http://localhost:8000` in `frontend/.env` for `pnpm dev` |
| `NPTC_TX_BASE_URL` | `nptc_shared.terminology` (backend and transform) | `https://tx.ontoserver.csiro.au/fhir` | No | The default is fine; point it at a local Ontoserver to work offline |
| `NPTC_TX_TOKEN` | `nptc_shared.terminology` (backend and transform) | *(empty — anonymous)* | Yes | Leave empty — `tx.ontoserver.csiro.au` accepts anonymous requests |
| `NPTC_TX_TIMEOUT_SECONDS` | `nptc_shared.terminology` (backend and transform) | `30` | No | `30` is fine for the P3 batch sweep; a deployment exposing `GET /api/v1/terminology/concepts/{code}` (FR-26, issue #240) also bounds an interactive form request with this value, so consider a lower value there |
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
`NPTC_INDEXER_DATABASE_URL` is deliberately not a third fallback in that same chain -
`nptc.db.property_reconciler.get_indexer_engine()` reads it alone, with **empty as its own
valid, fail-closed default** (unlike the two DSNs above): "index reconciliation is not
configured" is a safe deployment posture, so construction never raises the way
`MigrationSettings` does on a missing value. Never falls back to `NPTC_MIGRATION_DATABASE_URL`
(that role can `CREATE ROLE`/`DROP TABLE` - far more than a reconciler needs) or to
`NPTC_DATABASE_URL` (the app role provably cannot do DDL at all, so falling back to it would
only trade a clear refusal for a permission error deep inside a reconciliation run).
The `NPTC_TX_*` variables are the first read by Python code —
`nptc_shared.terminology.TerminologyConfig.from_env()` (FR-53), used identically by the
backend and the transform (see [ADR-0003](../adr/0003-terminology-client-in-shared.md)
and [the terminology client architecture doc](../architecture/terminology-client.md)). An
empty or unset `NPTC_TX_TOKEN` means anonymous access and sends no `Authorization`
header; setting it sends a static bearer token, the only auth scheme supported today —
OAuth2 client-credentials is deferred. `NPTC_TX_TIMEOUT_SECONDS`/`NPTC_TX_MAX_RETRIES`
were tuned only against the P3 batch sweep until issue #240 (FR-26) added the first
*interactive* caller, `GET /api/v1/terminology/concepts/{code}` — an operator exposing
that route should weigh a lower timeout there against the sweep's own tolerance for a
slow, retried request. Retry backoff timings are `TerminologyConfig`
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
air-gapped deployment that cannot reach a discovery endpoint. The URL must answer directly:
PyJWT 2.14 and later treat a redirect as an unreachable endpoint, so the backend serves
already-cached keys until the fallback age limit. A process that has not yet fetched the set
rejects every token immediately. `NPTC_JWKS_CACHE_SECONDS` and
`NPTC_JWKS_REFRESH_COOLDOWN_SECONDS` tune `nptc.auth.jwks.SigningKeys`'s own key cache and its
refresh cooldown against `kid`-spraying (the same cooldown also covers retrying a known `kid`
during an IdP outage, so it does not turn into a request-latency outage); the defaults are
untuned constants, not a measurement against a specific deployment. `SigningKeys`'s
`max_fallback_age_seconds` (how long an unreachable-endpoint fallback key is trusted for, default
`10 * NPTC_JWKS_CACHE_SECONDS`) is deliberately **not** an environment variable alongside these
two - it derives from `NPTC_JWKS_CACHE_SECONDS` rather than adding a fifth knob for what is a
rare-outage safety margin, not routine deployment tuning.

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

**The login theme is selected here and mounted separately.** The realm file sets
`"loginTheme": "nptc"`, and `deploy/compose.yml` mounts `deploy/keycloak/themes/nptc` read-only
at `/opt/keycloak/themes/nptc`. The two must agree: a realm that names a theme no mount
provides falls back to Keycloak's own look without an error
(`backend/tests/test_keycloak_login_theme.py` checks this). See
[the login theme](../architecture/authentication.md#the-login-theme).

**`${NPTC_FRONTEND_BASE_URL}` is the file's only placeholder.** Keycloak resolves `${VAR}` in
an imported realm file from the container's environment; `deploy/compose.yml` passes the
`NPTC_FRONTEND_BASE_URL` environment variable through for exactly this. It drives the
`nptc-frontend` client's `rootUrl`, `redirectUris`, `webOrigins` and post-logout redirect URI
— the one part of this realm that is genuinely per-deployment. It also sets the
`frame-ancestors` source in the realm's `Content-Security-Policy`, so the app may load
Keycloak in its hidden session-renewal iframe (see
[silent renewal](../architecture/authentication.md#silent-renewal-needs-keycloak-to-allow-framing)). The registration page also
reads it, through the realm attribute `nptcFrontendBaseUrl`, to build its links to `/privacy` and
`/terms`. Everything else in the file is static, which is what makes "identical realm on every clean clone" testable at all
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

## Anonymous rate limit (FR-22, NFR-24)

The API limits each anonymous client address to `NPTC_ANON_RATE_LIMIT_REQUESTS` requests in
each `NPTC_ANON_RATE_LIMIT_WINDOW_SECONDS` window. The defaults, 600 requests per 60 seconds,
leave room for the web app, which makes several calls per page, and for a script that walks the
whole catalogue in pages. They are a judgement, not a measurement. Lower them only if you see
abuse, and raise them if a legitimate consumer is refused. The policy and the reasons behind it
are in [`public-api.md`](../architecture/public-api.md#rate-limiting-and-caching).

**What counts.** A request with no `Authorization` header. A request that carries one is never
counted, even if its token is later refused. Requests from a loopback address (`127.0.0.1`,
`::1`) are never limited: the compose healthcheck calls from there, and so does anything you run
on the same machine.

**Counters live in one process.** The API keeps them in memory. The compose stack runs one API
process, so the limit is exact. If you run more than one worker or replica, each keeps its own
counters and a caller's real budget is the limit times the number of processes. A restart clears
them.

**Telling the API who the caller is.** Behind Caddy the API's connecting address is Caddy's, so
without help every visitor would share one budget. Caddy adds the visitor's address to
`X-Forwarded-For`, and `NPTC_TRUSTED_PROXIES` names the addresses the API believes that header
from. The compose default trusts the private ranges Docker assigns to compose networks. That is
safe because the `backend` service publishes no port, so only the `web` container can reach it.

- **Direct access.** If the API is reachable without a proxy, set `NPTC_TRUSTED_PROXIES` to empty.
  Otherwise any caller could write their own `X-Forwarded-For` and choose their own budget.
- **Another front end.** Behind a load balancer or CDN, list its addresses, and make sure it
  appends the connecting address to `X-Forwarded-For` rather than passing a caller's header on
  unchanged. The API reads the header from the right and stops at the first address it does not
  trust.
- **Audit log.** The same address is what the audit log records as the actor's address. Before
  this setting existed, events written behind Caddy recorded Caddy's address.

**IPv6.** Every address in one `/64` subnet shares one budget, because a caller can rotate
through the addresses in a subnet at no cost.

**Telling a caller where the bulk artefacts are.** A refused request gets a 429 whose body names
`NPTC_BULK_ARTEFACTS_URL`. No release exists to serve until FR-21 lands, so the default points to
the documentation on bulk retrieval. Set it to the release location when one exists.

## Choosing a maximum preferred-term length (`NPTC_MAX_PREFERRED_TERM_LENGTH`)

FR-86's maximum is deliberately unset out of the box: RCPA-QAP has never had one to
enforce, and the platform owes them the data needed to choose one (PRD open item OI-1)
before it makes sense to set anything. Once a value is set, saving a preferred term past
it still succeeds — the amendment response carries a length warning in its `warnings` list, never a
4xx, so an existing over-length entry never becomes uneditable. The editing screen shows it
under **Check these terms**. The amendment route also logs one warning record
when it changes a preferred term to one over the maximum. The record names the entry's
business key and its length, never the term. It is written as the save is made, so if the
request fails afterwards and its transaction rolls back, the record remains although the
term was not stored. A batch save logs one summary record, after every save in the batch has
succeeded, naming the count and the first ten business keys.

### The FR-87 length distribution report

`GET /catalogue/admin/preferred-term-length-distribution` (gated on
`catalogue.edit_published`, the same permission every other admin route in
`catalogue_admin.py` uses) answers "how many entries would this maximum affect" without
anyone writing a query by hand. Its `buckets` array has one row per preferred-term length
that actually occurs in the catalogue — not every possible length — carrying that
`length`, how many entries (`count`) have a preferred term exactly that long, and
`entries_exceeding`: how many entries a maximum set to that length would warn on.
`maximum` is the longest preferred term currently in the catalogue, or `null` for an empty
one. Every entry counts regardless of status (draft and withdrawn as well as active):
`POST .../designations/amendment` can warn on any of them, since it resolves an entry with
no status filter of its own, so scoping the report to active entries alone would undercount
what a chosen maximum affects. See [`docs/user/`](../user/) for how an administrator reads
this report end to end, including how to read a candidate length the report has no exact
row for.
