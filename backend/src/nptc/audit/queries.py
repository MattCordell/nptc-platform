"""NFR-12's administrator read model: search/filter `audit_event` and page
through the result (issue #286). `nptc.api.routers.audit` is the one HTTP
consumer, for both its read and export routes.

**Serves the raw `before`/`after`, unlike `nptc.catalogue.history`.** That
module deliberately projects field *names* only, for FR-19's public
surface. This module's caller is always an Administrator who has completed
step-up MFA (`Permission.AUDIT_READ` is in `MFA_REQUIRED_PERMISSIONS`), and
the stored JSONB is already safe to serve verbatim - `nptc.audit.diffing`
redacted it at write time, under `REDACTED_KEY`, so a withheld field still
appears here only by name, never by value. Nothing in this module reads
inside `before`/`after` to decide that; it is a property of what was
written, not of what is read.

**Attribution serves `{id, display_name, is_closed}`, not a bare name.** A
closed account is a tombstone (`nptc.db.models.user.User`'s own `tombstone`
CHECK constraint): `username`/`display_name`/`organisation` are `NULL`, so a
name-only projection would be blank for exactly the accounts an auditor
most needs to trace (NFR-13, NFR-17). The internal UUID is the stable
handle, safe to serve here because this route is Administrator-plus-MFA,
unlike `nptc.catalogue.history`'s anonymous-reachable surface.
`is_closed` lets a caller tell "closed account, blank name" apart from
"system event, no actor at all" (`actor` is `None` for the latter).

Ordered by `sequence`, never `occurred_at` - matching `nptc.catalogue.
history.load_history`'s own precedent and for the identical reason: two
events in the same transaction can share a `clock_timestamp()`-derived
`occurred_at` closely enough that ordering by it alone leaves a tie-break
undefined. `sequence` is a globally monotonic identity column, so paging
on it (most recent first) can never drop or repeat a row across a page
boundary - which is also what keeps ordering stable across pages under
concurrent appends (issue #190): a new append gets a higher `sequence` and
cannot land inside an already-served page.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from nptc.audit.serialisation import JsonValue
from nptc.db.models.audit import AuditEvent
from nptc.db.models.user import User, UserStatus

__all__ = [
    "ActorInfo",
    "AuditEventFilter",
    "AuditEventPage",
    "AuditEventRow",
    "AuditFilterError",
    "MalformedAuditCursorError",
    "search_audit_events",
    "stream_audit_events",
    "validate_audit_filters",
]

#: The largest value `AuditEvent.sequence` (`BigInteger`, signed 64-bit) can
#: hold - matching `nptc.catalogue.history._BIGINT_MAX` exactly, and for the
#: identical reason: `sequence` cannot hold a larger value to begin with, so
#: a `before` cursor claiming one is refused here rather than passed to the
#: query, where it would either overflow the driver's own bigint bind
#: parameter or, worse, silently compare true against every row.
_BIGINT_MAX: Final = 2**63 - 1


class MalformedAuditCursorError(ValueError):
    """Raised for a `before` cursor exceeding `_BIGINT_MAX`.

    `nptc.api.routers.audit.AuditCursorQuery`'s own `pattern`/`max_length`
    already reject anything that is not a short digit string; this catches
    the remainder - a well-formed but out-of-range digit string - matching
    `nptc.catalogue.history.MalformedHistoryCursorError`'s own "refused,
    never silently reinterpreted" precedent.
    """

    http_status: ClassVar[int] = 422


class AuditFilterError(ValueError):
    """Raised for a filter combination that cannot produce a well-defined
    result set: `entity_id` given without `entity_type`, or an
    `occurred_from` after `occurred_to`. Refused rather than silently
    returning an empty page, which would look like a genuine "no matching
    events" answer rather than a mistake in the request.
    """

    http_status: ClassVar[int] = 422


@dataclass(frozen=True, slots=True)
class AuditEventFilter:
    """Every filter `search_audit_events` accepts, all optional and
    AND-ed together. `entity_id` is only meaningful paired with
    `entity_type` (see `AuditFilterError`) - the two together are what
    `ix_audit_event_entity_type_entity_id_sequence` indexes.

    `occurred_from`/`occurred_to` are a half-open range `[from, to)`,
    matching the common convention for a date-range filter: a caller
    filtering by calendar day passes that day's start as `occurred_from`
    and the next day's start as `occurred_to`, with no risk of a
    midnight-boundary event counted in neither day or both.
    """

    actor_user_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    action: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class ActorInfo:
    """The acting user, resolved by internal UUID (NFR-13, NFR-17) - see
    the module docstring for why a bare display name is not enough.
    `None` on `AuditEventRow.actor` (rather than an `ActorInfo` with every
    field `None`) is reserved for a system-initiated event, which has no
    actor row to resolve at all."""

    id: uuid.UUID
    display_name: str | None
    is_closed: bool


@dataclass(frozen=True, slots=True)
class AuditEventRow:
    """One `audit_event` row, projected for NFR-12 - the raw stored
    `before`/`after` (see the module docstring for why that is safe here),
    plus the hash-chain fields an export needs to stay independently
    verifiable (NFR-10)."""

    sequence: int
    occurred_at: datetime
    actor: ActorInfo | None
    action: str
    entity_type: str
    entity_id: str
    before: Mapping[str, JsonValue] | None
    after: Mapping[str, JsonValue] | None
    reason: str | None
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True, slots=True)
class AuditEventPage:
    """One keyset page of matching events, most recent first. `next_cursor`
    is `None` exactly when this is the last page - decided by the one
    extra row `search_audit_events` asked for, never by a `COUNT(*)`,
    matching `nptc.catalogue.history.HistoryPage`'s own precedent."""

    events: tuple[AuditEventRow, ...]
    next_cursor: str | None


