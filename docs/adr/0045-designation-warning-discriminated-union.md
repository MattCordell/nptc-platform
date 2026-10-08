# ADR-0045: Designation write warnings are one discriminated union

**Status:** Accepted
**Date:** 2026-10-08

## Context

A designation write can succeed and still have something an editor should look at. Two classes
exist today:

- **A collision** (FR-05): the term is a synonym on another live entry. Every write that adds
  or reactivates a synonym can return one. It names the other entry and can be acknowledged.
- **A length warning** (FR-86): the entry's own preferred term is over the configured maximum.
  Only the amend route's preferred-term branch can return one. It names no other entry and has
  nothing to acknowledge.

The collision rode back in `warnings` on the add, amend and reinstate responses. The length
warning was added later as a second field, `length_warning`, on the amend response alone,
because `CollisionWarning` had nowhere to carry a length. The editing screen forwarded only
`warnings`, so no editor saw a length warning. Further classes are plausible: FR-84
(subsumption) or another FR-05 severity. Each would repeat the pattern: a new response field,
a new route change and a new screen path.

## Decision

**Every write response carries one `warnings` list. Each item is a member of a union
discriminated by a required `kind` field.**

- `CollisionWarning` has `kind: "collision"`. `LengthWarning` has `kind: "length"`.
- `DesignationWarning` is the annotated union. The add, amend and reinstate responses all type
  `warnings` with it, though only amend can produce a length item today. A new class then
  reaches every route without a schema change per route.
- `length_warning` is removed from the amend response.
- The OpenAPI document carries the discriminator, so the generated client narrows on `kind`.
- The editing screen renders every item through one panel. A new class adds a union member and
  one `kind` case in the panel. The compiler flags every `switch` over `kind` that lacks it.
- An item that has no action, such as a length warning, offers none. An acknowledgement
  addresses a collision's term only, so retiring or acknowledging a term clears the collision
  about that term and leaves other kinds alone.

## Alternatives considered

All three were judged on the same four points: how a new class is added, whether the client can
tell the classes apart, how much the response grows per class, and what changes for the shipped
`length_warning` field.

| | Add a class | Tell classes apart | Response growth | Shipped field |
|---|---|---|---|---|
| **Discriminated union in `warnings` (chosen)** | One union member | `kind`, typed in OpenAPI | None | Removed |
| Parallel top-level field per class (the status quo) | New field on each response that can carry it | By field name | One field per class | Kept |
| One widened `Warning` model with optional fields | New optional fields | Inspect which fields are set | None | Removed |

The parallel field was the approach this ADR replaces. Each class costs a field on every
affected response, and a screen has to read each field separately, which is the omission that
left FR-86 unseen. The widened model keeps one list but gives up the type: every field is
optional, so the compiler cannot say which fields a given item has, and a collision item could
legally carry a length.

## Consequences

- **A client that read `length_warning` breaks.** The only client is the editing screen in this
  repository, and it never read the field. Nothing outside the repository is known to.
- **The collision and length shapes stay separate.** Neither model grows fields for the other.
- **Every consumer must handle an unknown `kind`.** Within this repository the compiler
  enforces it. An external client generated from `docs/api/openapi.json` should treat an
  unrecognised `kind` as a warning with no action.
- **Length warnings are not durable.** Like collision warnings, they describe the last write and
  clear on the next save. Nothing stores them.
- **Revisit if** a warning class needs data that does not fit a flat item, or a screen needs to
  show a class somewhere other than the shared panel.
