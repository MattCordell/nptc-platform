"""FR-05 collision detection and FR-08's blocking-severity neighbour
(issue #49) - the module `nptc.catalogue.designations`, `nptc.catalogue.
entries` and `nptc.catalogue.bindings` all name as "not implemented here,
layered on top of the rows those modules create".

Three severities, three different postures:

- **Error** (this module's `assert_no_error_collisions`): a synonym that
  exactly matches another live entry's preferred term, or the symmetric
  case, a preferred term that matches another live entry's active
  synonym or preferred term. Raised before any row is constructed, by
  every write path in `designations.py`/`entries.py`, so a rejected save
  leaves no audit event - the same precondition-before-mutation posture
  `bindings.create_binding` already holds.
- **Warning** (`warning_collisions`): the same synonym on multiple live
  entries. Never raised - it is a query a caller (#149's edit screen)
  asks, and permits the save by construction. `acknowledge_collision`
  records the editorial decision that silences it for one entry.
- **Blocking** (`nptc.catalogue.bindings.CodeBindingCodeAlreadyBoundError`,
  not this module): one active SNOMED code cannot be bound to two
  entries at once. That check is unrepresentable at the database layer
  (`ix_code_binding_one_active_entry_per_code`) and pre-empted in
  `bindings.create_binding` itself, next to its sibling
  `CodeBindingAlreadyActiveError` - it has no acknowledgement path and
  nothing for this module to add.

**Candidate scope: `draft` and `active` entries, not only `active`.**
FR-05's own wording is "a different active entry", but a `deprecated`/
`withdrawn` entry never collides (PRD acceptance criterion), and a
`draft` entry colliding with another live entry is exactly the same
ordering hazard the moment either one is published - catching it at save
time, before publication, is strictly safer than FR-05's literal reading.
`_LIVE_STATUSES` is the one place this is spelled out.

**Why the comparison is keyed on the stored `term_key`/`preferred_term_key`
columns, never a per-call recomputation.** Both are written by the same
`@validates` hook that cleans the underlying term (`Designation._validate_
term`, `CatalogueEntry._validate_preferred_term`), so they can never drift
from what `nptc_shared.similarity.collision_key` would compute fresh - see
those models' own module docstrings. This module never calls
`collision_key` on anything already stored; only on the term a caller is
currently trying to save.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Permission
from nptc.catalogue.changelog import validate_changelog_note
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.designation import Designation, DesignationStatus, DesignationUse
from nptc.db.models.designation_collision_acknowledgement import (
    DesignationCollisionAcknowledgement,
)
from nptc_shared.language import DEFAULT_LANGUAGE
from nptc_shared.similarity import collision_key

__all__ = [
    "Collision",
    "CollisionSeverity",
    "DesignationCollisionError",
    "acknowledge_collision",
    "assert_no_error_collisions",
    "warning_collisions",
]

if TYPE_CHECKING:
    from nptc.auth.principal import Principal

#: A deprecated/withdrawn entry never collides (PRD A.5's own acceptance
#: criterion) - see the module docstring for why `draft` is nonetheless
#: included alongside `active`.
_LIVE_STATUSES = ("draft", "active")


class CollisionSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Collision:
    """One collision found against a live entry other than the one being
    saved. `business_key`/`preferred_term` name the *other*, colliding
    entry - its internal UUID is never exposed (NFR-04/NFR-26) - so a
    caller (#149's edit screen) can name the conflicting entry rather than
    show a bare 409/warning with nothing actionable in it."""

    severity: CollisionSeverity
    term: str
    term_key: str
    language: str
    business_key: str
    preferred_term: str
    #: 'preferred' | 'synonym' - which designation the *other* entry holds
    #: under this key. 'preferred' also covers a plain
    #: `catalogue_entry.preferred_term` match, which has no `Designation`
    #: row at all (ADR-0022).
    colliding_use: str


class DesignationCollisionError(ValueError):
    """Raised by `assert_no_error_collisions` - the same `http_status:
    ClassVar[int]` convention every other catalogue domain error in this
    package carries (`nptc.catalogue.errors.EntryVersionConflictError`,
    `nptc.catalogue.bindings.CodeBindingAlreadyActiveError`, ...), so
    `nptc.api.errors.register_exception_handlers` has a status to read
    rather than falling through to an unhandled 500. Carries every
    collision found, never just the first - a caller fixing one collision
    at a time only to discover a second on the next save is exactly the
    frustration a single, complete report avoids."""

    http_status: ClassVar[int] = 409

    def __init__(self, collisions: tuple[Collision, ...]) -> None:
        if not collisions:
            raise ValueError("DesignationCollisionError requires at least one collision")
        business_keys = ", ".join(c.business_key for c in collisions)
        super().__init__(
            f"{len(collisions)} error-severity collision(s) against {business_keys} (FR-05)"
        )
        self.collisions = collisions


def _matching_entries(
    session: Session, *, term_key: str, exclude_entry_id: uuid.UUID | None
) -> tuple[Collision, ...]:
    """Other live entries whose `preferred_term_key` equals `term_key` -
    the catalogue's own en-AU preferred term, which lives only on
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
            language=DEFAULT_LANGUAGE,
            business_key=row.business_key,
            preferred_term=row.preferred_term,
            colliding_use=str(DesignationUse.PREFERRED),
        )
        for row in rows
    )


