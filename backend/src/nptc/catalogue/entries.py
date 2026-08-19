"""The `catalogue_entry` service layer: business_key minting and the FR-38
optimistic-locking write path (issue #46).

**Why `save_entry`/`save_entries` never build a Core `sqlalchemy.update()`
statement.** ADR-0012 already names the hazard for `property_definition`:
a Core-style bulk update goes through the ORM `Session` but bypasses
`version_id_col` enforcement entirely, silently turning every concurrent
editor's optimistic lock into decoration. Every write here loads the
mapped instance and lets the ORM's own `UPDATE ... WHERE row_version = ...`
do the real work - `backend/tests/test_sql_parameterisation.py`'s AST guard
enforces this statically for `catalogue_entry` the same way it does for
SQL string construction.

**Two layers of conflict detection, not one.** `save_entry` first checks
`expected_row_version` against the freshly loaded row *before* mutating
anything - this is the path that can build a useful `ConflictReport`,
because both the caller's stale view and the current row are in hand
uncorrupted. `version_id_col` is the backstop for the genuine race: two
callers who both pass that first check and then interleave between load
and flush. That second case surfaces as SQLAlchemy's own `StaleDataError`
at `session.flush()` time, wrapped inside a `session.begin_nested()`
savepoint so only this entry's attempted write rolls back - not the whole
request, which matters for `save_entries`' multi-entry, one-savepoint-per-
entry loop that #63's bulk reclassify is meant to call.

**Why no audit event is ever written for a rejected save.** The first
layer raises before `nptc.audit.recording.record_change` is ever called.
The second layer's `StaleDataError` is raised by `session.flush()` *inside*
`nptc.audit.writer.append_audit_event`'s own flush step, which runs before
that function ever constructs or adds an `AuditEvent` row - so the
savepoint rollback discards only the attempted (and never-persisted)
`UPDATE`, and no audit row is ever built in the first place. Both paths
have their own test (`backend/tests/test_catalogue_optimistic_locking.py`);
one passing proves nothing about the other.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import policy_for
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.errors import (
    ConflictReport,
    EntryVersionConflictError,
    FieldConflict,
    ImmutableFieldEditError,
)
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus

#: The single Python source of truth for the FR-03 format - shared by
#: `format_business_key`, `advance_sequence_past`, and mirrored (never
#: generated from this constant, per test_sql_parameterisation.py's ban on
#: SQL built from runtime data) by `CatalogueEntry`'s own CHECK constraint
#: and migration 0006's sequence-backed default.
BUSINESS_KEY_PREFIX: Final[str] = "NPTC-"
BUSINESS_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^NPTC-([0-9]{6,})$")

#: Fixed name, referenced identically here and in migration
#: 0006_catalogue_entry.py (via `nptc.db.roles`) - a single source of truth
#: for the one identifier that must match on both sides.
BUSINESS_KEY_SEQUENCE_NAME: Final[str] = "catalogue_entry_business_key_seq"


def format_business_key(sequence_value: int) -> str:
    """`NPTC-` plus a 6-digit zero-padded sequence value (FR-03), for
    example `NPTC-000247`. Not capped at 6 digits: `BUSINESS_KEY_PATTERN`
    (and the database CHECK it mirrors) accept more, so a catalogue that
    outgrows six digits needs no migration - only a wider zero-pad here
    would need to change, and even that is cosmetic once past 999999."""
    return f"{BUSINESS_KEY_PREFIX}{sequence_value:06d}"


def allocate_business_key(session: Session) -> str:
    """Mints the next `business_key` from the dedicated Postgres sequence.
    The one mint point for a genuinely new (non-seeded) entry - see
    `nptc.db.models.catalogue_entry`'s module docstring for why this lives
    in Python rather than a column `server_default`."""
    next_value = session.execute(
        text("SELECT nextval(:seq)"), {"seq": BUSINESS_KEY_SEQUENCE_NAME}
    ).scalar_one()
    return format_business_key(int(next_value))


def advance_sequence_past(session: Session, business_key: str) -> None:
    """Reconciles the backend's minting sequence with a seeded baseline
    (ADR-0010): the P0 transform mints its own `business_key`s
    deterministically and positionally, so after a seeded import this must
    be called once with the *highest* seeded key to guarantee the next
    `allocate_business_key` call mints a key strictly greater than every
    seeded one.

    Deliberately never moves the sequence *backwards*: applying `setval`
    unconditionally to a key lower than the sequence's current value would
    silently reissue keys already minted since the last reconciliation -
    the exact FR-03 defect this module exists to prevent. The current
    value is read and compared in Python first, so calling this with a
    stale (lower) key is a safe no-op, not a corruption."""
    match = BUSINESS_KEY_PATTERN.match(business_key)
    if match is None:
        raise ValueError(f"{business_key!r} does not match the NPTC business_key format")
    numeric_value = int(match.group(1))

    # A plain literal, not an f-string built from BUSINESS_KEY_SEQUENCE_NAME
    # - test_sql_parameterisation.py's AST guard flags any f-string whose
    # literal text starts with a SQL keyword, and Postgres has no
    # parameter-binding syntax for an object name in any case. The literal
    # below and the constant above are asserted to agree by
    # test_catalogue_business_key.py.
    current_value = session.execute(
        text("SELECT last_value FROM catalogue_entry_business_key_seq")
    ).scalar_one()
    if numeric_value <= int(current_value):
        return

    session.execute(
        text("SELECT setval(:seq, :value, true)"),
        {"seq": BUSINESS_KEY_SEQUENCE_NAME, "value": numeric_value},
    )


@dataclass(frozen=True)
class EntryChanges:
    """Fields a save may change. `business_key` has deliberately no field
    here at all - FR-03 immutability is expressed by the absence, not by a
    runtime rejection of a value this dataclass would otherwise accept."""

    preferred_term: str | None = None
    status: str | None = None
    specimen_unconstrained: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            name: value
            for name, value in (
                ("preferred_term", self.preferred_term),
                ("status", self.status),
                ("specimen_unconstrained", self.specimen_unconstrained),
            )
            if value is not None
        }


def create_entry(
    session: Session,
    ctx: AuditContext,
    *,
    preferred_term: str,
    status: CatalogueEntryStatus | str = CatalogueEntryStatus.DRAFT,
    specimen_unconstrained: bool = False,
    reason: str | None = None,
    business_key: str | None = None,
) -> CatalogueEntry:
    """Creates a new entry. `business_key` is minted via
    `allocate_business_key` unless the caller supplies one explicitly - the
    seeded-import path (ADR-0010) supplies its own, positionally-derived
    key rather than minting."""
    resolved_key = business_key if business_key is not None else allocate_business_key(session)
    entry = CatalogueEntry(
        business_key=resolved_key,
        preferred_term=preferred_term,
        status=str(status),
        specimen_unconstrained=specimen_unconstrained,
    )
    session.add(entry)
    record_change(
        session,
        ctx,
        action="catalogue_entry.created",
        instance=entry,
        kind=ChangeKind.CREATED,
        reason=reason,
    )
    return entry


def _load_for_update(session: Session, business_key: str) -> CatalogueEntry:
    entry = session.execute(
        select(CatalogueEntry).where(CatalogueEntry.business_key == business_key)
    ).scalar_one_or_none()
    if entry is None:
        raise LookupError(f"no catalogue_entry with business_key={business_key!r}")
    return entry


def _latest_change_attribution(session: Session, entry_id: uuid.UUID) -> tuple[str | None, object]:
    row = session.execute(
        select(AuditEvent.actor_user_id, AuditEvent.occurred_at)
        .where(
            AuditEvent.entity_type == CatalogueEntry.__tablename__,
            AuditEvent.entity_id == str(entry_id),
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        return None, None
    actor_user_id, occurred_at = row
    # NFR-04/NFR-26: never the internal UUID itself. Attribution here is
    # deliberately coarse (whether *a* prior change is recorded, and when)
    # rather than a resolved display name, keeping this function free of a
    # dependency on nptc.db.models.user for a conflict report that already
    # carries plenty of detail without it.
    return (str(actor_user_id) if actor_user_id is not None else None), occurred_at


def _build_conflict_report(
    entry: CatalogueEntry,
    *,
    expected_row_version: int,
    changes: EntryChanges,
    changed_by: str | None,
    changed_at: object,
) -> ConflictReport:
    auditable = policy_for(CatalogueEntry).auditable
    submitted = changes.as_dict()
    conflicts = tuple(
        FieldConflict(field=name, submitted=value, current=getattr(entry, name))
        for name, value in submitted.items()
        if name in auditable and getattr(entry, name) != value
    )
    return ConflictReport(
        business_key=entry.business_key,
        expected_row_version=expected_row_version,
        current_row_version=entry.row_version,
        conflicts=conflicts,
        changed_by=changed_by,
        changed_at=changed_at,  # type: ignore[arg-type]
    )


def save_entry(
    session: Session,
    ctx: AuditContext,
    *,
    business_key: str,
    expected_row_version: int,
    changes: EntryChanges,
    reason: str | None = None,
) -> CatalogueEntry:
    """Applies `changes` to the entry identified by `business_key`,
    enforcing FR-38 optimistic locking. Raises `EntryVersionConflictError`
    (never a silent overwrite) if `expected_row_version` is stale, whether
    caught by the explicit precondition check below or by the
    `version_id_col` backstop for a genuine concurrent race - see the
    module docstring for why neither path ever leaves an audit event
    behind."""
    if "business_key" in changes.as_dict():  # pragma: no cover - EntryChanges has no such field
        raise ImmutableFieldEditError("business_key cannot be changed by save_entry")

    entry = _load_for_update(session, business_key)

    if entry.row_version != expected_row_version:
        changed_by, changed_at = _latest_change_attribution(session, entry.id)
        raise EntryVersionConflictError(
            _build_conflict_report(
                entry,
                expected_row_version=expected_row_version,
                changes=changes,
                changed_by=changed_by,
                changed_at=changed_at,
            )
        )

    # The savepoint must open *before* any attribute is mutated: opening
    # one autoflushes any already-pending state, and if that happened
    # after the setattr calls below it would flush the mutation straight
    # to the database and clear SQLAlchemy's attribute history before
    # record_change ever gets to read it - turning every save into a
    # spurious AuditNoOpError regardless of whether it actually conflicts.
    savepoint = session.begin_nested()
    try:
        for name, value in changes.as_dict().items():
            setattr(entry, name, value)

        record_change(
            session,
            ctx,
            action="catalogue_entry.updated",
            instance=entry,
            kind=ChangeKind.UPDATED,
            reason=reason,
        )
    except (StaleDataError, ObjectDeletedError):
        savepoint.rollback()
        session.expire(entry)
        refreshed = _load_for_update(session, business_key)
        changed_by, changed_at = _latest_change_attribution(session, refreshed.id)
        raise EntryVersionConflictError(
            _build_conflict_report(
                refreshed,
                expected_row_version=expected_row_version,
                changes=changes,
                changed_by=changed_by,
                changed_at=changed_at,
            )
        ) from None
    else:
        savepoint.commit()

    return entry


def save_entries(
    session: Session,
    ctx: AuditContext,
    *,
    updates: Sequence[tuple[str, int, EntryChanges]],
    reason: str | None = None,
) -> list[CatalogueEntry]:
    """Applies a batch of `(business_key, expected_row_version, changes)`
    updates, one `save_entry` call - and one savepoint - per entry (FR-39's
    "one audit event per affected entry"). This is the seam #63's bulk
    reclassify is meant to call instead of a Core bulk `update()`: it
    exists now, ahead of that issue, specifically so there is a correct
    path to reach for rather than one to invent under deadline."""
    return [
        save_entry(
            session,
            ctx,
            business_key=business_key,
            expected_row_version=expected_row_version,
            changes=changes,
            reason=reason,
        )
        for business_key, expected_row_version, changes in updates
    ]
