# ADR-0043: Terms acceptance is stored by the platform and enforced on the write path

**Status:** Accepted
**Date:** 2026-10-03

## Context

NFR-45 requires the terms of use to be presented at registration with positive acceptance.
The platform must record which version each user accepted and when. When the terms change,
existing users must accept the new version before their next contribution, and the prior
acceptance record must be kept. NFR-47 requires the terms to be versioned in the repository
and rendered from a single source. NFR-14 asks for the privacy and collection notices at
registration, and NFR-08 asks for an audit event on every state-changing write.

ADR-0021 left this open: `/register` hands off to Keycloak's own registration page, which
captures no acceptance. Issue #183 listed four shapes. This ADR compares them on one set of
criteria and picks one, so that #434 (API) and #435 (gate) can be built to a settled shape.

Three facts about the current stack shape the comparison:

- **The platform learns of a user late.** `resolve_user_for_claims` creates the `app_user`
  row, and grants `Provisional`, on the first valid token an API call carries. Registration
  in Keycloak happens before the platform knows the user exists.
- **Keycloak's two built-in options record too little.** The `TERMS_AND_CONDITIONS` required
  action stores one timestamp in a Keycloak user attribute and no version. The registration
  form action (`RegistrationTermsAndConditions`) forces a checkbox but persists nothing.
  These come from the upstream source of the pinned image (Keycloak 26.7.4-0). Check them
  again if the image changes.
- **The stack has no Keycloak extension point.** The realm is imported from
  `nptc-realm.json` (ADR-0014). A theme can change what a page shows. Anything that records a
  version would need a custom Java provider, which this repository does not build or ship.

## Decision

**The platform stores acceptance in its own database, and the API refuses contributions from
a signed-in user who has not accepted the current version.** The user meets this as a gate
shown straight after sign-in. Keycloak's registration page carries the collection notice and
links to the privacy policy and the terms.

### What is stored

- Acceptance is an **append-only table**: user, version, timestamp. It is a table and not a
  column on `app_user`, because NFR-45 requires prior acceptances to be kept.
- The row refers to the internal user id (ADR-0015). Account closure pseudonymises the user
  and keeps the row, so the record of what was accepted survives closure.
- The version is an **opaque string compared for equality**. Any difference from the current
  version means "not accepted", so rolling a version back also asks for re-acceptance. Issue
  #434 chooses the format (date or counter).

### How it is enforced

- **The server enforces it, and the SPA gate only presents it.** A signed-in user without
  acceptance of the current version receives a refusal on every *contribution*, which is the
  scope NFR-45 sets ("before their next contribution"). This matches NFR-20: the interface is
  never the authority.
- **These stay open to a user who has not accepted:** all reads, the accept request itself,
  sign-out, account closure (NFR-17) and requests to see or correct personal information
  (NFR-14, NFR-16), for the routes that exist or are added later. A user who declines a new
  version must still be able to leave and to exercise their privacy rights. #434 keeps the
  exempt routes in one explicit list, and the refusal and each exemption have a test.
- **Any state-changing route not on the exempt list is refused.** The default is refusal, so
  a write route added later is covered without anyone remembering to opt it in. #434 builds
  an exempt list, never a list of "contribution" routes.
- **The refusal is a 403 with a typed body that carries a machine `code` beside a
  human-readable `detail`.** Existing error bodies hold only a sentence in `detail`, so this
  is a new field, declared in the OpenAPI document. The version-conflict refusal already
  returns a typed body in the same way. The SPA routes on `code` and never parses `detail`.
  The response adds no `WWW-Authenticate` header. The existing step-up refusal uses that
  header because RFC 9470 defines it for authentication strength, and terms acceptance is not
  an authentication matter. The step-up loop (ADR-0036) is untouched. #434 sets the exact
  `code` string and the body's name.
- Whether the check rides on `principal_for` or on a separate dependency is #434's call. This
  ADR fixes the contract, not the wiring. Per FR-44 the check is a permission or a named
  condition, never a role name, and the denied case needs its own test.

### What the accept request carries

The accept request **names the version the user saw**. The server refuses it, with a distinct
conflict response that reports the current version, when the named version is not the
current one. Without this rule, a user who loaded the gate under version A and clicks accept
after version B deploys would be recorded as accepting B, a text they never read. That would
break NFR-47, which needs the accepted text to be reproducible exactly. After the refusal the
SPA shows the gate again under the new version.

### How the SPA learns the state

`GET /api/v1/auth/me`, or a sibling endpoint, reports the current version and whether the
caller has accepted it. The terms page and the gate render from the API's copy of the text,
which the API can serve for the current version and for any earlier one.

### Placeholder terms

The repository holds **one Markdown file per version** in one directory, each with its
version, effective date and text (NFR-47). A published file is never edited. A new version is
a new file, and one declared setting names the current one. The service refuses to start if
that setting names a version with no file, as it does for a malformed terminology setting.
Otherwise the gate would have nothing to show and every contribution would be refused.

The API serves any version by its id, so the text a user accepted can be reproduced exactly
from the deployed service and not only from git history. The SPA renders what the API
returns, so there is no second copy. The first file is marked as temporary text, and
production adds its own file as a later version. The legal wording of the contribution
licence (NFR-46) is outside this decision.

