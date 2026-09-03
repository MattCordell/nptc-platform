# Repository configuration

The exact commands that configure this repository outside of files tracked in
`main` — labels, milestones and the branch protection ruleset. Recorded here so
the configuration is reproducible (on a fork, or after a mistaken change),
rather than living only in one person's shell history (Foundation issue F-7).

## Labels, milestones and the Priority field

Created and maintained by hand, directly on GitHub — via the UI, or `gh label
create` / `gh api graphql` for one-off scripting. There is no reconciliation
script and none of these are declared in a tracked file (`scripts/backlog_sync.py` and
its `LABEL_TAXONOMY` were retired in issue #101 — GitHub Issues, Milestones and the
Projects v2 board are the source of truth for themselves, including for labels).

### Current label inventory

```text
a11y                      Accessibility
api                       Backend HTTP API
audit                     Audit logging
auth                      Authentication and identity
breaking-change           Changes docs/api/openapi.json in a way a consumer could break on
breaking-change-approved  The breaking API change on this PR is intended and reviewed
bug                       Something isn't working                    (GitHub default)
ci                        GitHub Actions workflows
db                        Database schema and migrations
dependencies              Dependency version update (Dependabot)
docs                      Documentation and its tooling
duplicate                 This issue or pull request already exists  (GitHub default)
export                    Release exports (CSV, FHIR, SPIA spreadsheet)
feature                   This PR implements backlog work - see pr-hygiene.yml
frontend                  React/TypeScript client
good first issue          Good for newcomers                         (GitHub default)
governance/open-issue     One of the PRD's numbered open issues (OI-n)
help wanted               Extra attention is needed                  (GitHub default)
infra                     CI, deployment, containers, tooling
invalid                   This doesn't seem right                    (GitHub default)
question                  Further information is requested           (GitHub default)
registry                  Property registry
security                  Security-relevant, cross-cutting across Bug/Task/Feature
spike                     Time-boxed investigation, not a committed implementation
status/blocked            Blocked on something outside this issue
status/needs-decision     Needs a decision before work can proceed
status/needs-rcpa-input   Needs RCPA-QAP editorial input
terminology               SNOMED CT / Ontoserver integration
transform                 The P0 seeding transform CLI
wontfix                   This will not be worked on                 (GitHub default)
```

`dependencies` is applied by all four ecosystems in `.github/dependabot.yml`.
`breaking-change` and `breaking-change-approved` are applied by
`.github/workflows/openapi.yml`'s `breaking` job (issue #206, FR-20); see
`docs/architecture/public-api.md`'s "Compatibility and breaking changes" section for
what triggers them. Created by hand:

```powershell
gh label create "breaking-change" --repo MattCordell/nptc-platform --color "b60205" --description "Changes docs/api/openapi.json in a way a consumer could break on"
gh label create "breaking-change-approved" --repo MattCordell/nptc-platform --color "0e8a16" --description "The breaking API change on this PR is intended and reviewed"
```

**Two GitHub default labels are kept on the repo but deliberately unused**, so their
presence in `gh label list` is not drift: `documentation` (the project labels docs work
`docs` instead) and `enhancement` (the project labels feature work `feature` instead, per
`pr-hygiene.yml`). They are default labels created automatically on repo creation, not
hand-added duplicates, and are left alone rather than deleted for no operational reason.

### Retired labels

