# ADR-0041: The FR-86 maximum preferred-term length is an environment variable, not an admin-editable setting

**Status:** Accepted
**Date:** 2026-09-15

## Context

FR-86 asks for a configurable maximum preferred-term length; FR-87 (the distribution
report) exists to give RCPA-QAP the data to choose one. Neither requirement says *how* the
value is configured — only that a maximum can be set, defaults to unset, and never blocks a
save when exceeded.

Two shapes were available: an environment variable read by `nptc.settings.ApiSettings`
(this codebase's existing configuration mechanism), or a value an Administrator sets
through the API/UI and the platform stores in the database.

## Decision

`NPTC_MAX_PREFERRED_TERM_LENGTH`, on `ApiSettings`, exactly like every other optional
runtime setting this codebase has (`fsn_semantic_tag`, `indexer_database_url`,
`NPTC_TX_CHUNK_SIZE`). Changing it means redeploying with a new value, not an in-app action.

An admin-editable, database-backed setting was considered and rejected:

- **There is no settings table or admin-config UI anywhere in this codebase to extend.**
  Every configuration value the platform reads today is `pydantic-settings`-backed
  (`nptc.settings`) and set at deploy time (`docs/operations/configuration.md`). Adding the
  first database-backed, API-editable setting would be new infrastructure this issue does
  not otherwise need: a table, a migration, a read/write route pair, a permission, and the
  audit-event shape a state-changing write requires (NFR-08) — all to store one integer
  RCPA-QAP is expected to change rarely, if ever, once chosen.
- **FR-86 does not ask for self-service.** The requirement is that a maximum can be
  *configured*, not that RCPA-QAP or an Administrator can change it from a screen without
  operator involvement. The distribution report (FR-87) is explicitly the tool for
  *deciding* the value; nothing in either requirement implies the value then needs to be
  *set* without a deployment step.
- **A wrong value is cheap to fix either way**, since FR-86 never blocks a save — an
  operator correcting a badly-chosen maximum only affects which entries show a warning, not
  which entries can be edited.

## Consequences

- Setting or changing the maximum requires an operator with deploy access, not just an
  Administrator role — a heavier process than a database-backed setting would need, but one
  this codebase already accepts for every comparable value (`NPTC_TX_CHUNK_SIZE`'s own
  tuning procedure in `docs/operations/configuration.md` is the closest precedent).
- No new table, migration, permission, or audit-event shape was needed for this issue.
- If a future requirement asks for genuine self-service (RCPA-QAP changing the value
  themselves, without operator involvement), that is new scope this ADR does not cover, and
  would need its own decision about where such a setting lives.
