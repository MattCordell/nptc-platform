# Operations

Written for an operator who did not build the system — this is what makes handover from
the development team to RCPA-QAP (or whoever the eventual operator is, per open issue
OI-7) possible rather than theoretical (PRD NFR-36).

Will hold, as the corresponding work lands:

| File | Populated by |
|---|---|
| `configuration.md` | Every environment variable the stack reads, kept in step with `deploy/.env.example` |
| `upgrade.md` | Database migration and version-upgrade notes |
| `backup-restore.md` | The backup procedure and the record of it actually being exercised (NFR-34) |
| `repo-configuration.md` | The exact `gh` commands that configure the branch ruleset, labels and milestones — Foundation issue F-7 |
| `runbooks/` | Operational procedures for jobs, validation sweeps, exports and releases |

Populated incrementally — see the documentation-impact table in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
