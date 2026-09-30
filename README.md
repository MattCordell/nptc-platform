# NPTC Catalogue Maintenance Platform

[![CI](https://github.com/aehrc/nptc-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/aehrc/nptc-platform/actions/workflows/ci.yml)
[![PR hygiene](https://github.com/aehrc/nptc-platform/actions/workflows/pr-hygiene.yml/badge.svg)](https://github.com/aehrc/nptc-platform/actions/workflows/pr-hygiene.yml)
[![Licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)

A web platform for maintaining the **National Pathology Test Catalogue** — the SPIA
Requesting terminology curated by RCPA-QAP and published downstream by the National
Clinical Terminology Service (NCTS) as a SNOMED CT reference set and FHIR ValueSet.

It replaces a hand-edited Excel workbook with a machine-readable catalogue that:

- makes the database the authoritative source and the spreadsheet a generated export,
  eliminating the identifier-corruption and invisible-character defect classes that
  spreadsheet editing introduces;
- opens the test submission pipeline to the pathology community under moderated
  governance, so implementers can see what is proposed and why;
- continuously validates every terminology binding against the live SNOMED CT-AU and
  International editions, so bindings cannot silently rot between publications;
- records every editorial decision in an append-only, tamper-evident audit log.

**Status: pre-alpha.** No application functionality is implemented yet. See the
[GitHub issues](https://github.com/aehrc/nptc-platform/issues) and
[milestones](https://github.com/aehrc/nptc-platform/milestones) for what is planned and
[docs/requirements/](docs/requirements/) for the requirement register and traceability
report tracking what's implemented.

---

## Quickstart

The whole stack comes up with one command and no manual post-installation steps
(NFR-41). It is a development-grade stack: plain HTTP, and not for production.

**Prerequisites:** Docker, with Compose (any recent version supporting the Compose
Specification). You do not need Python or Node. For the full dev toolchain (Node, pnpm, uv,
Python), see [CONTRIBUTING.md](CONTRIBUTING.md)'s Prerequisites table.

**Bring the stack up:**

```powershell
Copy-Item deploy/.env.example deploy/.env
docker compose -f deploy/compose.yml up -d --build
```

This builds two images and starts, on a clean volume:

- **PostgreSQL** (pinned to UTF-8; the version is in `deploy/compose.yml`) on `${POSTGRES_PORT:-5432}`
- **Keycloak** on `${KEYCLOAK_PORT:-8080}`
- **migrate**, a one-shot job that applies the database migrations and then exits
- **backend**, the API, after the migrations succeed
- **web**, which serves the web app and forwards `/api` to the API, on `${NPTC_WEB_PORT:-8081}`

Open <http://localhost:8081> when `docker compose -f deploy/compose.yml ps -a` shows the
services healthy. [docs/operations/deployment.md](docs/operations/deployment.md) covers
creating the first user and administrator, stopping the stack and troubleshooting. See
[docs/operations/configuration.md](docs/operations/configuration.md) for what each
`deploy/.env` variable does, and
[docs/operations/local-development.md](docs/operations/local-development.md) to run the API
or web app on your own machine.

## Documentation map

| Path | Contents |
|---|---|
| [docs/prd/](docs/prd/) | The Product Requirements Document. The authority for every `FR-nn` and `NFR-nn` reference in this repository. |
| [docs/requirements/](docs/requirements/) | Requirement register and the generated traceability report: which requirements exist, their status, and the tests covering them. |
| [GitHub Issues](https://github.com/aehrc/nptc-platform/issues) | The backlog. Source of truth for what's planned and its checklists — no YAML behind it. |
| [docs/adr/](docs/adr/) | Architecture decision records. Why things are the way they are, including the alternatives rejected. |
| [docs/architecture/](docs/architecture/) | Data model, component structure, and the contracts between them. |
| [docs/operations/](docs/operations/) | Deployment, configuration, upgrade, backup and restore, and runbooks. Written for an operator who did not build the system. |
| [docs/user/](docs/user/) | Role guides for Members, Reviewers and Administrators. |
| [docs/governance/](docs/governance/) | Clinical safety hazard log, privacy position, and the versioned terms of use. |

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. In short: work
from an issue, keep tests green before you push, update the issue checklist and the
documentation in the same PR, and expect a review.

Security issues go to [SECURITY.md](SECURITY.md), never to a public issue.

## Governance and provenance

Built by the [Australian e-Health Research Centre](https://aehrc.csiro.au/) (AEHRC),
CSIRO, for the NPTC Technical and Standards Working Group (TSWG), which reports to the
NPTC Steering Committee. The catalogue content itself is owned and curated by RCPA-QAP.

The question of who hosts and operates the platform if the proof of concept succeeds is
open and tracked as governance issue OI-7 (PRD §15.1; see the corresponding GitHub
issue).

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

**This licence covers the software only.** SNOMED CT and the SPIA Requesting
terminology are separately licensed by SNOMED International / NCTS and RCPA-QAP
respectively, and no SNOMED CT or SPIA content is contained in this repository.
