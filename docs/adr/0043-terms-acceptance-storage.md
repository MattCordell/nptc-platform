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

**The platform stores acceptance in its own database, and the API refuses writes from a
signed-in user who has not accepted the current version.** The user meets this as a gate
shown straight after sign-in. Keycloak's registration page carries only a short notice and a
link to the terms.

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
  acceptance of the current version receives a refusal on every state-changing request. This
  matches NFR-20: the interface is never the authority.
- The refusal carries a **machine-readable code**, so the SPA can route to the gate without
  parsing a message. The existing MFA challenge (`principal_for`, `permission_dep` and a 403
  with a machine-readable challenge) is the model.
- Whether the check rides on `principal_for` or on a separate dependency is #434's call. This
  ADR fixes the contract, not the wiring. Per FR-44 the check is a permission or a named
  condition, never a role name, and the denied case needs its own test.
- Reads stay open. A user who has not accepted can still browse, read the terms and sign out.

### How the SPA learns the state

`GET /api/v1/auth/me`, or a sibling endpoint, reports the current version and whether the
caller has accepted it. The terms page and the gate render from the API's copy of the text.

### Placeholder terms

One Markdown file in the repository holds the version, the effective date and the text
(NFR-47). The API serves it, and the SPA renders what the API returns, so there is no second
copy. The text is marked as temporary. Production replaces the file and sets a new version.
The legal wording of the contribution licence (NFR-46) is outside this decision.

### Audit

Each acceptance emits an audit event that names the version (NFR-08).

### How this reads "at registration"

NFR-14 and NFR-45 say "at registration". This ADR reads that as *before the user's first
contribution*, with the gate shown the moment registration returns the user to the
application. Keycloak's page shows a notice and a link. The gate gives the positive
acceptance and the record. We chose this reading because it is the only one that keeps the
version in the platform database and also covers existing users when a version changes.

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
| **Enforcing in the SPA only** | A direct API call would contribute without acceptance. NFR-20 and test item 15 in the PRD require the server to refuse. |

## Consequences

- #434 builds the table, the read and accept endpoints, the refusal code and the audit event.
  #435 builds the terms page and the gate. Both bodies are edited to match this ADR.
- Enforcement runs per request on the write path. The cost is one indexed read of the user's
  latest acceptance.
- A user who registers and never returns has an account but no acceptance row. The account
  cannot contribute, which is the intended outcome.
- Keycloak's own terms attribute stays unused. Nothing in the platform reads it.
- NFR-14, NFR-45 and NFR-47, and PRD test item 15, stay `planned` until #434 and #435 land.
- The account pseudonymisation path (NFR-17) is untouched: acceptance rows keep the internal
  user id and carry no personal data.
