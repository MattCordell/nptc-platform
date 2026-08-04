# ADR-0001: Technology stack

**Status:** Accepted
**Date:** 2026-08-04

## Context

The PRD (`docs/prd/NPTC-Catalogue-Platform-PRD.md`, §14.1) specifies a stack and argues
its reasoning at length, including alternatives it rejects. This ADR records the
decision as taken for this build, so the reasoning is discoverable from `git blame`
and code review without re-reading the whole PRD, and so it is not silently relitigated
by a future contributor who has not read §14.1.

## Decision

| Layer | Choice |
|---|---|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2 |
| Database | PostgreSQL 16+, extensions `pg_trgm` and `unaccent` |
| Frontend | React 18+, TypeScript, Vite, TanStack Query; client generated from the backend's OpenAPI document via `openapi-typescript` |
| Identity | Keycloak, realm configuration managed as code |
| Background jobs | PostgreSQL-backed queue using `SELECT ... FOR UPDATE SKIP LOCKED` |
| Reverse proxy | Caddy |
| Packaging | Docker Compose |

The P0 seeding transform is a separate Python package (`transform/`) sharing a `shared/`
library with the backend, rather than a script bolted onto either side — see §1 of the
repository layout in the delivery plan.

## Rationale (summarised from PRD §14.1 — read that section for the full argument)

- **One language for backend and tooling.** The seeding transform, the anomaly report
  and the Ontoserver batch validation are naturally Python regardless of backend choice.
  A Python backend removes a second language from the repository rather than adding one,
  and lets `shared/` be imported by both without a translation layer.
- **JSONB + typed registry over EAV or runtime DDL** for the property registry (PRD
  §6.5). Both alternatives are well-documented ways to make a system progressively
  harder to change, which is the opposite of the stated goal (G6).
- **One database, not two data stores.** `pg_trgm` and PostgreSQL full-text search are
  sufficient at the planning scale (20,000 entries, PRD §5.1). Elasticsearch or a vector
  store would add an operational component and a consistency gap between the search
  index and the audit-critical database of record, for no benefit at this size.
- **Postgres-backed job queue over Celery/Redis.** Avoids a fourth stateful component.
  Acceptable to revisit if the team already operates Redis, but not the default.
- **Keycloak over hand-rolled auth.** OIDC authorisation code flow with PKCE, local user
  database enabled with external federation switched off at launch (NFR-02) — enabling
  Entra or Google sign-in later is realm configuration, not an application change.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Full TypeScript backend (NestJS/Prisma or Next.js), Python retained only for the transform | Explicitly acceptable per the PRD if the team's TypeScript experience is materially stronger than its Python experience — not the case here, and it would duplicate the terminology client between the transform and the backend unless the transform calls the API instead of the database directly. |
| Django | The property registry works against the ORM's assumptions; the code ends up fighting the framework exactly where flexibility matters most. |
| Elasticsearch / a vector store | Unjustified at 20,000 entries; introduces a second source of truth for search. |
| A NoSQL primary store | The audit log needs transactional integrity and enforced privileges; the release model needs referential integrity across immutable snapshots. Both are native to a relational database. |
| Business logic in database triggers/functions | Invisible to the test suite and to code review. |

## Consequences

- `mypy --strict` and Pydantic v2 carry the runtime validation the property registry
  depends on; this is a real cost in verbosity, accepted for the type safety.
- Keycloak is the single largest memory consumer in the deployment topology (PRD §14.4)
  and pushes the sizing recommendation from 4 GB to 8 GB — a one-VM-size-step cost,
  accepted for not building session/credential handling from scratch.
- Revisiting this decision mid-build is expensive (a rewrite, not a refactor). If it
  needs revisiting, open a new ADR that supersedes this one rather than editing it in
  place — this file is a historical record of what was decided and why.
