"""The `designation` service layer (FR-04, FR-24, FR-37, FR-85).

`clean_term`, `preferred_term_length` and `TermCleaningError` are re-exported
from `nptc.catalogue.term_hygiene`. They live there because the ORM models
need them for their `@validates` and `length` hooks, and a model importing this
module would be circular: this module imports `nptc.audit.recording`, which
reaches back into `nptc.db.models` through `nptc.audit.writer`.

FR-05 error-severity collision detection runs here, before any row is
constructed (`assert_no_error_collisions`), so a rejected save leaves no audit
event. Warning-severity collisions never block a save, so they are a query a
caller makes through `nptc.catalogue.collisions.warning_collisions`, not a
precondition enforced here.

Every writer validates its session-free inputs, then calls
`acquire_append_lock`, before any session-touching statement (ADR-0035;
`test_lock_ordering.py` pins it). The error classes carry `http_status` for
`nptc.api.errors`: 409 where a well-formed request conflicts with current
state, 404 where an address resolves to nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.collisions import assert_no_error_collisions
from nptc.catalogue.term_hygiene import (
    TermCleaningError,
    clean_term,
    preferred_term_length,
    validate_language_tag,
)
from nptc.db.errors import unique_violation_constraint
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc_shared.language import DEFAULT_LANGUAGE
from nptc_shared.similarity import collision_key

__all__ = [
    "DesignationAlreadyRetiredError",
    "DesignationNotFoundError",
    "DesignationNotRetiredError",
    "DuplicateActiveTermError",
    "PreferredDesignationAlreadyActiveError",
    "TermCleaningError",
    "add_designation",
    "add_synonyms",
    "amend_designation",
    "clean_term",
    "find_active_designation",
    "find_retired_designation",
    "load_active_designation",
    "load_retired_designation",
    "preferred_term_length",
    "reinstate_designation",
    "retire_designation",
]

#: The explicit names given to `Index(...)` in `nptc.db.models.designation`, so
#: `NAMING_CONVENTION` never renames them. Matched against
#: `unique_violation_constraint(exc)` as `create_binding` does, so a race lost
#: at flush becomes the domain error the pre-insert check would have raised.
_NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT = "ix_designation_no_duplicate_active_term"
_ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT = (
    "ix_designation_one_active_preferred_per_entry_language"
)

if TYPE_CHECKING:
    # Annotation-only: a runtime import would be circular (see the module
    # docstring), and `from __future__ import annotations` keeps annotations lazy.
    from nptc.db.models.designation import Designation


class DesignationAlreadyRetiredError(ValueError):
    """Raised by `retire_designation` when `designation` is already `retired`.
    Retiring twice would write a second `designation.retired` audit event with no
    state change, which reads as a real edit in the audit log."""

    http_status: ClassVar[int] = 409


class DesignationNotRetiredError(ValueError):
    """Raised by `reinstate_designation` when the designation is not retired.
    An active row would reach `record_change` with an empty diff and raise the
    internal `AuditNoOpError` instead of a domain error.

    Also covers the address-level case. A term retired and then re-added creates
    a new active row with the same `term_key`. The original stays retired, but
    `(entry_id, term_key, language)` already has an active designation, so there
    is nothing to reinstate. The route checks this before resolving a retired row,
    so both cases answer with this type."""

    http_status: ClassVar[int] = 409


class DesignationNotFoundError(LookupError):
    """Raised by `load_active_designation` when no active designation on
    `entry_id` matches `term`/`language`. A term that was retired or never added
    is not addressable this way, which is not a conflicting state (as for
    `CodeBindingNotFoundError`)."""

    http_status: ClassVar[int] = 404


class DuplicateActiveTermError(ValueError):
    """Raised when `add_designation`, `amend_designation` or
    `reinstate_designation` would produce a second active designation sharing
    `(entry_id, term_key, language)`, the comparison-key fold that
    `ix_designation_no_duplicate_active_term` enforces in the database."""

    http_status: ClassVar[int] = 409


class PreferredDesignationAlreadyActiveError(ValueError):
    """Raised when `add_designation` or `reinstate_designation` would give one
    entry a second active `use='preferred'` designation in the same language, as
    `ix_designation_one_active_preferred_per_entry_language` forbids. The
    catalogue's en-AU preferred term is never a `designation` row (ADR-0022,
    `ck_designation_no_en_au_preferred`), so this only fires for a non-en-AU
    preferred variant."""

    http_status: ClassVar[int] = 409


def find_active_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation | None:
    """`load_active_designation` without the refusal: `None` where that function
    would raise `DesignationNotFoundError`. It is the one query both share.

    Exists for a caller that needs "is there one?" as a question. The amendment
    route is one: the catalogue's en-AU preferred term lives on
    `catalogue_entry.preferred_term`, never a `designation` row (ADR-0022), so the
    route must tell "no such designation, but this is the entry's preferred term"
    from "no such designation at all". Catching `DesignationNotFoundError` would
    put a `try`/`except` in a route body, which
    `nptc.api.routers.catalogue_designations`' module docstring forbids.
    """
    from nptc.db.models.designation import Designation as _Designation
    from nptc.db.models.designation import DesignationStatus

    key = collision_key(clean_term(term))
    canonical_language = validate_language_tag(language)
    return session.execute(
        select(_Designation).where(
            _Designation.entry_id == entry_id,
            _Designation.term_key == key,
            _Designation.language == canonical_language,
            _Designation.status == str(DesignationStatus.ACTIVE),
        )
    ).scalar_one_or_none()


def load_active_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation:
    """Resolves an active designation from its public address,
    `(entry_id, term, language)`. A request body supplies the term, never a path
    segment or an internal id, since a term can contain `/`.

    Looked up by comparison key, not the raw term, because
    `ix_designation_no_duplicate_active_term` is keyed on `term_key`: a case or
    punctuation variant resolves the same row the collision check would call a
    duplicate.

    `use` is not a filter: that index has no `use` column, so
    `(entry_id, term_key, language)` identifies at most one active row.

    `language` is canonicalised first (`validate_language_tag`), as
    `Designation`'s `@validates` hook did when the row was written, so `en-au`
    resolves a row stored as `en-AU`."""
    designation = find_active_designation(session, entry_id=entry_id, term=term, language=language)
    if designation is None:
        canonical_language = validate_language_tag(language)
        raise DesignationNotFoundError(
            f"entry {entry_id} has no active designation for term {term!r} "
            f"in language {canonical_language!r}"
        )
    return designation


def find_retired_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation | None:
    """The retired-row sibling of `find_active_designation`. The active-only query
    hardcodes `status == 'active'`, so it cannot resolve the row a reinstatement
    acts on.

    Several retired rows can share `(entry_id, term_key, language)`: a term
    added, retired and re-added repeatedly leaves one per cycle, because
    `ix_designation_no_duplicate_active_term` is active-only. The ordering picks
    the row an editor expects:

    - `retired_at DESC` picks the most recently retired.
    - `created_at DESC` breaks a tie from one transaction. Postgres `now()` is
      transaction time, so a bulk retirement ties on `retired_at`. Not `id`: a
      UUID carries no chronological meaning.
    - `id ASC` is the last tiebreaker, because `created_at` is also transaction
      time and a full add/retire cycle repeated in one transaction ties on both.
      Two identical requests must never disagree."""
    from nptc.db.models.designation import Designation as _Designation
    from nptc.db.models.designation import DesignationStatus

    key = collision_key(clean_term(term))
    canonical_language = validate_language_tag(language)
    return session.execute(
        select(_Designation)
        .where(
            _Designation.entry_id == entry_id,
            _Designation.term_key == key,
            _Designation.language == canonical_language,
            _Designation.status == str(DesignationStatus.RETIRED),
        )
        .order_by(
            _Designation.retired_at.desc(),
            _Designation.created_at.desc(),
            _Designation.id.asc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def load_retired_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation:
    """Resolves the most recently retired designation from its public address, or
    raises `DesignationNotFoundError` (404), reused from `load_active_designation`:
    a term never retired on this entry is not addressable this way.

    Does not check for an active designation at the same address. That is a
    different outcome (`DesignationNotRetiredError`, 409), and the route checks it
    before calling this function, so a 404 is never returned for an address that
    already has an active row."""
    designation = find_retired_designation(session, entry_id=entry_id, term=term, language=language)
    if designation is None:
        canonical_language = validate_language_tag(language)
        raise DesignationNotFoundError(
            f"entry {entry_id} has no retired designation for term {term!r} "
            f"in language {canonical_language!r}"
        )
    return designation


def add_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    term: str,
    use: str = "synonym",
    language: str = DEFAULT_LANGUAGE,
    reason: str,
) -> Designation:
    """Adds one designation row to `entry`. `term` is cleaned here as well as by
    `Designation`'s `@validates` hook, so FR-05's collision check compares the
    value that will be stored. `language` is canonicalised before the check for
    the same reason: `assert_no_error_collisions` branches on
    `language == DEFAULT_LANGUAGE`, and `en-au` would take the wrong path.

    `reason`, `term` and `language` are validated before `acquire_append_lock`;
    none touches the session, so a rejected input takes no lock. Callers are
    wrapped in `entry_child_write`, which already holds the lock, so the call here
    is a cheap re-assertion. It stays because a direct caller would otherwise take
    the collision lock before the append lock, the reverse of what an
    `entry_child_write` caller does (ADR-0035).

    The collision check narrows the race against other entries' designations but
    not against this entry's own: two concurrent adds of one term both pass it and
    one loses at insert. `record_change(kind=CREATED)` flushes, which is where the
    loser's `IntegrityError` surfaces. It is translated into
    `DuplicateActiveTermError` or `PreferredDesignationAlreadyActiveError` rather
    than reaching the caller as a 500.

    `entry_id` is read into a local before the flush. A failed flush expires every
    tracked instance, so touching `entry.id` in the `except` block would reload
    against a session pending rollback and raise `PendingRollbackError` in place
    of the domain error."""
    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(term)
    canonical_language = validate_language_tag(language)
    acquire_append_lock(session)

    from nptc.db.models.designation import Designation

    assert_no_error_collisions(
        session, entry=entry, term=cleaned_term, language=canonical_language, use=use
    )
    entry_id = entry.id
    designation = Designation(
        entry_id=entry_id, term=cleaned_term, use=use, language=canonical_language
    )
    session.add(designation)
    try:
        record_change(
            session,
            ctx,
            action="designation.created",
            instance=designation,
            kind=ChangeKind.CREATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT:
            raise DuplicateActiveTermError(
                f"entry {entry_id} already has an active designation for term "
                f"{cleaned_term!r} in language {canonical_language!r}"
            ) from exc
        if constraint_name == _ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT:
            raise PreferredDesignationAlreadyActiveError(
                f"entry {entry_id} already has an active preferred designation "
                f"in language {canonical_language!r}"
            ) from exc
        raise
    return designation


def add_synonyms(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    terms: Sequence[str],
    language: str = DEFAULT_LANGUAGE,
    reason: str,
) -> list[Designation]:
    """Adds each of `terms` as its own synonym row (FR-04). One changelog note
    covers the batch and is validated once up front.

    Deduplicates by collision key, not by cleaned term. Terms that fold to the
    same key (`"ADA2"` and `"ada2"`) are one synonym; inserting both would violate
    `ix_designation_no_duplicate_active_term` at flush with an unhelpful
    `IntegrityError`.

    **Inserted in comparison-key order, not caller order.** Each `add_designation`
    call takes `assert_no_error_collisions`' advisory lock and holds it to commit,
    so a batch of N terms holds up to N locks. Two batches sharing two keys in
    opposite order (`["ADA2", "17-OHP"]` and `["17-OHP", "ADA2"]`) would each hold
    one lock and wait on the other: a deadlock (Postgres `40P01`), for which this
    codebase has no handler. Sorting by comparison key gives every caller the same
    acquisition order, so batches can block but never deadlock. The returned list
    is therefore in comparison-key order; callers must not rely on positional
    correspondence with `terms`.

    `add_designation` re-asserts the append lock for each term. The call here
    stays so `test_lock_ordering.py`'s guard sees the lock ahead of the loop
    without reasoning through it."""
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)
    seen: set[str] = set()
    deduplicated: list[tuple[str, str]] = []
    for term in terms:
        cleaned = clean_term(term)
        key = collision_key(cleaned)
        if key not in seen:
            seen.add(key)
            deduplicated.append((key, cleaned))
    deduplicated.sort(key=lambda pair: pair[0])
    return [
        add_designation(
            session,
            ctx,
            entry=entry,
            term=cleaned,
            use="synonym",
            language=language,
            reason=validated_reason,
        )
        for _key, cleaned in deduplicated
    ]


def retire_designation(
    session: Session,
    ctx: AuditContext,
    *,
    designation: Designation,
    reason: str,
) -> Designation:
    """Retires `designation` via a `status` transition, never a `DELETE`
    (`REVOKE_DESIGNATION_DELETE_SQL` makes that a privilege-level guarantee, as
    `CatalogueEntry.status` does for deprecation). Raises
    `DesignationAlreadyRetiredError` instead of silently doing nothing.

    `reason` is validated first, so a rejected note takes no lock and is reported
    ahead of an already-retired refusal. This function takes no other lock, so
    there is no deadlock to close here; the append lock is taken anyway so
    `test_lock_ordering.py` needs no per-function exception.

    Sets `retired_at` with `func.now()`, the database's clock, so it orders
    correctly across app instances; `retire_binding` does the same."""
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)

    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.RETIRED):
        raise DesignationAlreadyRetiredError(f"designation {designation.id} is already retired")

    designation.status = str(DesignationStatus.RETIRED)
    designation.retired_at = func.now()
    record_change(
        session,
        ctx,
        action="designation.retired",
        instance=designation,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return designation


def reinstate_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    designation: Designation,
    reason: str,
) -> Designation:
    """Reinstates `designation` via a `status` transition back to active. The row
    keeps its `id`, so `nptc.catalogue.history.load_history` reads one continuous
    record across create, retire and reinstate, instead of orphaned history plus
    an unrelated new row, which re-adding the term produces.

    Shaped like `add_designation`, not `retire_designation`. Retiring can never
    violate a partial unique index; reinstating can violate either
    `ix_designation_no_duplicate_active_term` or
    `ix_designation_one_active_preferred_per_entry_language`. So it runs FR-05's
    collision check and the same `IntegrityError` translation.

    `entry` is required, unlike `retire_designation`, to give
    `assert_no_error_collisions` the entry to exclude, as `amend_designation`
    does.

    Guards on `designation.status` before mutating, as `retire_designation` does
    for already-retired: an active row would reach `record_change` with an empty
    diff and raise `AuditNoOpError`. The route also checks at the address level
    (see `DesignationNotRetiredError`), so only a direct caller reaches this guard.

    `entry_id`, `cleaned_term` and `canonical_language` are captured before the
    flush, for the reason in `add_designation`. `reason` is validated first, then
    the append lock is taken."""
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)

    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.ACTIVE):
        raise DesignationNotRetiredError(f"designation {designation.id} is already active")

    entry_id = entry.id
    cleaned_term = designation.term
    canonical_language = designation.language
    assert_no_error_collisions(
        session, entry=entry, term=cleaned_term, language=canonical_language, use=designation.use
    )
    designation.status = str(DesignationStatus.ACTIVE)
    designation.retired_at = None
    try:
        record_change(
            session,
            ctx,
            action="designation.reinstated",
            instance=designation,
            kind=ChangeKind.UPDATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT:
            raise DuplicateActiveTermError(
                f"entry {entry_id} already has an active designation for term "
                f"{cleaned_term!r} in language {canonical_language!r}"
            ) from exc
        if constraint_name == _ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT:
            raise PreferredDesignationAlreadyActiveError(
                f"entry {entry_id} already has an active preferred designation "
                f"in language {canonical_language!r}"
            ) from exc
        raise
    return designation


def amend_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    designation: Designation,
    new_term: str,
    reason: str,
) -> Designation:
    """Edits `designation.term` in place, re-running FR-05's collision check on the
    new value first. Chosen over retire-and-re-add so the row keeps its `id` and
    the audit log shows one `designation.amended` edit, not a retirement beside an
    unrelated-looking creation.

    `entry_id` is immutable (`Designation`'s `@validates` hook), so this never
    reparents a designation. `entry` is required, not derived from
    `designation.entry_id`, so the collision check can exclude this entry's own
    other designations as `add_designation` does.

    `reason` and `new_term` are validated first, so a rejected input takes no
    lock; then the append lock is taken."""
    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(new_term)
    acquire_append_lock(session)

    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.RETIRED):
        raise DesignationAlreadyRetiredError(
            f"designation {designation.id} is retired and cannot be amended"
        )

    if cleaned_term == designation.term:
        # A no-op edit would audit a change that did not happen. It is not an
        # error: resubmitting the term a designation holds is not a caller mistake.
        return designation

    # Captured before the flush; see `add_designation`.
    entry_id = entry.id
    designation_language = designation.language
    assert_no_error_collisions(
        session,
        entry=entry,
        term=cleaned_term,
        language=designation_language,
        use=designation.use,
    )
    designation.term = cleaned_term
    try:
        record_change(
            session,
            ctx,
            action="designation.amended",
            instance=designation,
            kind=ChangeKind.UPDATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT:
            raise DuplicateActiveTermError(
                f"entry {entry_id} already has an active designation for term "
                f"{cleaned_term!r} in language {designation_language!r}"
            ) from exc
        raise
    return designation
