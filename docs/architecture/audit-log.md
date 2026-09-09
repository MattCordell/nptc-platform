# The audit log query surface (NFR-12)

The administrator search, filter and export API over `audit_event`, landed with issue #286.
The write side - the table itself, the hash chain, field-level diffing - is documented in
[data-model.md](data-model.md) and [ADR-0017](../adr/0017-audit-hash-chain.md)/
[ADR-0018](../adr/0018-field-level-audit-diffing.md). This document describes the read side
only: what an administrator can query, and why it is read-only end to end.

Design decisions behind the export format, the redaction posture and the two orderings are
recorded in [ADR-0039](../adr/0039-audit-log-read-and-export-surface.md). This document
describes what the API *does*.

## Endpoints

Both paths are under `/api/v1`, both are `GET`, and both require `Permission.AUDIT_READ` -
Administrator-only, and inside `MFA_REQUIRED_PERMISSIONS` (NFR-06), so a caller who has not
completed the step-up gets the same RFC 9470 challenge every other Administrator-only write
route already gives (see [permissions.md](permissions.md)).

| Path | Query parameters | Response |
|---|---|---|
| `/audit/events` | `actor_user_id`, `entity_type`, `entity_id`, `action`, `occurred_from`, `occurred_to`, `limit` (1-200, default 50), `before` | `{items: [AuditEventItem], next_cursor}` |
| `/audit/events/export` | `actor_user_id`, `entity_type`, `entity_id`, `action`, `occurred_from`, `occurred_to` | `application/x-ndjson`, one JSON object per line |

Every filter is optional and AND-ed together - an unfiltered call pages (or exports) the
whole log. `entity_id` is only accepted alongside `entity_type` (422 otherwise): the two
together are what `ix_audit_event_entity_type_entity_id_sequence` indexes, and `entity_id`
alone is not unique across entity types. `occurred_from`/`occurred_to` are a half-open range
`[from, to)`; `occurred_from` after `occurred_to` is a 422, never a silently empty page.

## Read route: `sequence`-keyset paging, most recent first

`GET /audit/events` follows [ADR-0024](../adr/0024-catalogue-search-and-pagination.md)'s own
envelope (`{items, next_cursor}`), ordered `sequence` descending. `sequence` is a globally
monotonic identity column, never `occurred_at` - two events in the same transaction can share
a `clock_timestamp()`-derived `occurred_at` closely enough that ordering by it alone leaves a
tie-break undefined, the same reason `nptc.catalogue.history.load_history` gives for its own
identical choice. This is also what keeps ordering stable across pages under a concurrent
append (issue #190): a new row gets a higher `sequence` and cannot land inside a page already
served.

`next_cursor` is `null` exactly on the last page, decided by fetching one row more than asked
for rather than a `COUNT(*)`. A cursor beyond `AuditEvent.sequence`'s own `BigInteger` range
is a 422 (`MalformedAuditCursorError`), not silently reinterpreted.

## Export: the whole filtered set, oldest first, with the hash chain attached

`GET /audit/events/export` streams NDJSON - one JSON object per line, each carrying
`entry_hash`/`prev_hash` alongside the event fields, so an exported extract can be
independently re-verified against the chain (NFR-10) without a second round trip to this
API. Ordered oldest first - the opposite of the read route - matching
`nptc.audit.verification.verify_chain`'s own walk direction, and unpaged: an export is the
whole filtered set, not a page of it, which is practical at this platform's real catalogue
size (see ADR-0039's own note on that trade).

The response streams via SQLAlchemy's `yield_per` execution option
(`nptc.audit.queries.stream_audit_events`), matching `verify_chain`'s own precedent, so a
large filtered set is not loaded into memory wholesale before the first byte is sent.
Filter validation happens *before* the response starts streaming, not lazily inside the
generator - a `StreamingResponse` commits its `200` and headers as soon as its body is first
read, so a validation error raised any later could no longer become a clean `422`. See
`stream_audit_events`'s own docstring for the eager/lazy split this relies on.

## What is served, and why it is safe here

`before`/`after` are served exactly as `audit_event` stores them - the raw JSONB, not the
field-names-only projection `nptc.catalogue.history` serves on its own public FR-19 surface.
That is a deliberate difference in posture, not an oversight: `nptc.audit.diffing` already
redacted the payload at write time, under `REDACTED_KEY` (`_redacted`) - a withheld field
still appears here, but only by name in a list, never by value - and this route's audience
(Administrator, MFA-verified) is exactly who that redaction was always meant to eventually
let through. Serving the field-names-only projection here as well would simply withhold
information NFR-12 exists to surface, for no gain in safety.

Attribution resolves an event's actor by internal id, not a display name alone:

```json
"actor": {"id": "…", "display_name": "…", "is_closed": false}
```

A closed (tombstoned) account has `username`/`display_name`/`organisation` set to `NULL` -
`User`'s own `tombstone` `CHECK` constraint (NFR-17) - so a name-only projection would go
blank for exactly the accounts an auditor most needs to trace. The internal UUID is the
stable handle that survives closure, safe to serve here because this surface is
Administrator-plus-MFA rather than public. `is_closed` distinguishes a closed account's blank
name from a system-initiated event, where `actor` is `null` entirely (`actor_user_id IS
NULL` - no human actor to resolve at all).

## What this surface cannot do

Read-only, end to end. `nptc.audit.queries` issues no `INSERT`/`UPDATE`/`DELETE` against
`audit_event` - proven structurally by `test_audit_write_path_guard.py`'s repo-wide AST
sweep (nothing outside `nptc.audit.writer` may construct an `AuditEvent` row or emit a raw
`INSERT` against the table) and behaviourally by `test_db_audit_privileges.py` (the
`nptc_app_login` role remains refused `UPDATE`/`DELETE`/`TRUNCATE` after exercising this
surface). Reading or exporting the log emits no audit event of its own - NFR-08 scopes audit
events to state-changing operations, and this whole surface is `SELECT`-only (see
ADR-0039's "No `audit.exported` event").

Keycloak's own authentication event store (login, failure, lockout, MFA enrolment - NFR-11)
is not reachable through this surface. It is tracked separately as
[#310](https://github.com/MattCordell/nptc-platform/issues/310) - see ADR-0039 for why the
two stores are not conflated into one query surface.
