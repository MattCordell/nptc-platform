"""NPTC Catalogue Maintenance Platform - API and background worker.

Package layout (populated as the corresponding GitHub issues land - see the
repository layout in the PRD's delivery plan):

- api/          routers, dependencies, OpenAPI wiring (P1-9)
- auth/         OIDC verification, permission framework (P1-3, P1-4, FR-44)
- audit/        append-only log and hash chain (P1-2, NFR-08-10)
- catalogue/    entries, designations, code bindings (P1-5)
- registry/     property registry, datatype handler registry (P1-6, FR-77)
- terminology/  FR-53 client interface, Ontoserver and stub implementations
- submissions/  workflow state machine, interest, comments (P2)
- validation/   findings, sweep orchestration (P3)
- releases/     snapshots, export config versions (P4)
- exports/      csv, xlsx, fhir supplement renderers (P4)
- jobs/         SKIP LOCKED job queue and scheduler (P3)
- db/           models and Alembic environment (P1-1, issue #33); session.py still owed
"""

__version__ = "0.0.0"
