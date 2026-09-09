"""`GET /audit/events` and `GET /audit/events/export` (issue #286,
NFR-12): the administrator search/filter surface over `audit_event`, and
its NDJSON export sibling.

**Gated on `Permission.AUDIT_READ`**, Administrator-only and in
`MFA_REQUIRED_PERMISSIONS` (NFR-06), matching `catalogue_bindings.py`'s
own write routes - so the RFC 9470 step-up challenge comes free, with no
extra code in this module.

**Serves `nptc.audit.queries`' row shape close to verbatim.** `before`/
`after` are the stored JSONB as-is, and `actor` resolves by internal id,
`null` display name and all - see that module's own docstring for why
both are safe here, on an Administrator-plus-MFA surface, in a way they
are not on `nptc.catalogue.history`'s public FR-19 one.

**The export streams, and validates before it starts streaming.**
`export_audit_events` calls `nptc.audit.queries.stream_audit_events`
(which validates `filters` eagerly - see its own docstring) before ever
constructing the `StreamingResponse` - a `StreamingResponse` sends its
`200` and headers as soon as its body iterator is first pulled from, so a
validation error raised lazily inside that iterator could not become a
clean `422` any more. No `audit.exported` audit event: this route holds
no write privilege at all (NFR-09), and NFR-08 scopes audit events to
state-changing operations - a deliberate decision, not an oversight (see
`docs/adr/0039-*.md`).
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
from nptc.api.routers.catalogue_shared import LimitQuery
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
_RESPONSE_422: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A query parameter was unprocessable - a cursor this API did not issue, a "
        "`limit` outside its range, `entity_id` given without `entity_type`, "
        "`occurred_from` after `occurred_to`, or `occurred_from`/`occurred_to` given "
        "with no UTC offset."
    ),
}
_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    422: _RESPONSE_422,
}

SessionDep = Annotated[Session, Depends(get_session)]
_READ = Depends(permission_dep(Permission.AUDIT_READ))

#: Matches `nptc.catalogue.history.HistoryCursorQuery` exactly - the same
#: cursor shape (a digit string bounded by `AuditEvent.sequence`'s own
#: `BigInteger` range), for the identical reason: bounding the digit count
#: here is what stops a pathologically long cursor from ever reaching
#: `int(before)` below, and `nptc.audit.queries.MalformedAuditCursorError`
#: catches the remainder (a well-formed but out-of-range digit string).
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

ActorFilterQuery = Annotated[
    uuid.UUID | None, Query(description="Filter to one actor, by internal id.")
]
EntityTypeFilterQuery = Annotated[
    str | None,
    Query(
        description=(
            "Filter to one entity type, e.g. `catalogue_entry`. Required alongside `entity_id`."
        )
    ),
]
EntityIdFilterQuery = Annotated[
    str | None,
    Query(
        description=(
            "Filter to one entity, alongside `entity_type` - the two are required "
            "together, and `entity_id` alone is a 422."
        )
    ),
]
ActionFilterQuery = Annotated[
    str | None,
    Query(description="Filter to one action name, e.g. `catalogue_entry.updated`."),
]
#: `AwareDatetime`, not plain `datetime`: a naive value has no defined
#: meaning against `occurred_at` (`TIMESTAMP WITH TIME ZONE`) - accepting
#: one would leave Postgres to guess a timezone from its own session
#: setting, silently, per deployment. Refused as a 422 instead, so a
#: caller who forgot an offset learns that rather than getting a filter
#: that happens to work today and drifts wrong the day the server's
#: timezone setting ever changes.
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
    limit: LimitQuery = 50,
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


#: Fixed, not derived from the filter or the current time - a filter value
#: reflected into a response header is exactly the kind of caller-supplied-
#: text-in-a-header surface this platform avoids elsewhere (NFR-26/NFR-35).
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
    responses=_RESPONSES,
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
    `entry_hash` so the extract stays independently verifiable (NFR-10) -
    see `nptc.audit.queries.stream_audit_events`'s own docstring for why
    the order differs from the read route above. The whole filtered set,
    not one page: an export has no `limit`/cursor."""
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
