# Architecture

- [terminology-client.md](terminology-client.md) — the FR-53 terminology client contract:
  its four operations, editions and versions, error mapping, configuration and testing.
- [data-model.md](data-model.md) — the database baseline landed with issue #33: migration
  layout, naming convention, the least-privilege role model, the `audit_event` table, and
  the testcontainers test harness.
- [token-verification.md](token-verification.md) — NFR-07 server-side JWT verification
  (issue #43): the check order, the JWKS outage/refresh-cooldown design, and the
  payload-vs-header `typ` correction.
- [permissions.md](permissions.md) — the permission framework (issue #44): the PRD §4.7
  matrix as code, `Principal` derivation, the check API, the last-administrator guard, and
  the NFR-06 mandatory-admin-MFA step-up flow.
- [public-api.md](public-api.md) — the FR-20 public read API (issue #142): the endpoint
  table, the active-only visibility rule, the keyset paging contract, the search
  behaviour, and which test enforces each no-leak invariant.
- [catalogue-write-api.md](catalogue-write-api.md) — the catalogue admin API: entry read
  regardless of status (issue #228), code bindings (issue #219) and designations (issue
  #224). The endpoint tables, addressing a resource by its natural key (a binding's code,
  a designation's term) rather than an internal id, why replacement is one request rather
  than three, why a designation is edited in place rather than retired and re-added, and
  the authorisation/error mapping for each - including the different permission
  collision acknowledgement requires.
- [frontend-routing.md](frontend-routing.md) — the frontend routing skeleton and layout
  shell (issue #146): the route table as the single source of URL shapes, the FR-17 URL
  contract, why search values stay raw strings, the not-found/error surfaces, and the
  structural (not access-control) authentication seam for issue #41.
- [components.md](components.md) — the accessible component baseline (issue #148): the
  five components screens are expected to compose, the automated `axe-core` check and its
  known limits (`color-contrast` under jsdom), and the Tailwind styling strategy
  ([ADR-0025](../adr/0025-frontend-styling.md)).

Still owed: the deployment topology (PRD §14.3 is the starting point).

Populated incrementally as the corresponding backlog items land — see the
documentation-impact table in [CONTRIBUTING.md](../../CONTRIBUTING.md) for which
changes require an update here.
