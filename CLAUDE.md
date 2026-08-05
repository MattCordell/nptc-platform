# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The NPTC Catalogue Maintenance Platform: a web platform replacing a hand-edited Excel
workbook for maintaining the National Pathology Test Catalogue (the SPIA Requesting
terminology, curated by RCPA-QAP, published by NCTS as a SNOMED CT reference set and
FHIR ValueSet).

**Status: pre-alpha.** Every module under `backend/src/nptc/*` and
`transform/src/nptc_transform/` is scaffolding (an `__init__.py` docstring describing
what will live there, plus a CLI that only prints a version). Don't assume an entity,
endpoint, or table described in the PRD already exists — check the actual module before
writing code that depends on it.

The authority for all behaviour is `docs/prd/NPTC-Catalogue-Platform-PRD.md`. Every
requirement is cited as `FR-nn` (functional) or `NFR-nn` (non-functional) and those IDs
are stable — use them in commits, tests, and code comments instead of restating the
requirement. `docs/adr/` records technology decisions and the alternatives rejected;
read one before relitigating a stack choice.

## Repository layout

This is a polyglot monorepo: a `uv` workspace for Python, a `pnpm` workspace for the
frontend, one shared root git repo.

```text
backend/     nptc        - FastAPI API + background worker
transform/   nptc_transform - P0 seeding transform (Excel -> import dataset), CLI via typer
shared/      nptc_shared - code imported by BOTH backend and transform (SCTID/Verhoeff
                            validation, terminology client contract) so there is never a
                            second, divergent implementation (ADR-0001, FR-74)
frontend/    nptc-frontend - React 19 + TypeScript + Vite SPA
scripts/     backlog_sync.py, traceability_check.py - repo governance tooling (Python,
                            tested, run via uv, not part of the app runtime)
docs/        prd, adr, architecture, backlog, requirements, operations, user, governance
deploy/      Docker Compose stack (compose.yml, .env.example)
```

Backend module responsibilities (each currently just an `__init__.py` stub — see
`backend/src/nptc/__init__.py` for the authoritative list and which backlog issue lands
each one):

- `api/` — routers, dependencies, OpenAPI wiring
- `auth/` — OIDC verification, permission framework (FR-44: permissions, not role names)
- `audit/` — append-only log and hash chain (NFR-08-10)
- `catalogue/` — entries, designations, code bindings
- `registry/` — property registry, datatype handler registry (FR-77: `switch` on
  datatype belongs only here)
- `terminology/` — FR-53 client interface, Ontoserver + stub implementations
- `submissions/` — workflow state machine, interest, comments
- `validation/` — findings, sweep orchestration
- `releases/` — snapshots, export config versions
- `exports/` — csv, xlsx, fhir supplement renderers
- `jobs/` — Postgres `SELECT ... FOR UPDATE SKIP LOCKED` queue and scheduler
- `db/` — models, session, Alembic environment (migrations land with issue P1-1;
  `backend/migrations/` is empty until then)

## Technology stack (ADR-0001)

| Layer | Choice |
|---|---|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, `mypy --strict` |
| Database | PostgreSQL 16+ (`pg_trgm`, `unaccent`) — one datastore, no Elasticsearch/vector store |
| Frontend | React 19, TypeScript, Vite, TanStack Query; API client generated from the backend's OpenAPI doc via `openapi-typescript` |
| Identity | Keycloak (OIDC auth code flow + PKCE) |
| Background jobs | Postgres-backed queue, not Celery/Redis |
| Reverse proxy | Caddy |
| Packaging | Docker Compose (single-command stack is NFR-41, lands with issue F-7) |

Business logic must never live in database triggers/functions (invisible to tests and
review — see PRD §14.1 and CONTRIBUTING.md).

## Commands

All Python commands run from the repo root (one `uv` workspace covers
`backend/`, `transform/`, `shared/` — do not run them from inside a package directory,
since ruff's config resolution and pytest's `testpaths` both assume repo root).

```powershell
uv sync --all-packages --locked      # install the workspace
uv run ruff check .                  # lint
uv run ruff format --check .         # format check (--check omitted to auto-fix)
uv run mypy                          # strict type check, whole workspace
uv run pytest                        # all tests (backend/transform/shared/scripts)
uv run pytest --cov --cov-report=term-missing --cov-fail-under=80
uv run pytest backend/tests/test_scaffolding.py::test_name   # a single test
uv run pytest -m "req('FR-07')"      # tests tagged against a specific requirement
```

Frontend commands run from `frontend/` (or via the root `package.json` scripts, which
proxy to `pnpm --filter nptc-frontend`):

