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

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.collisions import assert_no_error_collisions
from nptc.catalogue.term_hygiene import TermCleaningError, clean_term, preferred_term_length
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc_shared.language import DEFAULT_LANGUAGE
from nptc_shared.similarity import collision_key

__all__ = [
    "DesignationAlreadyRetiredError",
    "TermCleaningError",
    "add_designation",
    "add_synonyms",
    "clean_term",
    "preferred_term_length",
    "retire_designation",
]

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
    precondition-before-mutation posture for FR-38)."""
    from nptc.db.models.designation import Designation

    validated_reason = validate_changelog_note(reason)
    cleaned_term = clean_term(term)
    assert_no_error_collisions(session, entry=entry, term=cleaned_term, language=language, use=use)
    designation = Designation(entry_id=entry.id, term=cleaned_term, use=use, language=language)
    session.add(designation)
    record_change(
        session,
        ctx,
        action="designation.created",
        instance=designation,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
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
    the stronger FR-05 comparison fold."""
    validated_reason = validate_changelog_note(reason)
    seen: set[str] = set()
    deduplicated = []
    for term in terms:
        cleaned = clean_term(term)
        key = collision_key(cleaned)
        if key not in seen:
            seen.add(key)
            deduplicated.append(cleaned)
    return [
        add_designation(
            session,
            ctx,
            entry=entry,
            term=term,
            use="synonym",
            language=language,
            reason=validated_reason,
        )
        for term in deduplicated
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