def _matching_designations(
    session: Session,
    *,
    term_key: str,
    language: str,
    use: str,
    exclude_entry_id: uuid.UUID | None,
) -> tuple[Collision, ...]:
    """Other live entries carrying an active `designation` row of `use`
    matching `term_key`/`language`."""
    conditions = [
        Designation.term_key == term_key,
        Designation.language == language,
        Designation.use == use,
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
            language=language,
            business_key=row.business_key,
            preferred_term=row.preferred_term,
            colliding_use=use,
        )
        for row in rows
    )


def _fill_term(collisions: tuple[Collision, ...], term: str) -> tuple[Collision, ...]:
    """`_matching_entries`/`_matching_designations` don't know the
    submitted surface form - only the caller does - so it is filled in
    here rather than threaded through every query above."""
    return tuple(
        Collision(
            severity=c.severity,
            term=term,
            term_key=c.term_key,
            language=c.language,
            business_key=c.business_key,
            preferred_term=c.preferred_term,
            colliding_use=c.colliding_use,
        )
        for c in collisions
    )


def assert_no_error_collisions(
    session: Session,
    *,
    entry: CatalogueEntry | None,
    term: str,
    language: str,
    use: str,
) -> None:
    """The mandatory FR-05 error-severity gate. `term` must already be
    cleaned (`nptc.catalogue.term_hygiene.clean_term`) - this function
    only derives its comparison key, never cleans it itself, matching
    every other catalogue write path's "clean, then check" ordering.

    Call before the row/attribute mutation is constructed: every existing
    write path in this package treats a domain-error precondition as
    something to check before touching the session, so a rejected save
    never leaves a partial mutation or an audit event behind.

    `entry` is the entry the term is being saved *to* - excluded from its
    own comparison so an entry never collides with itself - or `None` for
    `nptc.catalogue.entries.create_entry`'s own path, where the entry does
    not exist yet and there is nothing to exclude. A brand-new,
    not-yet-flushed `entry` (no identity yet, matching `nptc.catalogue.
    bindings.create_binding`'s own precedent) is flushed here first, since
    otherwise its `id` is `None` client-side and every comparison would
    vacuously exclude nothing.

    `use` is `'preferred'` for `CatalogueEntry.preferred_term` (issue #46)
    or `Designation.use == 'preferred'` (a non-en-AU preferred variant,
    issue #47), `'synonym'` for a `Designation.use == 'synonym'` row.
    """
    exclude_entry_id: uuid.UUID | None = None
    if entry is not None:
        if not sa_inspect(entry).identity:
            session.flush()
        exclude_entry_id = entry.id

    key = collision_key(term)
    collisions: tuple[Collision, ...] = ()

    if use == str(DesignationUse.SYNONYM):
        if language == DEFAULT_LANGUAGE:
            collisions += _matching_entries(
                session, term_key=key, exclude_entry_id=exclude_entry_id
            )
        collisions += _matching_designations(
            session,
            term_key=key,
            language=language,
            use=str(DesignationUse.PREFERRED),
            exclude_entry_id=exclude_entry_id,
        )
    else:
        # `use == 'preferred'`. `CatalogueEntry.preferred_term` is always
        # en-AU (`ck_designation_no_en_au_preferred` forbids an en-AU
        # `Designation.use == 'preferred'` row from ever existing), so
        # `_matching_entries` only makes sense when `language ==
        # DEFAULT_LANGUAGE` - `entries.py`'s own write paths always pass
        # exactly that, but a non-en-AU preferred variant added via
        # `designations.py` (issue #47's own mi-NZ example) must not be
        # compared against an unrelated en-AU preferred term across
        # entries just because the two surface forms happen to fold to the
        # same key.
        if language == DEFAULT_LANGUAGE:
            collisions += _matching_entries(
                session, term_key=key, exclude_entry_id=exclude_entry_id
            )
        collisions += _matching_designations(
            session,
            term_key=key,
            language=language,
            use=str(DesignationUse.SYNONYM),
            exclude_entry_id=exclude_entry_id,
        )

    if collisions:
        raise DesignationCollisionError(_fill_term(collisions, term))


