# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The NPTC Catalogue Maintenance Platform: a web platform replacing a hand-edited Excel
workbook for maintaining the National Pathology Test Catalogue (the SPIA Requesting
terminology, curated by RCPA-QAP, published by NCTS as a SNOMED CT reference set and
FHIR ValueSet).

**Status: P1 (core catalogue) in progress.** The P0 seeding transform
(`transform/src/nptc_transform/`) is complete, and the backend implements a large part of
P1. Most packages for later phases are still stubs: an `__init__.py` whose docstring
names the PRD phase that lands it. `docs/requirements/requirements.yaml` records each
requirement's status (`implemented`, `in-progress`, `planned` or `deferred`). There is no
application image yet, so the platform does not yet run as a single-command stack
(NFR-41). Don't assume an entity, endpoint, or table described in the PRD already
exists — check the actual module before writing code that depends on it.

The authority for all behaviour is `docs/prd/NPTC-Catalogue-Platform-PRD.md`. Every
requirement is cited as `FR-nn` (functional) or `NFR-nn` (non-functional) and those IDs
are stable — use them in commits, tests, and code comments instead of restating the
requirement. `docs/adr/` records technology decisions and the alternatives rejected;
read one before relitigating a stack choice.

## Repository layout

This is a polyglot monorepo: a `uv` workspace for Python, a `pnpm` workspace for the
frontend, one shared root git repo.

The `shared/` package (`nptc_shared`) is imported by BOTH `backend/` and `transform/`
(SCTID/Verhoeff validation, terminology client contract) so there is never a second,
divergent implementation (ADR-0001, FR-74). `scripts/` is repo governance tooling, not
part of the app runtime. Each `backend/src/nptc/*` package's `__init__.py` docstring
states what that package is responsible for, including the stub packages not built yet.

## Technology stack (ADR-0001)

| Layer | Choice |
|---|---|
| Database | PostgreSQL 16+ (`pg_trgm`, `unaccent`) — one datastore, no Elasticsearch/vector store |
| Identity | Keycloak (OIDC auth code flow + PKCE) |
| Background jobs | Postgres-backed queue, not Celery/Redis |
| Packaging | Docker Compose (single-command stack is NFR-41, lands with issue F-7) |

Languages, frameworks and versions live in the package manifests; ADR-0001 records why
each was chosen and what was rejected.

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
uv run pytest --req=FR-07            # tests tagged against a specific requirement (conftest.py; -m has no call syntax)
```

Fast iteration vs. full sweep: over half of `backend/tests` is marked
`@pytest.mark.integration` (testcontainers Postgres or Keycloak), and those tests take
most of the wall time. No test in `transform/tests`, `shared/tests` or `scripts/tests`
carries the marker. Counts as of 2026-09-29, as collected items (each parametrised case
counts separately):

| Tree | Collected | `integration` | Unmarked |
|---|---:|---:|---:|
| `backend/tests` | 1,850 | 1,035 | 815 |
| `transform/tests`, `shared/tests`, `scripts/tests` | 832 | 0 | 832 |
| Whole suite | 2,682 | 1,035 | 1,647 |

To re-measure, run `uv run pytest --collect-only -q -m integration` (or
`-m "not integration"`, optionally with a tree path); the last line gives the count.

So `-m "not integration"` runs about three fifths of the whole suite but under half of
`backend/tests`. For backend work it is a quick check, not a stand-in for the container
tests. It also still needs a running Docker daemon: 65 unmarked backend tests request the
Postgres container through the `app_db` fixture, until #369 derives the marker from
fixture use.

Run the fast subset while iterating, and the full suite (optionally parallelised via
`pytest-xdist`) once before pushing:

```powershell
uv run pytest -m "not integration"                       # fast subset; still starts Postgres until #369
uv run pytest -m integration -n auto --dist loadscope     # container tests, parallel
uv run pytest -n auto --dist loadscope                    # full sweep before pushing
```

`--dist loadscope` is required, not optional, whenever `-n` is used against
`backend/tests`: `postgres_container`/`owner_engine`/`app_engine` are session-scoped per
*worker process*, so grouping by module is what keeps each module's tests on the one
container `-n` gives that worker, rather than xdist spreading a module's tests (and their
shared container assumptions) across workers. This is local-dev tooling only: CI's own
`pytest` invocations stay serial, because one container per worker is a heavier ask of a
CI runner than of a dev machine. The two CI jobs that run `backend/tests` each took about
8 minutes on 2026-09-29, against a 15-minute timeout (`.github/workflows/ci.yml`).

Repo governance scripts (Python, at repo root, tested under `scripts/tests/`):

```powershell
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
- A test must never assert an absolute count/state on a table another test could
  plausibly have written to first (issue #190) — `backend/tests` shares one
  session-scoped Postgres container across every test in the run. Assert a relative
  delta against a baseline established in the test (see
  `test_catalogue_business_key.py`'s `advance_sequence_past` tests), scope the query to
  rows this test itself created, or — only when neither is possible, e.g. a check that is
  genuinely whole-table by definition — request an explicit isolation fixture
  (`pristine_audit_event` in `backend/tests/conftest.py`) rather than relying on
  incidental file-execution order.

## Hard constraints (from CONTRIBUTING.md — will be pushed back on in review)

- SNOMED CT identifiers must be a string end-to-end — never stored, passed, or
  serialised as a number (FR-06). This is the exact defect class the platform exists to
  eliminate; SCTIDs can exceed safe integer range and lose leading-zero/precision
  semantics if coerced.
- Authorisation is checked against a **permission**, never a role name (FR-44), and the
  **negative** case (access correctly denied) needs its own test, not just the positive
  path.
- Every state-changing write path emits an audit event (NFR-08).
- `switch`/`match` on property datatype only inside the `registry/datatypes/` handler
  package (FR-77, ADR-0013) — not scattered across storage, export, or search code, and
  not elsewhere in `registry/` either. Enforced by `backend/tests/test_datatype_dispatch.py`.
- No secrets, tokens, or personal information in code, logs, or fixtures (NFR-26, NFR-35).
- A comment or docstring states only what is not obvious from the code: it never
  restates the code, argues with a past reviewer, or cites an issue number. Review
  feedback is resolved in the code or an ADR, never in a comment. See CONTRIBUTING.md's
  "Code comments" section.

## Documentation is part of the change

A PR's body must state one of: docs updated in this PR, `no-doc-impact: <reason>`, or a
linked follow-up issue and why it can't be done now — CI enforces this isn't silent. See
CONTRIBUTING.md's table for which `docs/` path each kind of change touches (API/schema →
`docs/api/openapi.json` + `docs/architecture/`; DB schema →
`docs/operations/upgrade.md` + `docs/architecture/data-model.md`; config/env var →
`deploy/.env.example` + `docs/operations/configuration.md`; UI behaviour →
`docs/user/`; a rejected-alternative decision → a new ADR). New ADRs are paused until
the P1 milestone closes, except for a decision that genuinely rejects an alternative that
might be revisited; other decisions' reasoning goes in the PR description (see
CONTRIBUTING.md).

## Backlog and issues

GitHub Issues is the source of truth for backlog content and checklists — there is no
YAML file behind it and no sync script. Tick checklist boxes directly on the issue.
Labels, milestones and the Projects v2 Priority field are also managed by hand on
GitHub (see `docs/operations/repo-configuration.md`), not generated from a file.

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
