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

Status is stated honestly per row: `Active` means the control runs today and would
actually catch the thing it claims to; `Planned` names the phase or issue it lands
with. A stub module existing is not the same as a control being active.

| Control | Where | Requirement | Status |
|---|---|---|---|
| Dependency vulnerability scanning, build fails on high/critical in production dependencies | `.github/workflows/security.yml` | NFR-25 | Active |
| Static analysis (CodeQL) on every pull request, daily, on push to main, and on demand, failing the build on a high/critical (security-severity >= 7.0) finding | `.github/workflows/codeql.yml` | — | Active |
| Secret scanning of git history on every PR and daily (gitleaks CLI, run directly rather than via the paid organisation-tier GitHub Action) | `.github/workflows/security.yml` | NFR-26 | Active |
| GitHub native secret scanning and push protection (blocks a push containing a detected secret before it lands) | repo Settings > Code security | NFR-26 | Active |
| Supply-chain/security-posture scoring (OpenSSF Scorecard) daily, on push to main, and on demand | `.github/workflows/scorecard.yml` | — | Active |
| Dependency updates | `.github/dependabot.yml` | NFR-25 | Active |
| No secrets in the repository or in image layers; configuration by environment variable with a values-free `.env.example` | `deploy/.env.example` | NFR-26 | Active |
| Container image scanning before publication | — | NFR-25 | Planned (P4/P5, lands with the container images) |
| Server-side authorisation on every request, tested for the negative case | `backend/src/nptc/auth/` | NFR-20, FR-80, FR-81 | Planned (P1/P2 — currently a docstring stub, enforces nothing) |
| Append-only audit log enforced at database privilege level, with a hash chain | `backend/src/nptc/audit/` | NFR-09, NFR-10 | Implemented (issue #36, ADR-0017): `nptc.audit.writer.append_audit_event` is the sole write path, and `nptc.audit.verification.verify_chain` detects an out-of-band `UPDATE`/`DELETE`/re-order. One real emit site (`close_account`); the operator CLI wrapping `verify_chain` (`scripts/verify_audit_chain.py`) landed with issue #38 |

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

**CodeQL now uploads to the Security tab (2026-08).** The repository went public, which
makes GitHub code scanning free without needing GitHub Advanced Security (the prior
private-repo upload failure — "Advanced Security must be enabled for this repository to
use code scanning" — no longer applies). `codeql.yml` runs on every pull request, daily,
on push to main, and on demand, and results (including PR annotations) go to the
Security tab as well as the job's own step summary and a downloadable SARIF artifact.
Because a finding with no consequence is a finding nobody looks at, the workflow also
fails outright on any high/critical (security-severity >= 7.0) result, independent of
the Security tab, so a PR or the daily scheduled run fails loudly rather than relying on
someone checking the tab. Lower-severity findings stay advisory-only —
`security-extended` on a pre-alpha codebase produces enough of those that gating on all
of them would just teach reviewers to ignore a red run.

**Native secret scanning and push protection are enabled (2026-08).** Neither was
ever blocked by Advanced Security or repository visibility the way CodeQL's upload
was above — they just hadn't been turned on. Verified for real, not just by reading
the settings page: pushing a branch containing fake Slack and Stripe API keys was
rejected outright with `GH013: Repository rule violations found ... Push cannot
contain secrets`. Two synthetic AWS-access-key- and GitHub-PAT-shaped values did
*not* trigger a block or an alert in the same testing session - GitHub's push
protection only covers a specific, high-confidence subset of its supported patterns
(some require a provider-validated checksum a random test string won't satisfy),
so a real credential belonging to one of the less-reliably-detected formats could
still land undetected. `security.yml`'s gitleaks CLI scan stays in place as a
second, complementary check against the same class of mistake, since its pattern
set doesn't fully overlap with GitHub's.

**OpenSSF Scorecard is active (2026-08, issue #108).** `scorecard.yml` runs daily,
on push to main, and on demand, publishing to the public Scorecard API/badge and
uploading results to the Security tab alongside CodeQL's.
