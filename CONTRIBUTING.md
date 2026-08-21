# Contributing

Thanks for working on the NPTC platform. This document is short on purpose — if a rule
is not here, use your judgement and raise it in the PR.

## Prerequisites

| Tool | Version |
|---|---|
| Python | see [`.python-version`](.python-version) |
| Node.js | see [`.nvmrc`](.nvmrc) — bump alongside `frontend/package.json`'s `engines.node` floor, they must stay compatible |
| pnpm | `11.20.0` (the root `package.json`'s `packageManager` field) |
| `uv` | any recent version |
| Docker (with Compose) | any recent version supporting the Compose Specification - the daemon must be **running**, not just installed, since `backend/tests` runs against a real container (issue #33) |

See [README.md](README.md)'s Quickstart for bringing up the local Docker Compose stack.

## Ground rules

1. **Every change starts from an issue.** If one does not exist, open it first.
2. **Every change lands through a pull request.** No direct pushes to `main`.
3. **Tests pass before you push**, not after CI tells you.
4. **Documentation and the issue's checklist are updated in the same PR** as the code.

## The requirement identifiers

Everything traces back to the PRD in [docs/prd/](docs/prd/). Requirements are cited as
`FR-nn` and `NFR-nn` and those identifiers are stable — use them in issues, commit
messages, tests and code comments rather than restating the requirement.

Priority bands (`MUST` / `SHOULD` / `MAY`) set *scheduling* priority. RFC-2119 keywords
*inside* a requirement's text describe how it behaves once built. A SHOULD-band
requirement containing "MUST NOT" may be deferred, but that constraint is absolute if it
is built. See PRD §"Reading guide".

## Workflow

```text
issue  ->  branch  ->  local tests green  ->  PR  ->  review  ->  squash merge
```

**Branch naming:** `<type>/<issue-number>-<short-slug>`, e.g. `feat/42-property-registry`,
`fix/57-sctid-roundtrip`, `docs/12-runbook-exports`.

**Commits:** [Conventional Commits](https://www.conventionalcommits.org/). The PR *title*
must be one; individual commits are squashed, so they matter less.

```text
feat(registry): add datatype handler registry

Implements FR-77 so a new datatype is one handler module plus tests
rather than edits scattered across storage, export and search.
```

**Before you push:**

```powershell
pre-commit run --all-files
uv run pytest            # from the repo root (testpaths and ruff config resolve
                          # relative to the workspace root, not a package directory).
                          # Needs a *running* Docker daemon (not just installed) since
                          # issue #33: backend/tests runs against a real, containerized
                          # Postgres via testcontainers (NFR-39), not an in-memory
                          # substitute - see docs/operations/upgrade.md.
pnpm test                # from frontend/
```

## Definition of done

From PRD §17.2, applying to every requirement:

1. Automated tests cover the stated behaviour **and its principal failure mode**.
2. Every state-changing operation emits an audit event.
3. Authorisation is enforced server-side and tested for the **negative** case, not only
   the positive one.
4. Accessible: keyboard operable, correctly labelled, sensible focus order.
5. Errors tell the user what to do next — not a stack trace, not an HTTP status code.
6. Documented where the behaviour is not self-evident.

Plus, for this project:

7. **Documentation is updated in the same PR.** See below.
8. Requirement IDs appear on at least one test (`@pytest.mark.req("FR-07")`), and
   `docs/requirements/requirements.yaml` is moved to `implemented` when it is. For an
   infrastructure/process requirement with no plausible test, an `evidence:` path
   (pointing at the CI config or document that demonstrates it) may stand in for the test
   marker, or accompany one — see ADR-0002 and the `requirements.yaml` header.

## Documentation is part of the change

A change that lands without its documentation is incomplete. The platform's success
condition is that another organisation can adopt and operate it (NFR-36), and
documentation that lags the code is precisely how that fails.

Every issue declares its documentation impact in its "Documentation impact" field before
work starts. Your PR must then do one of three things, stated explicitly in the PR body:

- update the documentation in this PR; **or**
- state `no-doc-impact: <reason>` — a legitimate and common answer for internal
  refactors; **or**
- link a follow-up issue and say why it cannot be done here.

Silence is not an option, and CI enforces that.

| If you changed | Update |
|---|---|
| An API endpoint or schema | `docs/api/openapi.json` (regenerated), `docs/architecture/` |
| The database schema | `docs/operations/upgrade.md` (operational facts), `docs/architecture/data-model.md` (schema shape) — see below for who owns what |
| Configuration or an env var | `deploy/.env.example` **and** `docs/operations/configuration.md` |
| Jobs, backups, sweeps, exports | `docs/operations/runbooks/` |
| Anything a Reviewer or Admin does in the UI | `docs/user/` |
| A design decision with a rejected alternative | A new ADR in `docs/adr/` |
| A clinical safety consideration | `docs/governance/hazard-log.md` |
| Setup, quickstart or prerequisites | `README.md`, `CONTRIBUTING.md` |

### A schema change's prose has one home each

A DB schema change used to get its design rationale written up three times: the
migration's own docstring, the table's section in `data-model.md`, and a per-migration
note in `upgrade.md`. That isn't extra rigor, it's the same paragraph paid for three
times, and three copies of one rationale can silently drift apart from each other —
exactly the class of defect (a description and the thing it describes disagreeing) this
platform exists to eliminate elsewhere. Each fact gets exactly one home:

| The fact | Its home |
|---|---|
| *Why* it is built this way — the invariants enforced, the shape rejected, the trap avoided | The migration's own module docstring. It's reviewed alongside the DDL it explains, so it's the copy most likely to be revisited and kept honest. |
| The schema *shape* — columns, types, constraints, indexes — plus reasoning that is genuinely architectural (spans multiple tables or issues) | `docs/architecture/data-model.md` |
| *Operational* facts an operator must act on — "only succeeds against an empty table", "provisions a new role", a manual out-of-band step, a non-obvious downgrade order | `docs/operations/upgrade.md`, linking to the migration for the reasoning rather than re-summarising it |

Two rules follow: **link, don't restate** — a document referring to a fact that lives
elsewhere cites it instead of repeating it; and **a migration with no operator-facing
consequence gets a one-line row in `upgrade.md`'s migration index, not a section** (that's
why some revisions have no `upgrade.md` section at all — it isn't a gap, and it doesn't
make the doc-impact declaration above optional). See `0007_designation.py` and its
`upgrade.md`/`data-model.md` entries for a worked example.

## Issue checklists

The GitHub issue is the source of truth for what a unit of work covers and which of its
checklist boxes are ticked — there is no YAML file behind it. Tick the boxes on the issue
itself as the PR that does the work merges.

Discussion belongs in issue comments; the definition (acceptance criteria, checklist)
belongs in the issue body, edited in place as scope is clarified.

## Code review

Every PR is reviewed before merge, by the maintainer — including PRs prepared with
Claude's help: Claude opens the PR, waits for CI to go green, and then stops. It does not
self-review or merge. The maintainer reviews, relays any comments to action, and merges
once satisfied.

Reviews are **succinct and constructive**: findings ordered by severity, each naming the
file and the concrete problem. Elaborate positive feedback is not expected — "no blocking
issues" is a complete review when it is true.

Reviewers should check the negative-case tests and the documentation, not only the happy
path. Those are the two things that quietly go missing.

**Current merge protection: none, deferred.** `main` has no GitHub-enforced branch
protection today. It isn't needed yet for a single-committer project — direct pushes,
force pushes and merging without green CI are all technically possible, and the
discipline above is procedural, not platform-enforced, until that changes. It's also
currently blocked outright regardless: the ruleset (and classic branch protection, same
restriction) is a paid-plan feature for a private repository, and the account owning this
repo is on a plan that does not include it. Apply it — and raise required approvals from
`0` to `1` with `CODEOWNERS` enforcement — the day a second developer joins; that also
requires the account to be on a plan that supports it by then, or the repo to be public.
The exact command, and the actual error returned today, are recorded in
[docs/operations/repo-configuration.md](docs/operations/repo-configuration.md), along
with why required approvals stay at `0` until then: GitHub does not allow self-approval,
so any non-zero requirement would make a single-committer repository unmergeable without
an admin bypass, and a bypass that's always used is not a control.

## Things that will be pushed back on

- SNOMED CT identifiers stored, passed or serialised as anything other than a string
  (FR-06). This is the defect class the platform exists to eliminate.
- Authorisation checked against a role name instead of a permission (FR-44).
- A write path that does not emit an audit event (NFR-08).
- `switch`/`match` on property datatype outside `registry/datatypes/` (FR-77, ADR-0013).
- Business logic in database triggers or functions (PRD §14.1).
- A test that requires network access to a terminology server (NFR-37).
- Secrets, tokens or personal information in code, logs or fixtures (NFR-26, NFR-35).

## Getting help

Open a discussion on the issue. For anything security-sensitive, see
[SECURITY.md](SECURITY.md) instead.
