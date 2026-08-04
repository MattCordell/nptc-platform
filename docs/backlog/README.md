# Backlog

This directory, not the GitHub issue body, is the source of truth for what each issue
covers and which of its checklist boxes are ticked. See CONTRIBUTING.md's "Issue
checklists" section. `scripts/backlog_sync.py` renders these files into GitHub issues,
matched either by an explicit `github_issue:` number or by a hidden
`<!-- nptc-backlog-id: ID -->` marker it writes into every issue it creates - running
it again produces no further changes and no duplicates.

One YAML file per phase: `foundation.yaml`, `p0.yaml`, `p1.yaml` so far, with
`p2.yaml` … `p5.yaml` and `governance.yaml` to follow as each phase kicks off (see
`docs/prd/NPTC-Catalogue-Platform-PRD.md` §17.1 for phase acceptance criteria). The
full item schema, including the checklist done-marker convention and the `docs:`
field's `none: <reason>` form, is documented in the header comment of
[foundation.yaml](foundation.yaml).

A handful of items carry `children:` - the genuinely multi-week work (audit log,
identity, catalogue entity, property registry, admin editing UI) - which
`backlog_sync.py` links as GitHub native sub-issues rather than flattening into one
long checklist.

To preview what a real sync would change, without making any changes:

```powershell
uv run python scripts/backlog_sync.py
```

Pass `--apply` to execute it. CI (`docs.yml`) runs the dry-run form on every PR
touching this directory or `scripts/backlog_sync.py`, so a malformed backlog or an
unreachable `github_issue:` fails review rather than the later import.
