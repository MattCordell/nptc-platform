# Contributing

Thanks for working on the NPTC platform. This document is short on purpose — if a rule
is not here, use your judgement and raise it in the PR.

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
uv run pytest            # from backend/ or transform/
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
   marker instead — see ADR-0002 and the `requirements.yaml` header.

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
| The database schema | `docs/operations/upgrade.md`, `docs/architecture/data-model.md` |
| Configuration or an env var | `deploy/.env.example` **and** `docs/operations/configuration.md` |
| Jobs, backups, sweeps, exports | `docs/operations/runbooks/` |
| Anything a Reviewer or Admin does in the UI | `docs/user/` |
| A design decision with a rejected alternative | A new ADR in `docs/adr/` |
| A clinical safety consideration | `docs/governance/hazard-log.md` |
| Setup, quickstart or prerequisites | `README.md`, `CONTRIBUTING.md` |

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
restriction) needs GitHub Team or Enterprise Cloud for a private repository owned by an
organisation, and this org is on the Free plan. Apply it — and raise required approvals
from `0` to `1` with `CODEOWNERS` enforcement — the day a second developer joins; that
also requires the org to be on a plan that supports it by then, or the repo to be public.
The exact command is recorded in
[docs/operations/repo-configuration.md](docs/operations/repo-configuration.md), along
with why required approvals stay at `0` until then: GitHub does not allow self-approval,
so any non-zero requirement would make a single-committer repository unmergeable without
an admin bypass, and a bypass that's always used is not a control.

## Things that will be pushed back on

- SNOMED CT identifiers stored, passed or serialised as anything other than a string
  (FR-06). This is the defect class the platform exists to eliminate.
- Authorisation checked against a role name instead of a permission (FR-44).
- A write path that does not emit an audit event (NFR-08).
- `switch` on property datatype outside the handler module (FR-77).
- Business logic in database triggers or functions (PRD §14.1).
- A test that requires network access to a terminology server (NFR-37).
- Secrets, tokens or personal information in code, logs or fixtures (NFR-26, NFR-35).

## Getting help

Open a discussion on the issue. For anything security-sensitive, see
[SECURITY.md](SECURITY.md) instead.
