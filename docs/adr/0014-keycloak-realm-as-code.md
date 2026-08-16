# ADR-0014: Keycloak realm as code, imported on compose up

**Status:** Accepted
**Date:** 2026-08-17

## Context

`deploy/compose.yml` started Keycloak with a bare `start-dev` and nothing else: an empty
instance with only a bootstrap admin. Everything that makes it useful - a realm, a client,
PKCE, an `aud` claim a resource server can verify - would have to be clicked through the
admin console, which NFR-03 explicitly forbids ("Hand-configured realms are undocumented,
unreproducible, and the most common cause of a broken environment rebuild"). This issue is
first in the identity chain in `P1-SEQUENCING.md`: #41 (OIDC PKCE login), #42 (internal
`user`/`user_identity`), and #43 (server-side JWT verification, which needs a correct `aud`)
all build on the realm this issue defines.

Four things needed settling, each with a real wrong answer available: how role grants are
represented (or deliberately are not) in the realm at all; whether Keycloak's own storage
persists across restarts; how a client gets a token audience a resource server can check,
given the realm's own client-scope mechanism turned out to have a sharp edge; and how the
realm is proven correct without a login flow to exercise, since the frontend that would
drive one doesn't exist until #41.

## Decision

**`deploy/keycloak/realm/nptc-realm.json` is hand-authored, not exported.** An export from a
running Keycloak carries hundreds of version-specific defaults (internal IDs, timestamps,
every built-in flow's binding) that make later diffs unreadable and tie the file to the
exact server version that produced it. The file declares only what this issue's decisions
actually need; everything else is left for Keycloak's own realm-creation defaults to supply.
`deploy/compose.yml`'s `keycloak` service runs `start-dev --import-realm` with
`./keycloak/realm:/opt/keycloak/data/import:ro` bind-mounted in, so a clean clone plus
`docker compose up` yields the realm with no console step in any runbook.

**No application roles in the realm.** Keycloak authenticates; the platform authorises.
NFR-07 is explicit - "Authorisation decisions are made server-side from the internal user
record, never from claims in the token" - and FR-40/FR-41 put role grants on the admin user
dashboard, i.e. in the platform DB (#42/#44). Putting `observer`/`member`/`reviewer`/
`administrator` in the realm too would create a second place a role can be granted and a
permanent drift risk between the two. `nptc-realm.json` therefore declares no `roles`/
`realmRoles` block at all - `backend/tests/test_keycloak_realm.py::test_no_application_roles_declared_in_the_realm`
enforces this by asserting both keys are absent from the committed file, not merely empty.
Both clients also set `fullScopeAllowed: false`, so no realm role can leak into a token even
if one is created by hand later. The GitHub issue's "role mappings" checkbox is satisfied
instead by the client scope and protocol mappers described below (audience, plus the
standard `profile`/`email` claims).

**Keycloak stays ephemeral** (`start-dev`, no volume). Every fresh container re-imports the
committed realm on startup, so reproducibility is by construction and any console tinkering
is discarded on restart - a stronger guarantee than the issue's "delete the volume" wording,
which assumes a persistence layer that does not exist yet. Persisting Keycloak (`KC_DB=postgres`,
a production start mode) is deployment hardening (P5), not here.

**No client-level `clientScopes` array in the realm file - the `nptc-api-audience` mapper
sits directly on `nptc-frontend` instead.** The original design (this issue's plan) called
for a shared `clientScopes` entry the way Keycloak's admin console creates one. Building the
realm against a real container (not merely reading Keycloak's source) surfaced a sharp
edge: declaring a top-level `clientScopes` array in an imported realm replaces Keycloak's own
built-in default scopes wholesale rather than adding to them. With only `nptc-api-audience`
declared, the admin API showed exactly one real client scope after import (`nptc-api-audience`
plus, inconsistently, `offline_access` - itself further proof this path depends on undocumented,
version-specific behaviour rather than a stable contract); `nptc-frontend`'s references to
the built-in `profile`/`email`/`web-origins` scopes were silently dropped, since those scope
objects never existed in the imported realm at all. Confirmed empirically against a scratch
realm created via the admin API (which does get the full built-in set): the two behaviours
diverge only on whether `clientScopes` is present in the import payload, null or not. Omitting
`clientScopes` entirely and instead giving `nptc-frontend` its own `protocolMappers` entry
(`oidc-audience-mapper`, `included.client.audience: nptc-api`, `access.token.claim: true`)
produces the same `aud: "nptc-api"` claim - verified against a real token from a real login -
without disturbing the standard scopes. A shared client scope would only earn its reuse
benefit if a second client needed the same audience mapper, which none does yet; a
client-level mapper is simpler and does not depend on realm-import behaviour this issue had
to discover by trial rather than read in Keycloak's own documentation.

**`nptc-frontend`** is the SPA (#41): `publicClient: true` (no secret to leak),
`standardFlowEnabled: true`, `implicitFlowEnabled`/`directAccessGrantsEnabled`/
`serviceAccountsEnabled: false`, `fullScopeAllowed: false`,
`pkce.code.challenge.method: "S256"`. `rootUrl`/`redirectUris`/`webOrigins`/the post-logout
redirect all derive from one `${NPTC_FRONTEND_BASE_URL}` placeholder - the one part of this
realm that is genuinely per-deployment (hardcoding `http://localhost:5173` in a file destined
for production would be the mistake worth avoiding). Its `defaultClientScopes` are
`profile`/`email`/`web-origins` only - `roles` is deliberately left out even though Keycloak
creates it as a realm default, so no `realm_access`/`resource_access` claim appears in a
token at all, keeping the "authorisation never comes from the token" line as literal as
possible pending #44's role model.

**`nptc-api`** is an audience target only, so #43 has a real `aud` to verify against: every
flow disabled, `publicClient: true` (never confidential, so there is no secret field at all),
`redirectUris`/`webOrigins`/`defaultClientScopes`/`optionalClientScopes` all empty. It is
never logged into; it exists to be named in `nptc-frontend`'s tokens.

**Both kinds of test.** `backend/tests/test_keycloak_realm.py` has an offline group (no
Docker) walking the committed JSON for secret-shaped keys/values (NFR-26/NFR-35 - the
principal failure mode is a re-exported realm bringing back a client secret and whatever
test users a maintainer was poking at), asserting the client and scope shapes above, and
checking `compose.yml`'s bind mount resolves to the realm directory under test - parsed, not
hardcoded a second time. The integration group (`@pytest.mark.integration`,
`@pytest.mark.req("NFR-03")`) starts the pinned Keycloak image via testcontainers'
`DockerContainer` with the realm directory mounted read-only, then asserts against the real
discovery document (`issuer`, `S256` in `code_challenge_methods_supported`) and the admin API
(`nptc-frontend` public with PKCE). `conftest.py`'s `_image_from_compose()` is generalised to
`image_from_compose(service: str = "postgres")` and exported, so this reuses the repo's "one
parser for compose.yml" rule instead of adding a second one; `test_keycloak_realm.py` loads
it via `importlib.util.spec_from_file_location` rather than `import conftest`, since
`backend/tests` has no `__init__.py` and pytest's `--import-mode=importlib` does not register
`conftest.py` under an importable `conftest` name for a sibling module to find.

**`.github/workflows/ci.yml`'s `backend-integration` job pulls the Keycloak image before the
egress block**, alongside the existing Postgres pull, using the same pyyaml-based reader -
otherwise the container start is a registry miss against a blocked network, the same
reasoning ADR-0011 already applied to Postgres.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| `keycloak-config-cli` | A second tool and image to pin, for declarative reconciliation this project does not yet need - `--import-realm` already does the one thing required (import once, on startup). |
| The Keycloak Terraform provider | No Terraform anywhere in this stack (ADR-0001); would introduce a second infrastructure-as-code tool for a single realm. |
| An admin-CLI (`kcadm.sh`) shell script run at container startup | Imperative and order-dependent - reinvents what `--import-realm` already does declaratively, and is harder to diff. |
| A shared `nptc-api-audience` client scope (the original plan) | Declaring a top-level `clientScopes` array in the realm file replaces Keycloak's built-in default scopes wholesale rather than adding to them - confirmed by building the realm against a real container, not merely reading Keycloak's source - silently dropping `profile`/`email`/`web-origins` from `nptc-frontend`. A client-level `protocolMappers` entry on `nptc-frontend` produces the same `aud` claim without the collateral damage, and no second client needs the mapper today. |
| Including the `roles` client scope in `nptc-frontend`'s default scopes (matching Keycloak's own default) | Even with `fullScopeAllowed: false` and no realm roles beyond Keycloak's built-ins, it would still attach a `realm_access`/`resource_access` claim (naming e.g. `default-roles-nptc`) to every token - a role-shaped claim in the token is exactly what NFR-07 says authorisation must never be based on, so it is left off entirely rather than merely unused. |
| Persisting Keycloak (`KC_DB=postgres`, a production start mode) | Deployment hardening that belongs to P5, not this issue - an ephemeral, always-reimported realm is a *stronger* reproducibility guarantee for the stack this issue actually ships, not a placeholder for the real thing. |
| Exercising a full PKCE login as the realm's proof | No frontend client application exists until #41 - the discovery document plus the admin-API client assertions (and, for the audience claim, a decoded token from a manually-created test user during this issue's own verification) are the closest available proof; a login flow test belongs to #41. |

## Consequences

- #41 (OIDC PKCE login), #42 (`user`/`user_identity`), and #43 (server-side JWT verification)
  build directly on this realm - #43 in particular needs `nptc-api`'s audience exactly as
  declared here.
- `docs/operations/configuration.md`'s new "Keycloak realm import" section is the one place
  documenting that import is skipped once the realm already exists in a running instance -
  editing `nptc-realm.json` requires `docker compose down` on the `keycloak` service (or
  `down -v`, since there is no volume to preserve), not merely a restart.
- Any later client added to this realm should default to a client-level `protocolMappers`
  entry for a single-client need (as `nptc-frontend` does here) and reach for a shared
  `clientScopes` entry only once a second client genuinely needs the same mapper - the sharp
  edge documented above applies to any future top-level `clientScopes` addition, not just
  this issue's.
- NFR-03 moves to `implemented`. NFR-02 moves to `in-progress` (federation-off and local
  registration are realm-as-code now, but no login flow exercises them until #41). NFR-11
  moves to `in-progress` (the Keycloak half - its own event store enabled - is configured;
  the application half lands with #34-#38). NFR-01/04/05/06/07 stay `planned` - they need
  #41-#44's actual login flow, token verification, and role model.
