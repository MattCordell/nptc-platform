"""FR-05 collision detection, and FR-08's blocking-severity neighbour.

`nptc.catalogue.designations`, `entries` and `bindings` all name this layer
as "not implemented here". `docs/architecture/data-model.md` ("Collision
detection") summarises the schema shape.

Three severities, three postures:

- **Error** (`assert_no_error_collisions`): a synonym that exactly matches
  another live entry's preferred term, or the symmetric case, a preferred
  term matching another live entry's preferred term or active synonym.
  Raised before any row is constructed, by every write path in
  `designations.py` and `entries.py`, so a rejected save leaves no audit
  event - the posture `bindings.create_binding` already holds.
- **Warning** (`warning_collisions`): the same synonym on multiple live
  entries. Never raised: the edit screen asks, and the save is permitted.
  `acknowledge_collision` records the editorial decision that silences it
  for one entry.
- **Blocking** (`nptc.catalogue.bindings.CodeBindingCodeAlreadyBoundError`,
  not this module): one active SNOMED code cannot be bound to two entries.
  The database enforces it (`ix_code_binding_one_active_entry_per_code`) and
  `create_binding` pre-empts it. It has no acknowledgement path.

**Candidate scope: `draft` and `active` entries.** FR-05 says "a different
active entry", but a `deprecated` or `withdrawn` entry never collides (PRD
acceptance criterion), and a `draft` entry that collides has the same
ordering hazard the moment either is published. Catching it at save time is
safer than the literal reading. `_LIVE_STATUSES` is the one place this is
spelled out.

**The comparison uses the stored `term_key`/`preferred_term_key` columns.**
The `@validates` hook that cleans a term writes its key too, so the stored
key cannot drift from `nptc_shared.similarity.collision_key`. This module
calls `collision_key` only on the term a caller is currently trying to save.

**Concurrency.** `assert_no_error_collisions` is check-then-insert. Two
concurrent transactions saving terms that fold to the same key could each
pass the check against a snapshot that predates the other's uncommitted
insert, and both commit: the state FR-05 forbids, with nothing to detect it
afterwards. No `UNIQUE` index can say "no two live rows, across two tables,
share this key", and a trigger is excluded (PRD SS14.1). So
`pg_advisory_xact_lock(hashtext(key))` is taken before the comparison
queries. It serialises exactly the transactions contending for the same key
(a `hashtext` collision between unrelated keys only costs harmless extra
serialisation), is released at commit or rollback, and needs no grant.
`hashtext` is an undocumented Postgres function with no cross-version
stability contract; that is harmless here because the value is never
persisted. The lock relies on `nptc.db.session.REQUIRED_ISOLATION_LEVEL`
pinning every connection to `READ COMMITTED`. `append_audit_event` re-checks
that at runtime because NFR-10 treats it as security-critical; this module
relies on the connection-level guarantee. ADR-0035 records how this lock
orders against the other write locks.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Permission
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.term_hygiene import TermCleaningError
from nptc.db.errors import unique_violation_constraint
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.designation import Designation, DesignationStatus
from nptc.db.models.designation_collision_acknowledgement import (
    DesignationCollisionAcknowledgement,
)
from nptc_shared.similarity import collision_key

__all__ = [
    "CandidateKind",
    "Collision",
    "CollisionSeverity",
    "DesignationCollisionAcknowledgementConflictError",
    "DesignationCollisionError",
    "acknowledge_collision",
    "assert_no_error_collisions",
    "warning_collisions",
]

#: The unique index `acknowledge_collision` can lose a race on, matched
#: against `unique_violation_constraint(exc)` as
#: `nptc.catalogue.designations.add_designation` matches its own.
_COLLISION_ACK_CONSTRAINT = "ix_designation_collision_ack_entry_term"

if TYPE_CHECKING:
    from nptc.auth.principal import Principal

#: `deprecated` and `withdrawn` entries never collide; `draft` is included with
#: `active` (module docstring).
_LIVE_STATUSES = ("draft", "active")

_ACQUIRE_COLLISION_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:key))")


class CollisionSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class CandidateKind(StrEnum):
    """What the term being saved will be. A synonym collides as an error only with another
    entry's preferred term; the same synonym on another entry is a warning. A preferred term also
    collides as an error with another entry's synonym."""

    PREFERRED_TERM = "preferred_term"
    SYNONYM = "synonym"


@dataclass(frozen=True)
class Collision:
    """One collision found against a live entry other than the one being
    saved. `business_key` and `preferred_term` name the *other* entry, never
    its internal UUID (NFR-04/NFR-26), so the edit screen can name the
    conflicting entry rather than show a bare 409 or warning."""

    severity: CollisionSeverity
    term: str
    term_key: str
    business_key: str
    preferred_term: str


