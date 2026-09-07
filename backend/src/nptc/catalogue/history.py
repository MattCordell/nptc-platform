"""FR-19's audit-derived entry history (issue #141): every `audit_event`
against an entry or one of its children, projected as *what changed*
(field names only, never values) plus the changelog note - never the raw
`before`/`after` diff itself.

**Scope note (see the issue's own plan comment).** FR-19 asks for "every
published release in which it appeared, what changed at each, and the
changelog note" - releases do not exist until P4, so `HistoryEventRow.
release` is a defined, always-`None` slot here, populated once P4 lands.
This module delivers the audit-derived half only.

Read-only: this module has no write path of its own. Every event it reads
was written elsewhere - `nptc.catalogue.entries`/`designations`/`bindings`/
`property_values`, via `nptc.audit.recording.record_change` - and none of
that changes here.

**Spans an entry's children, not only the entry's own row.** A
`designation`/`code_binding` audit event's `entity_id` is *that child's
own* primary key (`nptc.audit.recording._default_entity_id`'s default),
never the parent entry's - so this module resolves every child id
attached to the entry first (`nptc.catalogue.queries.
load_designations_for_write`/`load_bindings`, both already unfiltered by
status: a retired child's history belongs in the entry's history too),
then queries `audit_event` for the union of `catalogue_entry`,
`designation`, `code_binding` and `property_value_set` rows naming one of
those ids. `property_value_set`'s own composite key
(`f"{entry_id}:{property_key}"`, `nptc.catalogue.property_values`'s own
precedent) is rebuilt from every property key the entry currently carries
a value for (`load_property_values`) - a property once set and later
cleared to no values at all would not surface here, a known limitation of
this minimal read rather than something this issue attempts to close.

**Redacted by construction, not by filtering.** `_changed_field_names`
emits field *names* only - it never reads a value out of `before`/`after`
- so there is no code path here that could serialise a withheld value.
`nptc.audit.diffing.REDACTED_KEY`'s own name list is unpacked into the
result exactly like any other changed field: that a field changed is not
the secret FR-18/FR-19 protect, only its value is.

Ordered by `sequence`, never `occurred_at` - matching
`nptc.catalogue.entries._latest_change_attribution`'s own precedent:
two events in the same transaction share a `clock_timestamp()`-derived
value closely enough that ordering by it alone leaves a tie-break
undefined. `sequence` is a globally monotonic identity column, so paging
on it (most recent first) can never drop or repeat a row across a page
boundary, the same guarantee `nptc.catalogue.queries.list_entries` gets
from `business_key`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from nptc.audit.diffing import REDACTED_KEY
from nptc.catalogue import queries
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation
from nptc.db.models.user import User

__all__ = ["HistoryEventRow", "HistoryPage", "load_history"]

#: `nptc.catalogue.property_values`'s own entity type for a property's
#: whole value set - no exported constant there to import, so this
#: literal matches that module's own inline string exactly.
_PROPERTY_VALUE_SET_ENTITY_TYPE = "property_value_set"


@dataclass(frozen=True, slots=True)
class HistoryEventRow:
    """One audit event against an entry or one of its children, projected
    for FR-19 - never the raw `before`/`after` diff (see the module
    docstring).

    `release` is always `None` in P1 - the defined slot P4's release
    membership fills once releases exist.
    """

    sequence: int
    occurred_at: datetime
    action: str
    changed_by: str | None
    changed_fields: tuple[str, ...]
    note: str | None
    release: None = None


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """One keyset page of an entry's history, most recent first.

    `next_cursor` is `None` exactly when this is the last page - decided
    by the one extra row `load_history` asked for, never by a `COUNT(*)`,
    matching `nptc.catalogue.queries.EntryPage`'s own precedent.
    """

    events: tuple[HistoryEventRow, ...]
    next_cursor: str | None


def _changed_field_names(
    before: Mapping[str, object] | None, after: Mapping[str, object] | None
) -> tuple[str, ...]:
    """Every field name in `before`/`after`, with `REDACTED_KEY` unpacked
    into the names it lists rather than kept as a literal key of its own -
    see the module docstring's redaction note."""
    names: set[str] = set()
    for payload in (before, after):
        if payload is None:
            continue
        for key, value in payload.items():
            if key == REDACTED_KEY:
                if isinstance(value, list):
                    names.update(str(name) for name in value)
            else:
                names.add(key)
    return tuple(sorted(names))


def load_history(
    session: Session,
    entry: CatalogueEntry,
    *,
    limit: int,
    before: int | None = None,
) -> HistoryPage:
    """One keyset page of `entry`'s change history, most recent first.

    `before` is the `sequence` of the last event on the previous page
    (exclusive) - `sequence` is a globally monotonic identity column, so
    it makes a total order with no possible tie.
    """
    designation_ids: Sequence[str] = tuple(
        str(row.id) for row in queries.load_designations_for_write(session, (entry.id,))
    )
    binding_ids: Sequence[str] = tuple(
        str(row.id) for row in queries.load_bindings(session, (entry.id,))
    )
    property_value_set_ids: Sequence[str] = tuple(
        f"{entry.id}:{row.property_key}"
        for row in queries.load_property_values(session, (entry.id,))
    )

    predicates = [
        and_(
            AuditEvent.entity_type == CatalogueEntry.__tablename__,
            AuditEvent.entity_id == str(entry.id),
        )
    ]
    if designation_ids:
        predicates.append(
            and_(
                AuditEvent.entity_type == Designation.__tablename__,
                AuditEvent.entity_id.in_(designation_ids),
            )
        )
    if binding_ids:
        predicates.append(
            and_(
                AuditEvent.entity_type == CodeBinding.__tablename__,
                AuditEvent.entity_id.in_(binding_ids),
            )
        )
    if property_value_set_ids:
        predicates.append(
            and_(
                AuditEvent.entity_type == _PROPERTY_VALUE_SET_ENTITY_TYPE,
                AuditEvent.entity_id.in_(property_value_set_ids),
            )
        )

    statement = (
        select(
            AuditEvent.sequence,
            AuditEvent.occurred_at,
            AuditEvent.action,
            User.display_name,
            AuditEvent.before,
            AuditEvent.after,
            AuditEvent.reason,
        )
        .select_from(AuditEvent)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(or_(*predicates))
        .order_by(AuditEvent.sequence.desc())
        # One more row than asked for: its existence *is* the answer to
        # "is there a next page" - matching `list_entries`' own precedent.
        .limit(limit + 1)
    )
    if before is not None:
        statement = statement.where(AuditEvent.sequence < before)

    rows = session.execute(statement).all()
    page_rows = rows[:limit]
    events = tuple(
        HistoryEventRow(
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            action=row.action,
            changed_by=row.display_name,
            changed_fields=_changed_field_names(row.before, row.after),
            note=row.reason,
        )
        for row in page_rows
    )
    next_cursor = str(page_rows[-1].sequence) if len(rows) > limit else None
    return HistoryPage(events=events, next_cursor=next_cursor)