`priority/must`, `priority/should` and `priority/may` were superseded by the Projects v2
Priority field (issue #71) and applied to zero open issues. Deleted by hand, 2026-08-05,
via the Foundation Phase Retro (issue #89):

```powershell
gh label delete priority/must   --repo MattCordell/nptc-platform --yes
gh label delete priority/should --repo MattCordell/nptc-platform --yes
gh label delete priority/may    --repo MattCordell/nptc-platform --yes
```

`phase/*`, `area/*` and `type/*` label definitions (retired by issue #69, native GitHub
Issue Types adopted instead) were checked at the same time and are already absent from the
repo — no cleanup needed there.

## Secret scanning and push protection

Enabled by hand, 2026-08-06 (issue #108), once the repository went public and both
became free without GitHub Advanced Security. Not file-based config — recorded here for
the same reason the branch protection ruleset below is, so a fork or a mistaken change
has a reproducible command to fall back on rather than only this record existing in one
PR's history.

```powershell
gh api -X PATCH repos/MattCordell/nptc-platform -f 'security_and_analysis[secret_scanning][status]=enabled' -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
```

### Verifying secret scanning took effect

```powershell
gh api repos/MattCordell/nptc-platform --jq '.security_and_analysis'
```

Settings alone don't prove push protection actually blocks anything — it was verified
for real in #108 by pushing a disposable scratch branch containing fake Slack and
Stripe API keys and confirming the push was rejected (`GH013: Repository rule
violations found ... Push cannot contain secrets`). See `SECURITY.md` for the coverage
caveat found during that test: fake AWS-access-key- and GitHub-PAT-shaped secrets did
not trigger a block in the same session, so `security.yml`'s gitleaks scan stays in
place as a complementary check rather than being superseded by this.

## Branch protection ruleset

**Deferred, not yet applied.** Not needed yet: this is a single-committer
project. It's also currently blocked outright regardless of that decision -
branch protection (both the modern rulesets API below and classic branch
protection) is a paid-plan feature for a private repository. Attempting the
command below returns the actual, authoritative error (not a paraphrase of
which plan tier is required):

```text
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.", ...}
```

Apply it the day a second developer joins - that's the trigger, since that's
when peer review actually matters - which also requires the account owning
this repo to be on a plan that supports it by then (or the repo to have gone
public; it stays
private until TSWG clears visibility, see `README.md`). Until then, `main`
has no branch protection: direct pushes, force pushes and merges without
green CI are all technically possible, and this is a real gap to be aware
of, not a theoretical one. Run the command below (and tick the F-7/F-4
issue checklists it corresponds to, on the issues themselves) once that
day comes.

To be applied once, by hand, via `gh api` (rulesets aren't file-based config,
so there's nothing to own here beyond this record of the command). The
ruleset body is committed as
[`main-ruleset.json`](main-ruleset.json) in this same directory, rather than
inlined as a heredoc, for two reasons: it makes the ruleset itself diffable,
and `<<'EOF'` is POSIX-shell syntax and a parse error in PowerShell 5.1
(which has here-strings, `@'...'@`/`@"..."@`, but not that syntax).

Run this from the repo root, so the relative `--input` path resolves:

```powershell
gh api --method POST repos/aehrc/nptc-platform/rulesets --input docs/operations/main-ruleset.json
```

This requires a pull request into `main` (no direct pushes), blocks force
pushes, requires a linear history, requires conversation resolution, and
requires four status checks: `ci.yml`, `docs.yml` and `security.yml` each
expose a single `Required (...)` gate job rather than their individual
lint/test jobs directly, and `pr-hygiene.yml`'s `hygiene` job.

**Why a gate job per workflow, not the individual jobs:** `ci.yml`,
`docs.yml` and `security.yml` all skip most of their real work on PRs that
don't touch the relevant paths (a docs-only PR doesn't need the Python/Node
test matrix; a code-only PR doesn't need the documentation checks). If the
individual job names were required directly, a PR of the type that skips one
would show that check as "Expected — waiting for status to be reported"
forever, since GitHub does not treat "this workflow never triggered for this
PR" as a passing required check — and with `required_approving_review_count`
at `0` and no bypass actors, there would be no way to force such a PR through.
Each workflow's `required` job always runs (`if: always()`), depends on every
real job in that workflow, and passes as long as none of them actually
failed — treating "skipped because the path filter didn't match" the same as
"passed", and only that job's name goes in the ruleset.

**Why zero required approvals:** GitHub does not allow a PR author to approve
their own PR, so any non-zero requirement makes a single-committer repository
unmergeable without an admin bypass — and a bypass that's always used is not a
control. The PR review that happens today is the maintainer's own review plus
the Definition of Done checklist in `CONTRIBUTING.md` (see "Code review"
there — reviews are manual, including for PRs Claude prepares). Raise this to
`1` and add a `CODEOWNERS`-backed review requirement the day a second
developer joins.

### Verifying it took effect

```powershell
gh api repos/aehrc/nptc-platform/rulesets
git push origin main   # rejected: direct pushes are blocked
```

## `frontend-audit` retries on registry timeouts (issue #255)

`pnpm audit`'s POST to `registry.npmjs.org/-/npm/v1/security/advisories/bulk`
intermittently timed out under pnpm's defaults, failing the
`pnpm audit (production dependencies)` job - and with it the
`Required (Security)` aggregator - on PRs that touched no dependency manifest
at all. Two layers now guard against that:

- The repo-root `.npmrc` raises `fetch-timeout` and `fetch-retries` above
  pnpm's defaults for every `pnpm` invocation (install and audit alike, in CI
  and locally).
- `frontend-audit`'s audit step in
  [`security.yml`](../../.github/workflows/security.yml) wraps `pnpm audit`
  in a retry loop that inspects the failure output: a registry-timeout
  signature (`TimeoutError`, `operation was aborted`, an `ERR_PNPM_*_FETCH`
  code) retries once more after a short sleep; anything else - including a
  real high/critical advisory - fails immediately, on the first attempt, with
  no retry. The job's `timeout-minutes` is sized to the worst-case retry wall
  time (see the comment above that job in `security.yml` for the budget).
