# Operations

Written for an operator who did not build the system — this is what makes handover from
the development team to RCPA-QAP (or whoever the eventual operator is, per open issue
OI-7) possible rather than theoretical (PRD NFR-36).

[`repo-configuration.md`](repo-configuration.md) records the exact commands that configure
labels, milestones and the branch protection ruleset.

[`configuration.md`](configuration.md) documents every environment variable the stack
reads, kept in step with `deploy/.env.example`, including its
[Keycloak realm import](configuration.md#keycloak-realm-import) section (issue #40,
[ADR-0014](../adr/0014-keycloak-realm-as-code.md)).

[`runbooks/`](runbooks/README.md) holds operational procedures for jobs, validation sweeps,
exports and releases - starting with [`runbooks/transform.md`](runbooks/transform.md) for the
P0 seeding transform CLI.

[`upgrade.md`](upgrade.md) documents running Alembic migrations, the two database DSNs,
out-of-band app-role login provisioning, and the deliberate downgrade/role asymmetry
(issue #33).

The rest will hold, as the corresponding work lands:

| File | Populated by |
|---|---|
| `backup-restore.md` | The backup procedure and the record of it actually being exercised (NFR-34) |

Populated incrementally — see the documentation-impact table in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
