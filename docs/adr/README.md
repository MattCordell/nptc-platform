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

Number sequentially. Never delete or renumber a past ADR; supersede it with a new one
and update its `Status` line.

## Index

| ADR | Title |
|---|---|
| [0001](0001-technology-stack.md) | Technology stack |