class DesignationCollisionError(ValueError):
    """Raised by `assert_no_error_collisions`. Carries the `http_status`
    `ClassVar` that `nptc.api.errors.register_exception_handlers` reads, and
    every collision found, not just the first, so a caller need not fix one
    only to meet another on the next save."""

    http_status: ClassVar[int] = 409

    def __init__(self, collisions: tuple[Collision, ...]) -> None:
        if not collisions:
            raise ValueError("DesignationCollisionError requires at least one collision")
        business_keys = ", ".join(c.business_key for c in collisions)
        super().__init__(
            f"{len(collisions)} error-severity collision(s) against {business_keys} (FR-05)"
        )
        self.collisions = collisions


class DesignationCollisionAcknowledgementConflictError(ValueError):
    """Raised when two truly concurrent `acknowledge_collision` calls for the
    same `(entry, term_key)` both pass the select-first check and
    reach the `INSERT`. The loser 409s rather than 500ing; re-reading finds
    the winner's row, since both calls recorded the same decision."""

    http_status: ClassVar[int] = 409


def _matching_entries(
    session: Session, *, term_key: str, exclude_entry_id: uuid.UUID | None
) -> tuple[Collision, ...]:
    """Other live entries whose `preferred_term_key` equals `term_key` -
    the catalogue's own preferred term, which lives only on
    `catalogue_entry.preferred_term` (ADR-0022), never on a `designation`
    row."""
    conditions = [
        CatalogueEntry.preferred_term_key == term_key,
        CatalogueEntry.status.in_(_LIVE_STATUSES),
    ]
    if exclude_entry_id is not None:
        conditions.append(CatalogueEntry.id != exclude_entry_id)
    rows = session.execute(
        select(CatalogueEntry.business_key, CatalogueEntry.preferred_term).where(*conditions)
    ).all()
    return tuple(
        Collision(
            severity=CollisionSeverity.ERROR,
            term="",
            term_key=term_key,
            business_key=row.business_key,
            preferred_term=row.preferred_term,
        )
        for row in rows
    )


def _matching_synonyms(
    session: Session, *, term_key: str, exclude_entry_id: uuid.UUID | None
) -> tuple[Collision, ...]:
    """Other live entries carrying an active synonym matching `term_key`."""
    conditions = [
        Designation.term_key == term_key,
        Designation.status == str(DesignationStatus.ACTIVE),
        CatalogueEntry.status.in_(_LIVE_STATUSES),
    ]
    if exclude_entry_id is not None:
        conditions.append(Designation.entry_id != exclude_entry_id)
    rows = session.execute(
        select(CatalogueEntry.business_key, CatalogueEntry.preferred_term)
        .select_from(Designation)
        .join(CatalogueEntry, CatalogueEntry.id == Designation.entry_id)
        .where(*conditions)
    ).all()
    return tuple(
        Collision(
            severity=CollisionSeverity.ERROR,
            term="",
            term_key=term_key,
            business_key=row.business_key,
            preferred_term=row.preferred_term,
        )
        for row in rows
    )


def _fill_term(collisions: tuple[Collision, ...], term: str) -> tuple[Collision, ...]:
    """The queries above do not know the submitted surface form, so it is
    filled in here. `replace` keeps a field later added to `Collision` from
    being dropped."""
    return tuple(replace(c, term=term) for c in collisions)


def assert_no_error_collisions(
    session: Session,
    *,
    entry: CatalogueEntry | None,
    term: str,
    kind: CandidateKind,
) -> None:
    """The mandatory FR-05 error-severity gate. `term` must already be
    cleaned (`nptc.catalogue.term_hygiene.clean_term`); this function only
    derives its comparison key.

    Call before the row mutation is constructed, so a rejected save leaves
    no partial mutation or audit event.

    `entry` is the entry the term is being saved *to*, excluded from its own
    comparison, or `None` for `nptc.catalogue.entries.create_entry`, where it
    does not exist yet. A new, unflushed `entry` is flushed first: its `id`
    is otherwise `None` and the comparison would exclude nothing.

    `kind` says whether `term` is the entry's preferred term or a synonym; see
    `CandidateKind`.
    """
    exclude_entry_id: uuid.UUID | None = None
    if entry is not None:
        if not sa_inspect(entry).identity:
            session.flush()
        exclude_entry_id = entry.id

    key = collision_key(term)
    # Serialises the transactions contending for this key before either
    # reads its snapshot (module docstring, "Concurrency").
    session.execute(_ACQUIRE_COLLISION_LOCK_SQL, {"key": key})
    collisions = _matching_entries(session, term_key=key, exclude_entry_id=exclude_entry_id)
    if kind is CandidateKind.PREFERRED_TERM:
        collisions += _matching_synonyms(session, term_key=key, exclude_entry_id=exclude_entry_id)

    if collisions:
        raise DesignationCollisionError(_fill_term(collisions, term))