def validate_audit_filters(filters: AuditEventFilter) -> None:
    """Raises `AuditFilterError` for a filter combination that cannot
    produce a well-defined result - see that error's own docstring.

    A free function, not folded into `AuditEventFilter.__post_init__`, so
    a caller building one filter at a time (the API layer's per-query-
    parameter shape) is not forced to construct in a specific order to
    avoid a transient invalid state. Called eagerly by both
    `search_audit_events` and, before it ever returns a lazy generator,
    `stream_audit_events` - see that function's own docstring for why
    eagerness matters there.
    """
    if filters.entity_id is not None and filters.entity_type is None:
        raise AuditFilterError("entity_id filter requires entity_type")
    if (
        filters.occurred_from is not None
        and filters.occurred_to is not None
        and filters.occurred_from > filters.occurred_to
    ):
        raise AuditFilterError("occurred_from must not be after occurred_to")


def _predicates(filters: AuditEventFilter) -> list[ColumnElement[bool]]:
    predicates: list[ColumnElement[bool]] = []
    if filters.actor_user_id is not None:
        predicates.append(AuditEvent.actor_user_id == filters.actor_user_id)
    if filters.entity_type is not None:
        predicates.append(AuditEvent.entity_type == filters.entity_type)
    if filters.entity_id is not None:
        predicates.append(AuditEvent.entity_id == filters.entity_id)
    if filters.action is not None:
        predicates.append(AuditEvent.action == filters.action)
    if filters.occurred_from is not None:
        predicates.append(AuditEvent.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        predicates.append(AuditEvent.occurred_at < filters.occurred_to)
    return predicates


def search_audit_events(
    session: Session,
    filters: AuditEventFilter,
    *,
    limit: int,
    before: int | None = None,
) -> AuditEventPage:
    """One keyset page of `audit_event`, filtered by `filters` and ordered
    `sequence` descending (most recent first).

    `before` is the `sequence` of the last event on the previous page
    (exclusive) - see `AuditEventPage.next_cursor`.

    Raises `MalformedAuditCursorError`/`AuditFilterError` before running
    any query - see each error's own docstring.
    """
    if before is not None and before > _BIGINT_MAX:
        raise MalformedAuditCursorError(f"audit cursor {before} exceeds bigint range")
    validate_audit_filters(filters)

    predicates = _predicates(filters)
    if before is not None:
        predicates.append(AuditEvent.sequence < before)

    statement = (
        select(
            AuditEvent.sequence,
            AuditEvent.occurred_at,
            AuditEvent.actor_user_id,
            User.display_name,
            User.status,
            AuditEvent.action,
            AuditEvent.entity_type,
            AuditEvent.entity_id,
            AuditEvent.before,
            AuditEvent.after,
            AuditEvent.reason,
            AuditEvent.prev_hash,
            AuditEvent.entry_hash,
        )
        .select_from(AuditEvent)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(*predicates)
        .order_by(AuditEvent.sequence.desc())
        # One more row than asked for: its existence *is* the answer to
        # "is there a next page" - matching `load_history`'s own precedent.
        .limit(limit + 1)
    )

    rows = session.execute(statement).all()
    page_rows = rows[:limit]
    events = tuple(
        AuditEventRow(
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            actor=ActorInfo(
                id=row.actor_user_id,
                display_name=row.display_name,
                is_closed=row.status == UserStatus.CLOSED,
            )
            if row.actor_user_id is not None
            else None,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            before=row.before,
            after=row.after,
            reason=row.reason,
            prev_hash=row.prev_hash,
            entry_hash=row.entry_hash,
        )
        for row in page_rows
    )
    next_cursor = str(page_rows[-1].sequence) if len(rows) > limit else None
    return AuditEventPage(events=events, next_cursor=next_cursor)


#: Rows fetched per round trip to the database for `stream_audit_events` -
#: matching `nptc.audit.verification.verify_chain`'s own `_DEFAULT_BATCH_SIZE`
#: exactly, for the identical reason: large enough to amortise the
#: round-trip cost, small enough that a very large table is still streamed
#: rather than loaded wholesale.
_DEFAULT_EXPORT_BATCH_SIZE: Final = 500


def stream_audit_events(
    session: Session,
    filters: AuditEventFilter,
    *,
    batch_size: int = _DEFAULT_EXPORT_BATCH_SIZE,
) -> Iterator[AuditEventRow]:
    """Every `audit_event` row matching `filters`, oldest first - the
    export's own read path (NFR-12). No `limit`/cursor: an export is the
    whole filtered set, not one page of it (see `docs/adr/0039-*.md` for
    why that is fine at this catalogue's real size).

    Oldest first, unlike `search_audit_events`'s most-recent-first keyset
    pages: an export exists to stay independently verifiable against the
    hash chain (NFR-10, `entry_hash`/`prev_hash` on every row), and
    ascending `sequence` is the same direction
    `nptc.audit.verification.verify_chain` itself walks the chain in.

    **Validates eagerly, not lazily.** This function's body is not itself
    a generator - it raises `AuditFilterError` immediately if `filters` is
    invalid and only then returns the lazy, `yield_per`-driven generator
    that does the actual streaming. A caller that raised the API's
    `StreamingResponse` before validating would already have sent a `200`
    and its headers by the time a generator-embedded validation ran,
    making a clean 422 impossible - see `nptc.api.routers.audit.
    export_audit_events`'s own call site.
    """
    validate_audit_filters(filters)
    return _stream_audit_events(session, filters, batch_size)


def _stream_audit_events(
    session: Session, filters: AuditEventFilter, batch_size: int
) -> Iterator[AuditEventRow]:
    statement = (
        select(
            AuditEvent.sequence,
            AuditEvent.occurred_at,
            AuditEvent.actor_user_id,
            User.display_name,
            User.status,
            AuditEvent.action,
            AuditEvent.entity_type,
            AuditEvent.entity_id,
            AuditEvent.before,
            AuditEvent.after,
            AuditEvent.reason,
            AuditEvent.prev_hash,
            AuditEvent.entry_hash,
        )
        .select_from(AuditEvent)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(*_predicates(filters))
        .order_by(AuditEvent.sequence.asc())
        .execution_options(yield_per=batch_size)
    )
    for row in session.execute(statement):
        yield AuditEventRow(
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            actor=ActorInfo(
                id=row.actor_user_id,
                display_name=row.display_name,
                is_closed=row.status == UserStatus.CLOSED,
            )
            if row.actor_user_id is not None
            else None,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            before=row.before,
            after=row.after,
            reason=row.reason,
            prev_hash=row.prev_hash,
            entry_hash=row.entry_hash,
        )