### Audit

Each acceptance emits an audit event that names the version (NFR-08).

### How this reads "at registration"

NFR-14 and NFR-45 both say "at registration" but ask for different things, so they are read
separately.

- **NFR-14 (privacy policy and collection notice, APP 1 and APP 5).** The notice must reach
  the user at or before the point where personal information is collected, and Keycloak's
  registration form is that point. So the collection notice and a link to the privacy policy
  go **on Keycloak's registration page**, in the #432 theme. The gate repeats the collection
  notice, so a user who registered before the notice existed also sees it.
- **NFR-45 (terms, positive acceptance, recorded version).** This ADR reads "at registration"
  as *before the user's first contribution*, with the gate shown the moment registration
  returns the user to the application. Keycloak's page links the terms. The gate gives the
  positive acceptance and the record. We chose this reading because it is the only one that
  keeps the version in the platform database and also covers existing users when a version
  changes.

If a reviewer holds that positive acceptance must happen on Keycloak's own page, shapes 3 and
4 below need another look. Each would need a custom Keycloak provider to record a version.

## Comparison

All four shapes from #183, on the same four criteria.

| Shape | Where acceptance is stored | Re-acceptance on a new version | Effort | Fit with NFR-01 and NFR-02 |
|---|---|---|---|---|
| **1. SPA interstitial before Keycloak registration** | Nowhere durable. No user exists yet, so only a browser-side flag is possible. | None. Existing users are never prompted, and the flag is lost with the browser. | Low | Keeps the redirect to Keycloak. Opening Keycloak's registration URL directly skips the step. |
| **2. Gate after first sign-in** (chosen) | Platform database, append-only table of user, version and timestamp. | The stored version differs from the current one, so the gate shows again. | Medium: a table, an endpoint and a gate (#434, #435). | Registration stays ordinary username and password in Keycloak. The SPA still never handles credentials. |
| **3. Keycloak `TERMS_AND_CONDITIONS` required action** | Keycloak user attribute, a timestamp only. | Needs a custom Java provider or an admin-API sweep that clears the attribute on each release. | High: a Java provider to build, ship and keep in step with Keycloak upgrades. | Native to Keycloak. The record sits outside the platform database, and a version needs custom code. |
| **4. Themed Keycloak registration page** | Nowhere. The form action forces the checkbox and persists nothing. | None. It runs only at registration, so existing users are never prompted. | Low to medium: the #432 theme plus the checkbox. | Native to the registration page. A custom provider would be needed to store anything. |

Shapes 1 and 4 cannot meet NFR-45's re-acceptance sentence. Shape 3 can meet it only with
custom Keycloak code. Shape 2 meets it with the stack's existing parts.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| **SPA interstitial before registration** | There is no user to record against, a client-side flag is bypassed by opening Keycloak's registration URL directly, and a user who accepted an old version is never asked again. |
| **Keycloak `TERMS_AND_CONDITIONS` required action** | It stores a timestamp and no version, so NFR-45 cannot be met without a custom Java provider or an admin-API sweep. This repository has no Keycloak extension point, and the record would live outside the database that holds everything else a reviewer must query. |
| **Themed registration page with a required checkbox** | The form action persists nothing. The checkbox gates one moment and leaves no record, and it never reaches existing users. |
| **A `terms_accepted_version` column on `app_user`** | A single column holds only the latest acceptance. NFR-45 requires the prior acceptance record to be kept. |
| **Refusing every state-changing request** | Read literally, it refuses the accept request itself, so the gate could never be passed. It also blocks account closure (NFR-17) and privacy requests (NFR-14, NFR-16) for a user who declines a new version. NFR-45 asks only to block the next contribution. |
| **One terms file that is overwritten for each version** | The deployed API could then show only the current text, and the text of an earlier acceptance would survive only in git history. NFR-47 asks for reproduction of the exact accepted text. |
| **A `WWW-Authenticate` challenge for the terms refusal** | RFC 9470 defines that header for authentication strength. Terms acceptance is not an authentication matter, and reusing it would blur the step-up loop in ADR-0036. A body code is enough for the SPA to route on. |
| **Enforcing in the SPA only** | A direct API call would contribute without acceptance. NFR-20 and test item 15 in the PRD require the server to refuse. |

## Consequences

- #434 builds the table, the read and accept endpoints, the refusal code, the exempt-route
  list, the per-version terms files and the audit event. #435 builds the terms page and the
  gate. #432 adds the collection notice and the privacy and terms links to the Keycloak
  registration page. The three issue bodies are edited to match this ADR.
- Enforcement runs per request on the write path. The cost is one read of the user's
  latest acceptance, so #434 should index the table by user.
- A user who registers and never returns has an account but no acceptance row. The account
  cannot contribute, which is the intended outcome.
- Keycloak's own terms attribute stays unused. Nothing in the platform reads it.
- NFR-14, NFR-45 and NFR-47, and PRD test item 15, stay `planned` until #434 and #435 land.
- The account pseudonymisation path (NFR-17) is untouched: acceptance rows keep the internal
  user id and carry no personal data.
