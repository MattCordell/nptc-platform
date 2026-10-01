"""`GET /audit/events` and `GET /audit/events/export` (NFR-12): the administrator search and
filter surface over `audit_event`, and its NDJSON export. ADR-0039 records the decisions
below.

**Gated on `Permission.AUDIT_READ`**, Administrator-only and in `MFA_REQUIRED_PERMISSIONS`
(NFR-06), so the RFC 9470 step-up challenge needs no code here.

**Rows are served close to verbatim.** `before` and `after` are the stored JSONB, and `actor`
resolves by internal id, with a `null` display name for a closed account. That is safe on an
Administrator-plus-MFA surface and not on the public FR-19 history (see
`nptc.audit.queries`).

**The export validates before it streams.** `export_audit_events` calls
`stream_audit_events`, which validates `filters` eagerly, before it builds the
`StreamingResponse`. That response sends its `200` and headers on the first pull from its
iterator, so a validation error raised lazily could no longer become a 422. The route emits
no `audit.exported` event: it holds no write privilege (NFR-09), and NFR-08 scopes audit
events to state-changing operations.

**`prev_hash` and `entry_hash` are for cross-reference, not standalone recomputation** (see
`nptc.audit.queries.AuditEventRow`).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy.orm import Session

from nptc.api.dependencies import get_session, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.audit import queries as audit_queries
from nptc.auth.permissions import Permission

router = APIRouter(prefix="/audit", tags=["audit"])

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `audit.read`, or holds it "
        "but has not completed the MFA step-up this permission requires (the "
        "response then also carries a `WWW-Authenticate` step-up challenge)."
    ),
}
#: The read route's own 422 causes. `_EXPORT_RESPONSE_422` is separate because the
#: export accepts neither `before` nor `limit`.
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query parameter was unprocessable - a cursor this API did not issue, a "
        "`limit` outside its range, `entity_id` given without `entity_type`, "
        "`occurred_from` at or after `occurred_to`, or `occurred_from`/`occurred_to` "
        "given with no UTC offset."
    ),
}
_EXPORT_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query parameter was unprocessable - `entity_id` given without "
        "`entity_type`, `occurred_from` at or after `occurred_to`, or `occurred_from`/"
        "`occurred_to` given with no UTC offset. This route has no `limit`/cursor "
        "of its own to be unprocessable."
    ),
}
_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _RESPONSE_422,
}
#: The export's response map. The 200 is declared explicitly because FastAPI
#: cannot infer a `StreamingResponse`'s content type from its return annotation,
#: so the document would show `application/json` with an empty schema for an
#: NDJSON body.
_EXPORT_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "The filtered audit log, one JSON object per line.",
        "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
    },
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _EXPORT_RESPONSE_422,
}

SessionDep = Annotated[Session, Depends(get_session)]
_READ = Depends(permission_dep(Permission.AUDIT_READ))

#: Not `catalogue_shared.LimitQuery`: its description says "entries", and an audit
#: event is not an entry. Same bounds.
AuditLimitQuery = Annotated[
    int,
    Query(ge=1, le=200, description="Maximum events in this page."),
]

#: The same cursor shape as `catalogue.py`'s `HistoryCursorQuery`: a digit string
#: bounded by `AuditEvent.sequence`'s `BigInteger` range. Bounding the digit count
#: stops an arbitrarily long cursor reaching `int(before)` below, and
#: `nptc.audit.queries.MalformedAuditCursorError` catches a well-formed but
#: out-of-range string.
AuditCursorQuery = Annotated[
    str | None,
    Query(
        pattern=r"^[0-9]+$",
        max_length=19,
        description=(
            "The `next_cursor` from the previous page. Pass it back unmodified, "
            "and do not construct one."
        ),
    ),
]

#: Not derived from a column width: `entity_type`, `entity_id` and `action` are
#: unbounded `Text`. 200 caps a pathological query string and is not a business
#: rule; every value this platform writes is far shorter.
_FILTER_VALUE_MAX_LENGTH: Final = 200

ActorFilterQuery = Annotated[
    uuid.UUID | None, Query(description="Filter to one actor, by internal id.")
]
EntityTypeFilterQuery = Annotated[
    str | None,
    Query(
        max_length=_FILTER_VALUE_MAX_LENGTH,
        description=(
            "Filter to one entity type, e.g. `catalogue_entry`. Required alongside `entity_id`."
        ),
    ),
]
EntityIdFilterQuery = Annotated[
    str | None,
    Query(
        max_length=_FILTER_VALUE_MAX_LENGTH,
        description=(
            "Filter to one entity, alongside `entity_type` - the two are required "
            "together, and `entity_id` alone is a 422."
        ),
    ),
]
ActionFilterQuery = Annotated[
    str | None,
    Query(
        max_length=_FILTER_VALUE_MAX_LENGTH,
        description="Filter to one action name, e.g. `catalogue_entry.updated`.",
    ),
]
#: `AwareDatetime`, not `datetime`: a naive value has no meaning against
#: `occurred_at` (`TIMESTAMP WITH TIME ZONE`), and Postgres would silently guess a
#: timezone from its session setting. A 422 tells the caller instead.
OccurredFromQuery = Annotated[
    AwareDatetime | None,
    Query(
        description="Filter to events at or after this instant (inclusive). Must include a UTC offset."
    ),
]
OccurredToQuery = Annotated[
    AwareDatetime | None,
    Query(
        description="Filter to events before this instant (exclusive). Must include a UTC offset."
    ),
]


class AuditActor(BaseModel):
    """The acting user, resolved by internal id (NFR-13, NFR-17) - see
    `nptc.audit.queries`'s own module docstring for why a bare display
    name is not enough on this Administrator-only surface.

    `display_name` is `null` for a closed (tombstoned) account -
    `is_closed` is what tells that apart from an account that simply never
    set one."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    display_name: str | None
    is_closed: bool