```powershell
pnpm install --frozen-lockfile
pnpm dev                 # vite dev server
pnpm lint                # eslint
pnpm format:check        # prettier --check
pnpm typecheck           # tsc -b --noEmit
pnpm test                # vitest run
pnpm test:watch          # vitest, watch mode
pnpm build               # tsc -b && vite build
```

Repo governance scripts (Python, at repo root, tested under `scripts/tests/`):

```powershell
uv run python scripts/backlog_sync.py            # dry-run: preview GitHub issue sync
uv run python scripts/backlog_sync.py --apply    # apply it
uv run python scripts/traceability_check.py      # regenerate docs/requirements/traceability.md
```

Before pushing (also run by `pre-commit run --all-files`, and mirrored by CI):

```powershell
pre-commit run --all-files
```

## Testing conventions

- `@pytest.mark.req("FR-07")` links a test to the PRD requirement it verifies (defined
  in `pyproject.toml`'s `markers`). Add this to at least one test per requirement you
  implement, and move that requirement to `implemented` in
  `docs/requirements/requirements.yaml` in the same PR.
- Every requirement's test coverage must include **its principal failure mode**, not
  just the happy path — this is checked in review, not just CI.
- `transform/tests` and `shared/tests` must pass with **no network access** (NFR-37,
  enforced in CI via `iptables` egress blocking). Mock/stub the terminology client
  (FR-53 interface) rather than hitting a live Ontoserver in tests.
- Coverage floor is 80% (`--cov-fail-under=80`), enforced in CI.
- Test trees (`backend/tests`, `transform/tests`, `shared/tests`, `scripts/tests`) have
  no `__init__.py` — pytest runs with `--import-mode=importlib`, and more than one tree
  is allowed to reuse a basename like `test_scaffolding.py` without colliding.

## Hard constraints (from CONTRIBUTING.md — will be pushed back on in review)

- SNOMED CT identifiers must be a string end-to-end — never stored, passed, or
  serialised as a number (FR-06). This is the exact defect class the platform exists to
  eliminate; SCTIDs can exceed safe integer range and lose leading-zero/precision
  semantics if coerced.
- Authorisation is checked against a **permission**, never a role name (FR-44), and the
  **negative** case (access correctly denied) needs its own test, not just the positive
  path.
- Every state-changing write path emits an audit event (NFR-08).
- `switch`/`match` on property datatype only inside the `registry/` handler module
  (FR-77) — not scattered across storage, export, or search code.
- No secrets, tokens, or personal information in code, logs, or fixtures (NFR-26, NFR-35).

## Documentation is part of the change

A PR's body must state one of: docs updated in this PR, `no-doc-impact: <reason>`, or a
linked follow-up issue and why it can't be done now — CI enforces this isn't silent. See
CONTRIBUTING.md's table for which `docs/` path each kind of change touches (API/schema →
`docs/api/openapi.json` + `docs/architecture/`; DB schema →
`docs/operations/upgrade.md` + `docs/architecture/data-model.md`; config/env var →
`deploy/.env.example` + `docs/operations/configuration.md`; UI behaviour →
`docs/user/`; a rejected-alternative decision → a new ADR).

## Backlog and issues

`docs/backlog/*.yaml` (one file per delivery phase: `foundation.yaml`, `p0.yaml`,
`p1.yaml`, ...) is the source of truth for issue content and checklists — **not** the
GitHub issue body, which `backlog_sync.py` generates and will overwrite. Tick checklist
boxes in the YAML, not on GitHub.

## Git / PR workflow

- Every change starts from an issue and lands via PR — no direct pushes to `main`.
- Branch naming: `<type>/<issue-number>-<short-slug>` (e.g. `feat/42-property-registry`).
- Conventional Commits for the PR title (individual commits are squashed).
- Claude's role stops at opening the PR and waiting for CI to go green — it does not
  self-review or merge; the maintainer (single-committer project, no branch protection
  yet — see `docs/operations/repo-configuration.md`) reviews and merges.

## Windows-specific conventions (this repo is edited on Windows)

- PowerShell scripts (`*.ps1`, `*.psm1`) are the one exception to the repo's LF-everywhere
  rule: they're CRLF by convention (`.gitattributes`, `.editorconfig`) and must stay
  ASCII-only / PowerShell 5.1-compatible — no em-dashes, smart quotes, or other
  non-ASCII characters.
- Everything else in the repo is LF, enforced by `.gitattributes` (`* text=auto eol=lf`)
  and pre-commit's `mixed-line-ending --fix=lf`.
