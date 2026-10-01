"""FR-19's audit-derived entry history: every `audit_event` against an entry
or one of its children, projected as *what changed* (field names only, never
values) plus the changelog note - never the raw `before`/`after` diff.
ADR-0034 records the scope.

**Scope.** FR-19 asks for "every published release in which it appeared,
what changed at each, and the changelog note". Releases do not exist until
P4, so `HistoryEventRow.release` is a defined, always-`None` slot here. This
module delivers the audit-derived half only.

Read-only: every event it reads was written elsewhere, via
`nptc.audit.recording.record_change`.

**Spans an entry's children, not only the entry's own row.** A `designation`
or `code_binding` audit event's `entity_id` is that child's own primary key
(`nptc.audit.recording._default_entity_id`), never the parent entry's. So
this module resolves every child id attached to the entry first
(`nptc.catalogue.queries.load_designations_any_status` and `load_bindings`,
both unfiltered by status: a retired child's history belongs in the entry's
too), then queries `audit_event` for the union of `catalogue_entry`,
`designation`, `code_binding` and `property_value_set` rows naming one of
those ids. `property_value_set`'s composite key
(`f"{entry_id}:{property_key}"`) is rebuilt from every property key the
entry currently has a value for, so a property once set and later cleared to
no values does not surface. That is a known limitation of this minimal read.

**Redacted by construction, not by filtering.** `_changed_field_names` emits
field *names* only and never reads a value out of `before`/`after`, so no
code path can serialise a withheld value. The names listed under
`nptc.audit.diffing.REDACTED_KEY` are unpacked like any other changed field:
that a field changed is not the secret FR-18/FR-19 protect, only its value
is.

**`changed_by` is gated the same way**, on caller authentication rather than
field policy (NFR-26). `load_history`'s `include_changed_by` decides whether
the query joins `User` at all, so an anonymous caller's page never has a
display name to withhold.

Ordered by `sequence`, never `occurred_at`, as
`nptc.catalogue.entries._latest_change_attribution` does: two events in one
transaction share a `clock_timestamp()`-derived value closely enough that
ordering by it leaves the tie-break undefined. `sequence` is a globally
monotonic identity column, so paging on it (most recent first) never drops or
repeats a row across a page boundary, the guarantee
`nptc.catalogue.queries.list_entries` gets from `business_key`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.orm import Session

from nptc.audit.diffing import REDACTED_KEY
from nptc.catalogue import queries
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation
from nptc.db.models.user import User

__all__ = ["HistoryEventRow", "HistoryPage", "MalformedHistoryCursorError", "load_history"]

#: The largest value `AuditEvent.sequence` (a signed 64-bit `BigInteger`) can
#: hold. A real `next_cursor` never exceeds it, so `load_history` refuses a
#: larger cursor rather than pass it to the query, where it would overflow the
#: driver's bigint bind parameter or compare true against every row.
_BIGINT_MAX: Final = 2**63 - 1


class MalformedHistoryCursorError(ValueError):
    """Raised for a `before` cursor exceeding `_BIGINT_MAX`.

    `nptc.api.routers.catalogue.HistoryCursorQuery` already rejects anything
    that is not a short digit string; this catches a well-formed but
    out-of-range one. Refused, never silently reinterpreted, as in
    `nptc.catalogue.search.MalformedSearchCursorError`.
    """

    http_status: ClassVar[int] = 422


#: `nptc.catalogue.property_values`'s entity type for a property's whole value
#: set. That module exports no constant, so this literal must match its inline
#: string.
_PROPERTY_VALUE_SET_ENTITY_TYPE = "property_value_set"


@dataclass(frozen=True, slots=True)
class HistoryEventRow:
    """One audit event against an entry or one of its children, projected for
    FR-19 - never the raw `before`/`after` diff (module docstring).

    `changed_by` is `None` for a system-initiated event, a pseudonymised
    account, and (NFR-26) whenever `load_history` was called with
    `include_changed_by=False`. The three cases are indistinguishable here on
    purpose, matching `HistoryEvent`'s public field description.

    `release` is always `None` in P1: the defined slot that P4's release
    membership fills.
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
    """Every field name in `before`/`after`, with `REDACTED_KEY` unpacked into
    the names it lists (module docstring).

    Raises rather than silently dropping names if `REDACTED_KEY`'s value is
    not the `list` that `FieldDiff._payload` in `nptc.audit.diffing` writes. A redacted field
    name is what proves a withheld value changed at all, so an unrecognised
    shape must fail loudly, not vanish from an otherwise complete-looking
    public response.
    """
    names: set[str] = set()
    for payload in (before, after):
        if payload is None:
            continue
        for key, value in payload.items():
            if key == REDACTED_KEY:
                if not isinstance(value, list):
                    raise TypeError(
                        f"{REDACTED_KEY!r} value must be a list of field names, "
                        f"got {type(value).__name__}"
                    )
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
    include_changed_by: bool,
) -> HistoryPage:
    """One keyset page of `entry`'s change history, most recent first.

    `before` is the `sequence` of the last event on the previous page
    (exclusive). `sequence` is a globally monotonic identity column, so it
    gives a total order with no tie.

    Raises `MalformedHistoryCursorError` if `before` exceeds `_BIGINT_MAX`.

    `include_changed_by=False` (an anonymous caller; NFR-26) never joins
    `User`, rather than joining it and discarding `display_name`: redacted by
    construction, as `_changed_field_names` is. It has no default, so every
    caller states its own case rather than inheriting one that happens to be
    public-safe.
    """
    if before is not None and before > _BIGINT_MAX:
        raise MalformedHistoryCursorError(f"history cursor {before} exceeds bigint range")

    designation_ids: Sequence[str] = tuple(
        str(row.id) for row in queries.load_designations_any_status(session, (entry.id,))
    )
    binding_ids: Sequence[str] = tuple(
        str(row.id) for row in queries.load_bindings(session, (entry.id,))
    )
    # A set: `load_property_values` returns one row per *value*, so a
    # multi-valued property would repeat its `f"{entry.id}:{property_key}"`
    # once per value. Sorted, because a set's iteration order varies between
    # requests, and the emitted SQL text, and with it psycopg's
    # prepared-statement cache key, would vary with it: one cache entry per
    # permutation.
    property_keys = sorted(
        {row.property_key for row in queries.load_property_values(session, (entry.id,))}
    )
    property_value_set_ids: Sequence[str] = tuple(f"{entry.id}:{key}" for key in property_keys)

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

    changed_by_column = (
        User.display_name if include_changed_by else literal(None).label("display_name")
    )
    statement = (
        select(
            AuditEvent.sequence,
            AuditEvent.occurred_at,
            AuditEvent.action,
            changed_by_column,
            AuditEvent.before,
            AuditEvent.after,
            AuditEvent.reason,
        )
        .select_from(AuditEvent)
        .where(or_(*predicates))
        .order_by(AuditEvent.sequence.desc())
        # One extra row decides whether a next page exists.
        .limit(limit + 1)
    )
    if include_changed_by:
        statement = statement.outerjoin(User, User.id == AuditEvent.actor_user_id)
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
