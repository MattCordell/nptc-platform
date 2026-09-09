# ADR-0039: Audit log read and export surface

**Status:** Accepted
**Date:** 2026-09-10

## Context

The write side of the audit log already exists in full: the `audit_event` table (#33,
ADR-0011), the hash chain (#36, ADR-0017), field-level diffs (#37, ADR-0018) and route
coverage (#165). `Permission.AUDIT_READ` was defined and Administrator-only from the start
(`nptc.auth.permissions`), but no route consumed it. PRD §17 (NFR-12) puts the reason
plainly - "an audit log nobody can query is a compliance artefact rather than a working
tool." Issue #38 cited NFR-12 in its requirement IDs but delivered chain-integrity
verification only (`scripts/verify_audit_chain.py`); this issue (#286) is `AUDIT_READ`'s
first consumer.

Four decisions needed settling before any code: the export file format, whether an export
itself is an auditable event, whether to also proxy Keycloak's own authentication event
store (NFR-11's other half), and whether the raw `before`/`after` diff is safe to serve here
at all, given `nptc.catalogue.history`'s public FR-19 surface deliberately never does.

## Decision

### Export format: NDJSON, not CSV

One JSON object per line (`GET /api/v1/audit/events/export`, `application/x-ndjson`). NDJSON
round-trips the `before`/`after` JSONB - and the `_redacted` marker inside it - without
inventing a flattening rule, and carries `entry_hash`/`prev_hash` on every line so an
exported line can be cross-referenced against the stored row (NFR-10), without a second
file format for those two fields. CSV was considered and rejected: `before`/`after` are
JSONB objects of varying shape (see `nptc.audit.diffing`'s own per-model column policy), and
a CSV cell holding embedded, re-escaped JSON is exactly the ambiguous, hand-rolled shape
NDJSON avoids.

**`entry_hash`/`prev_hash` are for cross-reference, not standalone recomputation** (PR #309
review). `nptc.audit.hashing.digest_field_names` covers every `audit_event` column except
`entry_hash`/`sequence`, which includes `id`, `correlation_id`, `actor_ip` and `user_agent` -
none of which this export payload carries, deliberately: putting an actor's IP address and
user agent into a downloadable file is a real NFR-26/NFR-35 decision this issue does not
need to make, not an oversight. A reader with database access can confirm an exported line
still matches the stored row (the same check `scripts/verify_audit_chain.py` performs), but
cannot recompute `entry_hash` from the exported line alone. A *filtered* export compounds
this: its rows are not contiguous in the real chain, so `prev_hash` linkage cannot be walked
across the extract itself either way, filtered or not - only each row individually confirmed
against its stored counterpart. `backend/tests/test_api_audit_export.py::test_export_entry_
hash_cannot_be_recomputed_from_the_exported_line_alone` pins the omitted fields so this claim
and the payload cannot silently drift apart again.

**A mid-stream failure is not signalled.** `stream_audit_events` streams via `yield_per`, and
`StreamingResponse` has already sent its `200` and headers by the time the first row is
written - if the query or the connection fails partway through, the client is left holding a
truncated file with nothing in the HTTP response to say so (PR #309 review). Not addressed
here: NDJSON has no natural trailer to carry a completion marker, and this platform's real
scale (below) makes the failure window small. Worth revisiting if export size grows enough
to make a partial failure a real operational concern.

### No `audit.exported` event

Reading and exporting the log emit no audit event of their own. NFR-08 scopes audit events
to state-changing operations, and this whole issue is `SELECT`-only - `nptc.audit.queries`
issues no `INSERT`/`UPDATE`/`DELETE` against `audit_event`, proven structurally by
`test_audit_write_path_guard.py`'s repo-wide AST sweep and behaviourally by
`test_db_audit_privileges.py` (the `nptc_app_login` role remains refused a write after a
read through the new surface). An export is a large disclosure and a real operational event
worth noticing, but recording it would need its own decision about who reviews an export
log and how - deferred rather than defaulted into existence here.

### NFR-11 (Keycloak's own authentication event store) is out of scope

Keycloak's event store (login, failure, lockout, MFA enrolment - ADR-0014, issue #40) is not
proxied through this route. It is a structurally different store: no hash chain, no
`before`/`after` diff, and a fixed `eventsExpiration` window rather than indefinite
retention - conflating the two into one query surface would either force `audit_event`'s own
guarantees onto data that does not carry them, or silently drop those guarantees from the
combined response with no way for a caller to tell which kind of row they were looking at.
Tracked separately as #310, which records the two candidate approaches (proxy through this
platform's API, or document Keycloak's own admin console/API as a separate operator path).

### The raw diff is served here, unlike `nptc.catalogue.history`'s public surface

`nptc.catalogue.history.load_history` (FR-19) deliberately projects field *names* only,
never values - that module's own docstring calls this "redacted by construction, not by
filtering," since the endpoint is reachable by an anonymous caller. NFR-12's administrator
surface is different on the one axis that matters: `Permission.AUDIT_READ` is
Administrator-only and inside `MFA_REQUIRED_PERMISSIONS` (NFR-06), so the RFC 9470 step-up
challenge applies here with no extra code, and the caller is exactly the audience the
`before`/`after` JSONB was always intended to be read by eventually. Serving it raw is safe
precisely because `nptc.audit.diffing` already redacted it at write time: a field withheld
under `REDACTED_KEY` (`_redacted`) still appears here only by name, in a list, never by
value - this module adds no second redaction step and removes none either
(`test_before_after_are_served_exactly_as_stored`).

### Attribution resolves by internal id, not a display name alone

A closed (tombstoned) account has `username`/`display_name`/`organisation` set to `NULL`
(`User`'s own `tombstone` CHECK constraint, NFR-17). A name-only projection - `history.py`'s
own choice, correct for its public, anonymous-reachable audience - would go blank for
exactly the accounts an auditor most needs to trace. `AuditActor` instead serves
`{id, display_name, is_closed}`: the internal UUID as the stable handle (safe here, on an
Administrator-plus-MFA surface, in a way it is not on a public one), plus `is_closed` so a
closed account's blank name is distinguishable from a system-initiated event's absent
`actor` entirely (`actor_user_id IS NULL`).

### Keyset pagination on `sequence`, most recent first for the read route; oldest first, unpaged, for the export

The read route (`GET /api/v1/audit/events`) follows ADR-0024's own envelope
(`{items, next_cursor}`), ordered `sequence DESC`, `next_cursor` null exactly on the last
page - `occurred_at` is a filter only, never the sort key, matching `history.py`'s own
documented reason (`clock_timestamp()` ties within a transaction). This is also what keeps
ordering stable under a concurrent append (issue #190): a new row gets a higher `sequence`
and cannot land inside an already-served page.

The export (`nptc.audit.queries.stream_audit_events`) walks the identical filtered set
oldest first, with no `limit`/cursor at all - the whole filtered set, not a page of it. Two
reasons converge: an export exists to stay verifiable against the hash chain, and ascending
`sequence` is the same direction `nptc.audit.verification.verify_chain` itself walks the
chain in; and this platform's real catalogue size (~2,000 terms, ~5,000 ceiling - see
CLAUDE.md's own sizing note) makes an unpaged, `yield_per`-streamed export practical without
a row cap. If a much larger deployment ever makes an unbounded export a real concern, the
documented alternative is a row cap that returns `422` rather than a silently truncated
file - not attempted here because there is no present need to trade legibility for it.

### Filters as `Annotated[..., Query(...)]` aliases, not a Pydantic query model or `filter.*` syntax

Matching the house style already used throughout `nptc.api.routers.*` for a fixed, named
filter set. ADR-0032's `filter.<property_key>` dynamic syntax does not apply here - that
exists because the catalogue's filterable properties are not fixed at compile time; the
audit log's four filters (actor, entity, action, date range) are. `entity_id` is accepted
only alongside `entity_type` (422 otherwise, `AuditFilterError`) so every entity-scoped
query can use `ix_audit_event_entity_type_entity_id_sequence` rather than an
`entity_id`-alone scan with no supporting index.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| CSV export | `before`/`after` are JSONB of varying, per-model shape - a CSV cell holding re-escaped embedded JSON is the exact ambiguity NDJSON avoids by design. |
| An `audit.exported` audit event on every export | This issue is `SELECT`-only by design (NFR-09); recording an export needs its own decision about review and retention, not a default. |
| Proxying Keycloak's own event store through this route | A structurally different store (no hash chain, no diff, fixed expiration) - conflating it with `audit_event` would misrepresent one store's guarantees as the other's. Tracked separately as #310. |
| Field-names-only projection, matching `history.py` | Would defeat NFR-12's own purpose - an administrator needs the actual before/after values to investigate a change, not just that one occurred. Safe to serve raw here specifically because the audience is Administrator-plus-MFA, not anonymous. |
| A name-only actor projection | Blank for exactly the closed accounts an auditor most needs to trace (NFR-13, NFR-17) - the internal id is the only handle that survives closure. |
| A `filter.*` dynamic query surface (ADR-0032's own shape) | Built for a property set that varies at runtime; the audit log's four filters are fixed, so a plain named-parameter set is simpler and needs no facet-derivation machinery. |
| A row-capped export from day one | This catalogue's real size (~2,000 terms) makes an unpaged export practical now; adding a cap pre-emptively would trade legibility for a constraint not yet needed. |

## Consequences

- `nptc.audit.queries` becomes the one read model both the interactive read route and the
  export route share (`_predicates`/`validate_audit_filters`), so the four filters cannot
  drift between the two surfaces.
- An administrator can trace a closed account's actions by internal id even though the
  account's own name is gone - the same UUID a database-level investigation would already
  use, now reachable from the API.
- NFR-11's application half remains open, tracked as #310, with two candidate approaches
  recorded there rather than decided by default here.
- The `/admin/audit` screen this route will eventually feed is a separate follow-up issue,
  mirroring how #282 (API) and #285 (UI) were split for an earlier feature - this issue
  ships the API only.
