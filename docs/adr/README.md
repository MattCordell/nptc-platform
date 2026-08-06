# Architecture decision records

MADR-lite: enough structure to be useful, not enough to be a chore. Each ADR records a
decision, the alternatives considered, and the consequences — so the reasoning survives
staff turnover and a decision is superseded explicitly rather than silently relitigated.

## When to write one

When a design decision has a rejected alternative worth remembering — see the
"documentation impact" table in [CONTRIBUTING.md](../../CONTRIBUTING.md). Not every
decision needs one; a decision the PRD already argues in full only needs an ADR if this
codebase adds something the PRD does not already settle (see ADR-0001, which mostly
records and cross-references the PRD's own argument rather than repeating it).

## Format

```markdown
# ADR-NNNN: Title

**Status:** Proposed | Accepted | Superseded by ADR-MMMM
**Date:** YYYY-MM-DD

## Context
## Decision
## Rejected alternatives
## Consequences
```

Number sequentially. Never delete or renumber a past ADR.

**Reversing the decision itself** — choosing a different technology, rule, or approach —
gets a new ADR that supersedes the old one; update the superseded ADR's `Status` line, but
leave its `Context`/`Decision`/`Consequences` as the historical record of what was decided
and why at the time.

**Correcting a stated fact within a decision that has not changed** — e.g. a version
number in a table that was wrong or has since been narrowed, a broken link, a typo — may be
edited in place, with a dated `## Amendments` section appended (one line per correction:
what was stated, what it's now, why) so the correction itself is visible in `git blame`
without a whole new ADR for something that isn't a new decision. See ADR-0001's Amendments
section for the shape.

If it's unclear which of the two a change is, it's a reversal: supersede.

## Index

| ADR | Title |
|---|---|
| [0001](0001-technology-stack.md) | Technology stack |
| [0002](0002-requirement-evidence-without-a-test.md) | Requirement evidence without a pytest test |
| [0003](0003-terminology-client-in-shared.md) | Terminology client in shared/, with httpx as its first runtime dependency |
| [0004](0004-informational-band-and-code-level-band-assignment.md) | A fourth, non-blocking `INFORMATIONAL` band; band assigned from the finding code, never from content |
| [0005](0005-sweep-chunk-size-and-concurrency-defaults.md) | Batch sweep defaults — chunk size 300, delta concurrency 4, first pass sequential |
