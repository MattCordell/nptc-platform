# Repository configuration

The exact commands that configure this repository outside of files tracked in
`main` — labels, milestones and the branch protection ruleset. Recorded here so
the configuration is reproducible (on a fork, or after a mistaken change),
rather than living only in one person's shell history (Foundation issue F-7).

## Labels and milestones

Not created by hand. `scripts/backlog_sync.py` owns the label taxonomy
(`LABEL_TAXONOMY`) and the milestone list (`KNOWN_MILESTONES`) and reconciles
both — creating anything missing, never deleting — every time it runs:

```powershell
$env:GH_TOKEN = gh auth token
uv run python scripts/backlog_sync.py --apply
```

Re-running it is always safe: it diffs against the live repository state and
only acts on what's missing or out of date (see the script's module
docstring). If you need to add a label or milestone, add it to the script's
taxonomy and re-run — not `gh label create` by hand — or the next sync will
not know about it and won't keep it in step with `docs/backlog/*.yaml`.

## Branch protection ruleset

**Currently blocked, not yet applied.** Branch protection (both the modern
rulesets API below and classic branch protection) requires GitHub Team or
Enterprise Cloud for a private repository owned by an organisation — the
`aehrc` org is on the Free plan. Attempting the command below returns:

```text
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.", ...}
```

This repo stays private until TSWG clears visibility (see `README.md`), so
the fix is an org billing decision (upgrade `aehrc` to Team/Enterprise), not
a configuration change. Until one of those happens, `main` has no branch
protection: direct pushes, force pushes and merges without green CI are all
technically possible, and this is a real gap to be aware of, not a
theoretical one. Re-run the command below (and flip the F-7/F-4 backlog
checklist boxes it corresponds to, via `backlog_sync.py --apply`) the moment
either condition changes.

Applied once, by hand, via `gh api` (rulesets aren't file-based config, so
there's nothing for `backlog_sync.py` or any other script to own here).

```powershell
gh api --method POST repos/aehrc/nptc-platform/rulesets --input - <<'EOF'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true
      }
    },
    { "type": "required_linear_history" },
    { "type": "non_fast_forward" },
    {
      "type": "required_status_checks",
      "parameters": {
        "required_status_checks": [
          { "context": "Required (CI)" },
          { "context": "Required (Docs)" },
          { "context": "Required (Security)" },
          { "context": "hygiene" }
        ],
        "strict_required_status_checks_policy": false
      }
    }
  ],
  "bypass_actors": []
}
EOF
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
control. The PR review that happens today is a Claude review comment plus the
Definition of Done checklist in `CONTRIBUTING.md`. Raise this to `1` and add a
`CODEOWNERS`-backed review requirement the day a second developer joins.

### Verifying it took effect

```powershell
gh api repos/aehrc/nptc-platform/rulesets
git push origin main   # rejected: direct pushes are blocked
```
