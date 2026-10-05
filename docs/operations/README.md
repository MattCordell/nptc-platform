# Operations

Written for an operator who did not build the system — this is what makes handover from
the development team to RCPA-QAP (or whoever the eventual operator is, per open issue
OI-7) possible rather than theoretical (PRD NFR-36).

[`deployment.md`](deployment.md) is the guide to build, run and stop the compose stack,
from a clean checkout to a signed-in browser (NFR-41). [`local-development.md`](local-development.md)
covers running the API and web app on your own machine against that stack's database and
sign-in service.

[`backend-test-container-split.md`](backend-test-container-split.md) records which backend
tests need a Postgres container, with counts per file and timings (issue #363).

[`code-size-remeasurement.md`](code-size-remeasurement.md) compares prose density and
function length with the milestone-one review, and records which long functions to split
(issue #366).

[`repo-configuration.md`](repo-configuration.md) records the exact commands that configure
labels, milestones and the branch protection ruleset.

[`configuration.md`](configuration.md) documents every environment variable the stack
reads, kept in step with `deploy/.env.example`, including its
[Keycloak realm import](configuration.md#keycloak-realm-import) section (issue #40,
[ADR-0014](../adr/0014-keycloak-realm-as-code.md)).

[`runbooks/`](runbooks/README.md) holds operational procedures for jobs, validation sweeps,
exports and releases - starting with [`runbooks/transform.md`](runbooks/transform.md) for the
P0 seeding transform CLI and [`runbooks/seed-baseline.md`](runbooks/seed-baseline.md) for the
one-off step that loads its dataset into an empty catalogue.
[`runbooks/load-baseline.md`](runbooks/load-baseline.md) is the end-to-end guide for loading
the real workbook, from the first report to the checks afterwards.

[`upgrade.md`](upgrade.md) documents running Alembic migrations (which the compose
`migrate` service does for you), the two database DSNs, app-role login provisioning, and
the deliberate downgrade/role asymmetry (issue #33).

The rest will hold, as the corresponding work lands:

| File | Populated by |
|---|---|
| `backup-restore.md` | The backup procedure and the record of it actually being exercised (NFR-34) |

Populated incrementally — see the documentation-impact table in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