def warning_collisions(
    session: Session,
    *,
    entry: CatalogueEntry,
    terms: Sequence[str],
) -> tuple[Collision, ...]:
    """FR-05's warning-severity query: for each of `terms` (already-cleaned
    synonym surface forms), every other live entry carrying an active
    synonym under the same comparison key, excluding a key `entry` has
    acknowledged via `acknowledge_collision`.

    Never raises: a warning permits the save. The edit screen calls it before
    a save and when displaying an entry's synonyms.

    A new, unflushed `entry` is flushed first, as in
    `assert_no_error_collisions`: its `id` is otherwise `None`, which would
    find no acknowledgements whatever had been acknowledged."""
    if not sa_inspect(entry).identity:
        session.flush()

    acknowledged = set(
        session.execute(
            select(DesignationCollisionAcknowledgement.term_key).where(
                DesignationCollisionAcknowledgement.entry_id == entry.id
            )
        ).scalars()
    )

    found: list[Collision] = []
    for term in terms:
        key = collision_key(term)
        if key in acknowledged:
            continue
        found.extend(
            replace(m, severity=CollisionSeverity.WARNING, term=term)
            for m in _matching_synonyms(session, term_key=key, exclude_entry_id=entry.id)
        )
    return tuple(found)


def acknowledge_collision(
    session: Session,
    ctx: AuditContext,
    *,
    acknowledger: Principal,
    entry: CatalogueEntry,
    term_key: str,
    reason: str,
) -> tuple[DesignationCollisionAcknowledgement, bool]:
    """Records that `acknowledger` has seen and accepted the warning-severity
    collision on `entry` for `term_key` - FR-05's "resolvable to
    an acknowledged state so the same warning does not recur every save".
    Requires `Permission.VALIDATION_ACKNOWLEDGE` (FR-44) and raises
    `PermissionDeniedError` before anything is added to the session.

    Scoped to `entry`, not to `term_key` alone; see
    `DesignationCollisionAcknowledgement`'s module docstring for why a fourth
    entry joining the group still warns once, on its own save.

    Idempotent: a repeat returns the existing row rather than raising or
    writing a second no-change audit event, as `nptc.auth.grants.grant_role`
    does for a role already held. Returns `(acknowledgement, created)`;
    `created` is `False` for the repeat, and `acknowledgement.reason` is then
    the *stored* note, not necessarily the one just submitted.

    `term_key` is checked non-blank before anything else runs. This table's
    model has no `@validates` hook, so a blank one would otherwise reach its
    `CHECK` constraint as a `23514`, which `unique_violation_constraint` does
    not recognise, and re-raise as an unmapped 500. The router cannot produce
    a blank `term_key`; the guard covers a caller reaching this function
    directly.

    `reason` is validated before the idempotent-repeat lookup, so whether an
    invalid note is rejected does not depend on whether someone acknowledged
    this collision first.

    **No advisory lock here**, unlike `assert_no_error_collisions`. Two
    concurrent first acknowledgements both read "no existing row", and one
    hits the `UNIQUE` index at flush; that loser becomes
    `DesignationCollisionAcknowledgementConflictError` (409). Losing this
    race costs an occasional 409 on a rare double-click, not a false
    negative, so a retryable refusal is proportionate and a lock would add
    contention nobody needs.

    The function does not check that `term_key` is a live
    `warning_collisions` finding for `entry`. Acknowledging ahead of a
    warning only suppresses a warning that would otherwise fire, and a
    wrongly suppressed *warning* has no safety consequence of the kind a
    missed *error* collision has."""
    if not acknowledger.has(Permission.VALIDATION_ACKNOWLEDGE):
        raise PermissionDeniedError(
            f"permission {Permission.VALIDATION_ACKNOWLEDGE.value!r} is required"
        )

    if not term_key:
        raise TermCleaningError(
            "a term with no significant characters after comparison-key folding "
            "cannot be acknowledged (FR-63)"
        )
    validated_reason = validate_changelog_note(reason)

    # Read once up front: a failed flush expires every instance the session
    # tracks, so reading `entry.id` in the `except` block would reload against
    # a session not yet rolled back and raise `PendingRollbackError` in place
    # of the domain error.
    entry_id = entry.id

    existing = session.execute(
        select(DesignationCollisionAcknowledgement).where(
            DesignationCollisionAcknowledgement.entry_id == entry_id,
            DesignationCollisionAcknowledgement.term_key == term_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    acknowledgement = DesignationCollisionAcknowledgement(
        entry_id=entry_id,
        term_key=term_key,
        acknowledged_by_user_id=acknowledger.user_id,
        reason=validated_reason,
    )
    session.add(acknowledgement)
    try:
        record_change(
            session,
            ctx,
            action="designation_collision.acknowledged",
            instance=acknowledgement,
            kind=ChangeKind.CREATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        if unique_violation_constraint(exc) == _COLLISION_ACK_CONSTRAINT:
            raise DesignationCollisionAcknowledgementConflictError(
                f"entry {entry_id} was already acknowledged for "
                f"{term_key!r} by a concurrent request"
            ) from exc
        raise
    return acknowledgement, True
