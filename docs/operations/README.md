# Operations

Written for an operator who did not build the system — this is what makes handover from
the development team to RCPA-QAP (or whoever the eventual operator is, per open issue
OI-7) possible rather than theoretical (PRD NFR-36).

[`repo-configuration.md`](repo-configuration.md) records the exact commands that configure
labels, milestones and the branch protection ruleset.

[`runbooks/`](runbooks/README.md) holds operational procedures for jobs, validation sweeps,
exports and releases - starting with [`runbooks/transform.md`](runbooks/transform.md) for the
P0 seeding transform CLI.

The rest will hold, as the corresponding work lands:

| File | Populated by |
|---|---|
| `configuration.md` | Every environment variable the stack reads, kept in step with `deploy/.env.example` |
| `upgrade.md` | Database migration and version-upgrade notes |
| `backup-restore.md` | The backup procedure and the record of it actually being exercised (NFR-34) |

Populated incrementally — see the documentation-impact table in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