def warning_collisions(
    session: Session,
    *,
    entry: CatalogueEntry,
    terms: Sequence[str],
    language: str = DEFAULT_LANGUAGE,
) -> tuple[Collision, ...]:
    """FR-05's warning-severity query: for each of `terms` (already-cleaned
    synonym surface forms), every other live entry carrying an active
    synonym under the same comparison key and `language` - excluding a key
    `entry` has already acknowledged via `acknowledge_collision`.

    Never raises: a warning permits the save by construction. This is what
    #149's edit screen calls to render the warning banner, both before a
    save (on the terms about to be submitted) and when simply displaying
    an entry's existing synonyms.
    """
    acknowledged = {
        (row.term_key, row.language)
        for row in session.execute(
            select(
                DesignationCollisionAcknowledgement.term_key,
                DesignationCollisionAcknowledgement.language,
            ).where(DesignationCollisionAcknowledgement.entry_id == entry.id)
        ).all()
    }

    found: list[Collision] = []
    for term in terms:
        key = collision_key(term)
        if (key, language) in acknowledged:
            continue
        matches = _matching_designations(
            session,
            term_key=key,
            language=language,
            use=str(DesignationUse.SYNONYM),
            exclude_entry_id=entry.id,
        )
        if matches:
            found.extend(
                Collision(
                    severity=CollisionSeverity.WARNING,
                    term=term,
                    term_key=m.term_key,
                    language=m.language,
                    business_key=m.business_key,
                    preferred_term=m.preferred_term,
                    colliding_use=m.colliding_use,
                )
                for m in matches
            )
    return tuple(found)


def acknowledge_collision(
    session: Session,
    ctx: AuditContext,
    *,
    acknowledger: Principal,
    entry: CatalogueEntry,
    term_key: str,
    language: str,
    reason: str,
) -> DesignationCollisionAcknowledgement:
    """Records that `acknowledger` has seen and accepted the warning-
    severity collision on `entry` for `(term_key, language)` - FR-05's "it
    MUST be resolvable to an acknowledged state so the same warning does
    not recur every save". Requires `Permission.VALIDATION_ACKNOWLEDGE`
    (FR-44: checked against a permission, never a role name); raises
    `PermissionDeniedError` otherwise, before anything is added to the
    session, matching `nptc.auth.grants.grant_role`'s own service-layer
    permission-check precedent.

    Scoped to `entry`, not to `term_key` alone - see `Designation
    CollisionAcknowledgement`'s own module docstring for why a fourth
    entry later joining the group still warns once, on its own save."""
    if not acknowledger.has(Permission.VALIDATION_ACKNOWLEDGE):
        raise PermissionDeniedError(
            f"permission {Permission.VALIDATION_ACKNOWLEDGE.value!r} is required"
        )

    validated_reason = validate_changelog_note(reason)
    acknowledgement = DesignationCollisionAcknowledgement(
        entry_id=entry.id,
        term_key=term_key,
        language=language,
        acknowledged_by_user_id=acknowledger.user_id,
        reason=validated_reason,
    )
    session.add(acknowledgement)
    record_change(
        session,
        ctx,
        action="designation_collision.acknowledged",
        instance=acknowledgement,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
    return acknowledgement
