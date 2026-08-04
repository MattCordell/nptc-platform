# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report privately through GitHub's [private vulnerability
reporting](https://github.com/aehrc/nptc-platform/security/advisories/new), or by email
to **matt.cordell@csiro.au** with `[SECURITY]` in the subject line. See
[CODEOWNERS](.github/CODEOWNERS) if that contact ever goes stale.

Please include what you can: affected component and version or commit, reproduction
steps, and the impact you believe it has. A partial report received early is more useful
than a complete one received late.

**What to expect:** acknowledgement within five working days, an assessment and a
remediation plan within fifteen. We will tell you when a fix ships and credit you unless
you prefer otherwise. This is a research-team project, not a 24/7 operation, and the
timelines reflect that honestly.

## Scope

This repository is pre-alpha and has no production deployment. Reports about the
codebase, its dependencies, its container images and its deployment configuration are in
scope. Reports about `tx.ontoserver.csiro.au`, Keycloak upstream, or the NCTS publication
pipeline belong with those services, not here.

## Controls in this repository

| Control | Where | Requirement |
|---|---|---|
| Dependency vulnerability scanning, build fails on high/critical in production dependencies | `.github/workflows/security.yml` | NFR-25 |
| Container image scanning before publication | `.github/workflows/images.yml` | NFR-25 |
| Static analysis (CodeQL) on every PR and weekly | `.github/workflows/codeql.yml` | — |
| Secret scanning of git history on every PR and daily (gitleaks CLI, run directly rather than via the paid organisation-tier GitHub Action) | `.github/workflows/security.yml` | NFR-26 |
| Dependency updates | `.github/dependabot.yml` | NFR-25 |
| No secrets in the repository or in image layers; configuration by environment variable with a values-free `.env.example` | `deploy/.env.example` | NFR-26 |
| Server-side authorisation on every request, tested for the negative case | `backend/src/nptc/auth/` | NFR-20, FR-80, FR-81 |
| Append-only audit log enforced at database privilege level, with a hash chain | `backend/src/nptc/audit/` | NFR-09, NFR-10 |

An independent security review is required before any production deployment (NFR-27) and
is scheduled in phase P5.

## Known and accepted risks

Recorded here so that a reviewer finds them stated rather than discovers them.

**Dependency on `tx.ontoserver.csiro.au` (PRD OI-8, §15.2).** Terminology validation
depends on a reference Ontoserver instance that carries no availability commitment. This
was decided deliberately, not overlooked. Two mitigations are built in: the endpoint sits
behind an interface (FR-53), so repointing at an NCTS or self-hosted instance is
configuration rather than code; and an outage degrades validation gracefully (FR-54)
rather than blocking browsing, searching or editing.

**Personal information is collected.** Real name, organisation, username and email
(NFR-15). Account closure pseudonymises rather than deletes, so that the audit chain
remains verifiable (NFR-17). Whether that fully discharges APP 11.2 is under privacy
review and tracked as OI-15. The retention position is disclosed in the privacy policy
rather than left to be discovered.

**Public read access is intentional.** Approved catalogue content is served to
unauthenticated users by design (PRD §4.1) — a national standard behind a login would
defeat its purpose. Submissions, user identities and internal comments are not public.

**GitHub's native secret scanning, code scanning (CodeQL's Security tab integration)
and OpenSSF Scorecard aren't active yet.** All three need GitHub Advanced Security to
run against a private repository, and this one doesn't have it enabled — confirmed by
running each, not assumed from the docs (CodeQL's own upload step fails outright with
"Advanced Security must be enabled for this repository to use code scanning").
`codeql.yml` still runs the actual scan on every PR and weekly, with `upload: never` and
findings summarised into the job's own step summary instead of the Security tab.
`security.yml` runs the open-source gitleaks CLI directly against git history on every
PR and daily. Both are real controls, just not surfaced through GitHub's own UI. Revisit
all three once the repository goes public (see the licence decision in README.md) or Advanced
Security is enabled first.
