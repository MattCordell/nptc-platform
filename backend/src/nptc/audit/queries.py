"""NFR-12's administrator read model: search and filter `audit_event`, and page through the
result. `nptc.api.routers.audit` is the one HTTP consumer, for both its read and export
routes. Design and rejected alternatives: ADR-0039.

**Serves the raw `before`/`after`, unlike `nptc.catalogue.history`**, which projects field
names only for FR-19's public surface. The caller here is an Administrator who has completed
step-up MFA (`Permission.AUDIT_READ` is in `MFA_REQUIRED_PERMISSIONS`). The stored JSONB is
already safe to serve: `nptc.audit.diffing` redacted it at write time under `REDACTED_KEY`, so
a withheld field appears by name only.

**Attribution is `{id, display_name, is_closed}`, not a bare name.** A closed account is a
tombstone (`nptc.db.models.user.User`'s `tombstone` CHECK): its name fields are `NULL`, so a
name-only projection would be blank for the accounts an auditor most needs to trace (NFR-13,
NFR-17). The internal UUID is a stable handle, safe on this route and not on
`nptc.catalogue.history`'s anonymous-reachable one. `is_closed` separates a closed account
from a system event, where `actor` is `None`.

Ordered by `sequence`, never `occurred_at`: two events in one transaction can share a
`clock_timestamp()` value closely enough to leave a tie-break undefined. `sequence` is a
globally monotonic identity column, so keyset paging on it never drops or repeats a row across
a page boundary, even under concurrent appends: a new append gets a higher `sequence` and
cannot land inside a page already served.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final

from sqlalchemy import ColumnElement, Row, Select, select
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
    "EntityIdRequiresEntityTypeError",
    "MalformedAuditCursorError",
    "OccurredRangeInvalidError",
    "search_audit_events",
    "stream_audit_events",
    "validate_audit_filters",
]

#: The largest `AuditEvent.sequence` (a signed 64-bit `BigInteger`). A larger `before` cursor
#: is refused rather than passed to the query, where it would overflow the driver's bigint
#: bind parameter or compare true against every row. Same bound as `nptc.catalogue.history`.
_BIGINT_MAX: Final = 2**63 - 1


class MalformedAuditCursorError(ValueError):
    """Raised for a `before` cursor above `_BIGINT_MAX`.

    `nptc.api.routers.audit.AuditCursorQuery` already rejects anything that is not a short
    digit string. This catches a well-formed but out-of-range one: refused, never silently
    reinterpreted, as `nptc.catalogue.history.MalformedHistoryCursorError` does.
    """

    http_status: ClassVar[int] = 422


class AuditFilterError(ValueError):
    """Base class for a filter combination that can never match: `entity_id` without
    `entity_type` (`EntityIdRequiresEntityTypeError`), or `occurred_from` at or after
    `occurred_to` (`OccurredRangeInvalidError`). Refused, because an empty page would look like
    a genuine "no matching events" answer.

    Each subclass has its own fixed client-facing detail (`nptc.api.errors`), so no handler is
    tempted to serve `str(exc)` in a response body (NFR-26, NFR-35).
    """

    http_status: ClassVar[int] = 422


class EntityIdRequiresEntityTypeError(AuditFilterError):
    """`entity_id` was given without `entity_type`. `entity_id` alone is not unique across
    entity types and cannot use `ix_audit_event_entity_type_entity_id_sequence`."""


class OccurredRangeInvalidError(AuditFilterError):
    """`occurred_from` is not strictly before `occurred_to`. The range is half-open
    `[from, to)`, so a backwards range and a zero-width one both match nothing whatever the
    table holds, unlike a filter that matches nothing only because of the data."""


@dataclass(frozen=True, slots=True)
class AuditEventFilter:
    """Every filter `search_audit_events` accepts, all optional and AND-ed together.
    `entity_id` only makes sense with `entity_type` (see `AuditFilterError`).

    `occurred_from`/`occurred_to` are a half-open range `[from, to)`: a caller filtering by
    calendar day passes that day's start and the next day's start, so no event at a midnight
    boundary is counted in neither day or both. A range of zero or negative width is refused
    (`OccurredRangeInvalidError`).
    """

    actor_user_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    action: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class ActorInfo:
    """The acting user, resolved by internal UUID (NFR-13, NFR-17); see the module docstring.
    `AuditEventRow.actor` is `None`, not an `ActorInfo` of `None`s, for a system-initiated
    event with no actor row."""

    id: uuid.UUID
    display_name: str | None
    is_closed: bool


@dataclass(frozen=True, slots=True)
class AuditEventRow:
    """One `audit_event` row projected for NFR-12: the raw stored `before`/`after` plus the two
    hash-chain fields (NFR-10).

    **`prev_hash` and `entry_hash` let a reader with database access cross-reference this row
    against the stored one. They do not make the row re-hashable on its own.**
    `nptc.audit.hashing.digest_field_names` covers every `audit_event` column except
    `entry_hash` and `sequence`, including `id`, `correlation_id`, `actor_ip` and `user_agent`.
    This projection carries none of those four, because an actor's IP address and user agent do
    not belong in a downloadable file (NFR-26, NFR-35). Recomputing `entry_hash` needs the full
    row, which `nptc.audit.verification.verify_chain` reads from the table."""

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
    """One keyset page of matching events, most recent first. `next_cursor` is `None` exactly
    when this is the last page, decided by the one extra row requested and not by `COUNT(*)`, as
    in `nptc.catalogue.history.HistoryPage`."""

    events: tuple[AuditEventRow, ...]
    next_cursor: str | None


def validate_audit_filters(filters: AuditEventFilter) -> None:
    """Raises `AuditFilterError` for a combination that can never match; see that error.

    A free function, not `AuditEventFilter.__post_init__`, so a caller building one filter at a
    time (the API layer's per-query-parameter shape) is not forced into a construction order
    that avoids a transient invalid state. `search_audit_events` and `stream_audit_events` both
    call it eagerly.
    """
    if filters.entity_id is not None and filters.entity_type is None:
        raise EntityIdRequiresEntityTypeError("entity_id filter requires entity_type")
    if (
        filters.occurred_from is not None
        and filters.occurred_to is not None
        and filters.occurred_from >= filters.occurred_to
    ):
        raise OccurredRangeInvalidError("occurred_from must be strictly before occurred_to")


def _select_events() -> Select[*tuple[Any, ...]]:
    """The 13-column projection shared by `search_audit_events` and `_stream_audit_events`, so a
    column added to one path cannot miss the other."""
    return (
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
    )


def _row_to_event(row: Row[Any]) -> AuditEventRow:
    """Builds one `AuditEventRow` from a row that `_select_events()` produced."""
    return AuditEventRow(
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
    """One keyset page of `audit_event`, filtered by `filters`, `sequence` descending.

    `before` is the `sequence` of the last event on the previous page, exclusive.
    Raises `MalformedAuditCursorError` or `AuditFilterError` before running any query.
    """
    if before is not None and before > _BIGINT_MAX:
        raise MalformedAuditCursorError(f"audit cursor {before} exceeds bigint range")
    validate_audit_filters(filters)

    predicates = _predicates(filters)
    if before is not None:
        predicates.append(AuditEvent.sequence < before)

    statement = (
        _select_events()
        .where(*predicates)
        .order_by(AuditEvent.sequence.desc())
        # One extra row: its existence answers "is there a next page".
        .limit(limit + 1)
    )

    rows = session.execute(statement).all()
    page_rows = rows[:limit]
    events = tuple(_row_to_event(row) for row in page_rows)
    next_cursor = str(page_rows[-1].sequence) if len(rows) > limit else None
    return AuditEventPage(events=events, next_cursor=next_cursor)


#: Rows per database round trip when streaming. Same value and reason as
#: `nptc.audit.verification`'s `_DEFAULT_BATCH_SIZE`.
_DEFAULT_EXPORT_BATCH_SIZE: Final = 500


def stream_audit_events(
    session: Session,
    filters: AuditEventFilter,
    *,
    batch_size: int = _DEFAULT_EXPORT_BATCH_SIZE,
) -> Iterator[AuditEventRow]:
    """Every `audit_event` row matching `filters`, oldest first: the export read path (NFR-12).
    There is no limit or cursor, because an export is the whole filtered set (ADR-0039 covers
    why that is fine at this catalogue's size).

    Oldest first, unlike the read route's newest-first pages, because ascending `sequence` is
    the direction `nptc.audit.verification.verify_chain` walks. Each row carries `entry_hash`
    and `prev_hash` (NFR-10) so it can be cross-referenced against the stored row; see
    `AuditEventRow` for why that is narrower than recomputation. **A filtered export is not
    contiguous in the real chain**, so even an operator with database access cannot walk
    `prev_hash` linkage across it, only confirm each row against its stored counterpart.

    **Validates eagerly.** This function is not itself a generator: it raises
    `AuditFilterError` at once and only then returns the lazy generator. If validation ran
    inside the generator, `StreamingResponse` would already have sent a `200` and its headers,
    making a clean 422 impossible (see `nptc.api.routers.audit.export_audit_events`).
    """
    validate_audit_filters(filters)
    return _stream_audit_events(session, filters, batch_size)


def _stream_audit_events(
    session: Session, filters: AuditEventFilter, batch_size: int
) -> Iterator[AuditEventRow]:
    statement = (
        _select_events()
        .where(*_predicates(filters))
        .order_by(AuditEvent.sequence.asc())
        .execution_options(yield_per=batch_size)
    )
    for row in session.execute(statement):
        yield _row_to_event(row)
