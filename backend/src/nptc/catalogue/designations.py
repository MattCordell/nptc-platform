"""The `designation` service layer (issue #47, FR-04, FR-24, FR-37, FR-85).

`clean_term`/`preferred_term_length`/`TermCleaningError` are re-exported
from `nptc.catalogue.term_hygiene` for convenience - see that module's own
docstring for why the audit-free pieces had to move there rather than
live here: `nptc.db.models.designation.Designation` (and
`nptc.db.models.catalogue_entry.CatalogueEntry`) need them for their own
`@validates`/`length` hooks, and this module imports `nptc.audit.recording`
(which reaches back into `nptc.db.models` through `nptc.audit.writer`), so
a model importing *this* module directly would be circular.

FR-05 error-severity collision detection runs here, before every row is
constructed (`nptc.catalogue.collisions.assert_no_error_collisions`) - a
rejected save leaves no audit event, matching every other precondition
check in this package. Warning-severity ("the same synonym on multiple
entries") is deliberately **not** checked here: it never blocks a save,
so it is a query a caller (#149's edit screen) asks of
`nptc.catalogue.collisions.warning_collisions`, not a precondition this
module enforces.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.collisions import assert_no_error_collisions
from nptc.catalogue.term_hygiene import TermCleaningError, clean_term, preferred_term_length
from nptc.db.errors import unique_violation_constraint
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc_shared.language import DEFAULT_LANGUAGE
from nptc_shared.similarity import collision_key

__all__ = [
    "DesignationAlreadyRetiredError",
    "DesignationNotFoundError",
    "DuplicateActiveTermError",
    "PreferredDesignationAlreadyActiveError",
    "TermCleaningError",
    "add_designation",
    "add_synonyms",
    "amend_designation",
    "clean_term",
    "load_active_designation",
    "preferred_term_length",
    "retire_designation",
]

#: `ix_designation_no_duplicate_active_term`/`ix_designation_one_active_
#: preferred_per_entry_language`'s own literal names (`Index(...)` was
#: given an explicit name directly, so `NAMING_CONVENTION`'s "ix" pattern
#: never applies to either - see `nptc.db.models.designation`). Matched
#: against `unique_violation_constraint(exc)` the same way `nptc.catalogue.
#: bindings.create_binding` matches its own two constraint names, so a lost
#: race at flush becomes the same typed domain error the pre-insert check
#: below would have raised had it run a moment later.
_NO_DUPLICATE_ACTIVE_TERM_CONSTRAINT = "ix_designation_no_duplicate_active_term"
_ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT = (
    "ix_designation_one_active_preferred_per_entry_language"
)

if TYPE_CHECKING:
    # Import-time only - see the module docstring for why a runtime import
    # of `nptc.db.models.designation` here would be circular.
    # `from __future__ import annotations` makes every annotation below a
    # lazy string, so the type checker sees this without the interpreter
    # ever needing to resolve it at import time.
    from nptc.db.models.designation import Designation


class DesignationAlreadyRetiredError(ValueError):
    """Raised by `retire_designation` when `designation` is already
    `retired` - retiring twice would otherwise silently write a second
    `designation.retired` audit event with no actual state change, which
    reads as a real edit to anyone reviewing the audit log even though
    nothing changed.

    `http_status: ClassVar[int] = 409` - the same convention every other
    error class this issue adds carries (`TermCleaningError`,
    `DesignationLanguageError`, `ChangelogNoteError`), so
    `nptc.api.errors.register_exception_handlers` has a status to read
    rather than falling through to an unhandled 500 once #149/#150 give
    this a caller. 409, not 422: the request is well-formed, it just
    conflicts with the resource's current state - the same reasoning
    `EntryVersionConflictError` already applies."""

    http_status: ClassVar[int] = 409


class DesignationNotFoundError(LookupError):
    """Raised by `load_active_designation` when no *active* designation on
    `entry_id` matches `term`/`language` - the same `LookupError` +
    `http_status` convention `nptc.catalogue.bindings.
    CodeBindingNotFoundError` uses, so `nptc.api.errors.
    register_exception_handlers` has a status to read rather than falling
    through to an unhandled 500 once #224 gives this a caller.

    404, not 409: a term that was retired, or never added, is simply not
    addressable this way any more - not a conflicting state (matching
    `CodeBindingNotFoundError`'s own reasoning)."""

    http_status: ClassVar[int] = 404


class DuplicateActiveTermError(ValueError):
    """Raised when `add_designation`/`amend_designation` would produce a
    second active designation sharing `(entry_id, term_key, language)` -
    the same comparison-key fold `ix_designation_no_duplicate_active_term`
    (issue #49) enforces at the database. 409, not 422: the request is
    well-formed, it just conflicts with a term this entry already holds
    (the same reasoning `EntryVersionConflictError` already applies)."""

    http_status: ClassVar[int] = 409


class PreferredDesignationAlreadyActiveError(ValueError):
    """Raised when `add_designation` would give one entry a second active
    `use='preferred'` designation in the same language -
    `ix_designation_one_active_preferred_per_entry_language` (issue #47) is
    the database invariant this mirrors. Note the catalogue's own en-AU
    preferred term is never a `designation` row at all (ADR-0022,
    `ck_designation_no_en_au_preferred`); this only ever fires for a
    non-en-AU preferred variant."""

    http_status: ClassVar[int] = 409


def load_active_designation(
    session: Session,
    *,
    entry_id: uuid.UUID,
    term: str,
    language: str = DEFAULT_LANGUAGE,
) -> Designation:
    """Resolves an active designation from its public address:
    `(entry_id, term, language)` - the shape a caller addressing a
    designation by term in a request body (never a path segment or an
    internal id, since a term can contain `/`) actually has on hand.

    Looked up by *comparison key*, not the raw term:
    `ix_designation_no_duplicate_active_term` (issue #49) is itself keyed
    on `term_key`, so a caller naming a case/punctuation variant of the
    stored term still resolves the same row - matching what the collision
    check itself would consider a duplicate.

    `use` is deliberately not a filter parameter: the index above has no
    `use` column, so `(entry_id, term_key, language)` already identifies
    at most one *active* row regardless of use."""
    from nptc.db.models.designation import Designation as _Designation
    from nptc.db.models.designation import DesignationStatus

    key = collision_key(clean_term(term))
    designation = session.execute(
        select(_Designation).where(
            _Designation.entry_id == entry_id,
            _Designation.term_key == key,
            _Designation.language == language,
            _Designation.status == str(DesignationStatus.ACTIVE),
        )
    ).scalar_one_or_none()
    if designation is None:
        raise DesignationNotFoundError(
            f"entry {entry_id} has no active designation for term {term!r} in language {language!r}"
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
    """Adds one designation row to `entry`. `term` is cleaned by
    `Designation`'s own `@validates` hook, but cleaned again here first so
    FR-05's error-severity collision check
    (`nptc.catalogue.collisions.assert_no_error_collisions`) compares the
    same value that will actually be stored; `reason` is validated here,
    before the row is even constructed, so a rejected note (or a rejected
    collision) leaves nothing behind to roll back (matching `save_entry`'s
    precondition-before-mutation posture for FR-38).

    The error-severity collision check above narrows the race against a
    *different* entry's designations but cannot close a race against
    *this* entry's own: two concurrent adds of the same term on one entry
    both pass it and only one wins at insert. `record_change(kind=CREATED)`
    flushes the session itself (see its own docstring) - that flush is
    what actually hits the database and is where the loser's
    `IntegrityError` surfaces, so it is what this translates into
    `DuplicateActiveTermError`/`PreferredDesignationAlreadyActiveError`
    (issue #224), rather than reaching the caller as an unmapped 500.

    `entry_id` is read into a local before the flush, not re-read from
    `entry.id` inside the `except` block: a failed flush leaves every
    instance the session tracks expired, so touching an ORM attribute
    afterwards - even one already loaded - triggers a reload against a
    session that is not yet rolled back, raising `PendingRollbackError`
    in place of the domain error this is meant to raise (issue #224
    review)."""
    from nptc.db.models.designation import Designation

    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(term)
    assert_no_error_collisions(session, entry=entry, term=cleaned_term, language=language, use=use)
    entry_id = entry.id
    designation = Designation(entry_id=entry_id, term=cleaned_term, use=use, language=language)
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
                f"{cleaned_term!r} in language {language!r}"
            ) from exc
        if constraint_name == _ONE_ACTIVE_PREFERRED_PER_LANGUAGE_CONSTRAINT:
            raise PreferredDesignationAlreadyActiveError(
                f"entry {entry_id} already has an active preferred designation "
                f"in language {language!r}"
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
    """Adds each of `terms` as its own synonym row (FR-04) - the same
    changelog note covers the whole batch, validated once up front rather
    than once per row, since they are one edit from the caller's point of
    view.

    Deduplicates by *collision key* before inserting, not merely by the
    cleaned term: `ix_designation_no_duplicate_active_term` (issue #49) is
    itself keyed on `term_key`, so two terms that collapse to the same
    comparison key after `collision_key` (a case or punctuation variant,
    not only a whitespace one - e.g. `"ADA2"` and `"ada2"`) are one
    synonym, not two - inserting both would violate that index at flush
    with an unhelpful `IntegrityError`, from a batch the caller reasonably
    thinks is well-formed. FR-04's whole premise is cleaning up doubled-
    delimiter/whitespace-variant cells; this extends the same posture to
    the stronger FR-05 comparison fold.

    **Inserted in comparison-key order, not caller order.** Each call to
    `add_designation` below acquires `nptc.catalogue.collisions.
    assert_no_error_collisions`'s `pg_advisory_xact_lock` and holds it
    until this transaction commits - so a batch of N terms holds up to N
    locks at once. Two concurrent batches sharing two keys, acquired in
    opposite order (transaction A saving `["ADA2", "17-OHP"]`, transaction
    B saving `["17-OHP", "ADA2"]`), would otherwise each hold one lock and
    wait on the other - a genuine deadlock (Postgres `40P01`), not merely
    contention, and one this codebase has no handler for. Sorting the
    deduplicated terms by their own comparison key first makes acquisition
    order the same for every caller regardless of the order terms were
    submitted in, so two batches can only ever block on each other, never
    deadlock. The returned list is therefore ordered by comparison key,
    not by the order `terms` was given in - #149's caller should not rely
    on positional correspondence between `terms` and the return value."""
    validated_reason = validate_changelog_note(reason)
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
    """Retires `designation` via a `status` transition - never a `DELETE`
    (`nptc.db.roles.REVOKE_DESIGNATION_DELETE_SQL` makes this a privilege-
    level guarantee, matching `CatalogueEntry.status`'s own precedent for
    deprecation-not-deletion). Raises `DesignationAlreadyRetiredError`
    rather than silently no-opping - see that class's own docstring."""
    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.RETIRED):
        raise DesignationAlreadyRetiredError(f"designation {designation.id} is already retired")

    validated_reason = validate_changelog_note(reason)
    designation.status = str(DesignationStatus.RETIRED)
    record_change(
        session,
        ctx,
        action="designation.retired",
        instance=designation,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
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
    """Edits `designation.term` in place, re-running FR-05's error-severity
    collision check against the new value first - the same precondition-
    before-mutation posture `add_designation` uses. Chosen over retire-and-
    re-add (issue #224) so the row keeps its identity (`id`) and the audit
    log shows one `designation.amended` edit, not a retirement paired with
    an unrelated-looking creation.

    `entry_id` is immutable (`Designation`'s own `@validates` hook), so
    this can never reparent a designation - only ever change the term it
    holds. `entry` is required, not derived from `designation.entry_id`,
    so the collision check can exclude this entry's own other designations
    the same way `add_designation` does - the caller (already having
    resolved both via `load_entry_for_update`/`load_active_designation`)
    has them both on hand."""
    from nptc.db.models.designation import DesignationStatus

    if designation.status == str(DesignationStatus.RETIRED):
        raise DesignationAlreadyRetiredError(
            f"designation {designation.id} is retired and cannot be amended"
        )

    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(new_term)
    if cleaned_term == designation.term:
        # A no-op edit: nothing to check the term against itself for, and
        # a same-value "edit" audit event would misrepresent that nothing
        # changed - the same reasoning `retire_designation`'s guard against
        # a double retirement applies, just without needing its own
        # exception type (submitting the term a designation already holds
        # is not a caller mistake worth surfacing).
        return designation

    # Captured into locals before the flush below, not re-read from the
    # ORM instances inside `except`: a failed flush leaves every instance
    # the session tracks expired, so touching an already-loaded attribute
    # afterwards triggers a reload against a session that is not yet
    # rolled back, raising `PendingRollbackError` in place of the domain
    # error this is meant to raise (issue #224 review).
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