class AuditEventItem(BaseModel):
    """One `audit_event` row, served close to verbatim (NFR-12) - see
    `nptc.audit.queries`'s own module docstring for why `before`/`after`
    are raw here rather than the field-names-only projection FR-19's
    public history surface uses. `actor` is `null` for a system-initiated
    event, never a name-only fallback."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    occurred_at: datetime
    actor: AuditActor | None
    action: str
    entity_type: str
    entity_id: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    reason: str | None


class AuditEventPage(BaseModel):
    """One keyset page, most recent first. `next_cursor` is `null` exactly
    when this is the last page - matching every other paged surface in
    this API."""

    model_config = ConfigDict(frozen=True)

    items: list[AuditEventItem]
    next_cursor: str | None


def _actor_from_row(actor: audit_queries.ActorInfo | None) -> AuditActor | None:
    if actor is None:
        return None
    return AuditActor(id=actor.id, display_name=actor.display_name, is_closed=actor.is_closed)


def _event_from_row(row: audit_queries.AuditEventRow) -> AuditEventItem:
    return AuditEventItem(
        sequence=row.sequence,
        occurred_at=row.occurred_at,
        actor=_actor_from_row(row.actor),
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        before=row.before,
        after=row.after,
        reason=row.reason,
    )


@router.get(
    "/events",
    summary="Search and filter the audit log (NFR-12)",
    responses=_RESPONSES,
    dependencies=[_READ],
)
def read_audit_events(
    session: SessionDep,
    limit: AuditLimitQuery = 50,
    before: AuditCursorQuery = None,
    actor_user_id: ActorFilterQuery = None,
    entity_type: EntityTypeFilterQuery = None,
    entity_id: EntityIdFilterQuery = None,
    action: ActionFilterQuery = None,
    occurred_from: OccurredFromQuery = None,
    occurred_to: OccurredToQuery = None,
) -> AuditEventPage:
    """Every filter narrows independently and in combination, AND-ed
    together; none is required, so an unfiltered call pages through the
    whole log. Never empty-errors: no matching events is a `200` with an
    empty `items` list, not a 404."""
    page = audit_queries.search_audit_events(
        session,
        audit_queries.AuditEventFilter(
            actor_user_id=actor_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        ),
        limit=limit,
        before=int(before) if before is not None else None,
    )
    return AuditEventPage(
        items=[_event_from_row(event) for event in page.events],
        next_cursor=page.next_cursor,
    )


#: Fixed, not derived from the filter or the clock: this platform avoids reflecting
#: caller-supplied text into a header (NFR-26/NFR-35).
_EXPORT_FILENAME = "audit-events.ndjson"


def _export_payload(row: audit_queries.AuditEventRow) -> dict[str, Any]:
    actor = _actor_from_row(row.actor)
    return {
        "sequence": row.sequence,
        "occurred_at": row.occurred_at.isoformat(),
        "actor": actor.model_dump(mode="json") if actor is not None else None,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "before": row.before,
        "after": row.after,
        "reason": row.reason,
        "prev_hash": row.prev_hash,
        "entry_hash": row.entry_hash,
    }


def _ndjson_lines(rows: Iterator[audit_queries.AuditEventRow]) -> Iterator[str]:
    for row in rows:
        yield json.dumps(_export_payload(row)) + "\n"


@router.get(
    "/events/export",
    summary="Export the filtered audit log as NDJSON (NFR-12)",
    # `response_class=StreamingResponse`, not just the annotation: FastAPI cannot
    # infer a streamed body's content type, and would also guess a JSON `200`.
    # `_EXPORT_RESPONSES` declares the real schema.
    response_class=StreamingResponse,
    responses=_EXPORT_RESPONSES,
    dependencies=[_READ],
)
def export_audit_events(
    session: SessionDep,
    actor_user_id: ActorFilterQuery = None,
    entity_type: EntityTypeFilterQuery = None,
    entity_id: EntityIdFilterQuery = None,
    action: ActionFilterQuery = None,
    occurred_from: OccurredFromQuery = None,
    occurred_to: OccurredToQuery = None,
) -> StreamingResponse:
    """One JSON object per line, oldest first, including `prev_hash`/
    `entry_hash` for cross-referencing a line against its stored row - see
    `nptc.audit.queries.AuditEventRow`'s own docstring for why that is a
    narrower guarantee than standalone recomputation, and `stream_audit_
    events`'s own docstring for why the order differs from the read route
    above. The whole filtered set, not one page: an export has no
    `limit`/cursor."""
    rows = audit_queries.stream_audit_events(
        session,
        audit_queries.AuditEventFilter(
            actor_user_id=actor_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        ),
    )
    return StreamingResponse(
        _ndjson_lines(rows),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{_EXPORT_FILENAME}"'},
    )
