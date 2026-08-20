"""The `designation` service layer (issue #47, FR-04, FR-24, FR-37, FR-85).

`clean_designation_term`/`preferred_term_length`/`DesignationTermError` are
re-exported from `nptc.catalogue.designation_term` for convenience - see
that module's own docstring for why the audit-free pieces had to move
there rather than live here: `nptc.db.models.designation.Designation`
needs them for its own `@validates`/`length` hooks, and this module
imports `nptc.audit.recording` (which reaches back into
`nptc.db.models` through `nptc.audit.writer`), so the model importing
*this* module directly would be circular.

FR-05 collision detection (a synonym matching another active entry's
preferred term, or the same synonym on multiple entries) is deliberately
**not** here - it is issue #49's own module, layered on top of the rows
this one creates.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.designation_term import (
    DesignationTermError,
    clean_designation_term,
    preferred_term_length,
)
from nptc.db.models.catalogue_entry import CatalogueEntry

__all__ = [
    "DesignationTermError",
    "add_designation",
    "add_synonyms",
    "clean_designation_term",
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


def add_designation(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    term: str,
    use: str = "synonym",
    language: str = "en-AU",
    reason: str,
) -> Designation:
    """Adds one designation row to `entry`. `term` is cleaned by
    `Designation`'s own `@validates` hook; `reason` is validated here,
    before the row is even constructed, so a rejected note leaves nothing
    behind to roll back (matching `save_entry`'s precondition-before-
    mutation posture for FR-38)."""
    from nptc.db.models.designation import Designation

    validated_reason = validate_changelog_note(reason)
    designation = Designation(entry_id=entry.id, term=term, use=use, language=language)
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
    language: str = "en-AU",
    reason: str,
) -> list[Designation]:
    """Adds each of `terms` as its own synonym row (FR-04) - the same
    changelog note covers the whole batch, validated once up front rather
    than once per row, since they are one edit from the caller's point of
    view."""
    validated_reason = validate_changelog_note(reason)
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
        for term in terms
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
    deprecation-not-deletion)."""
    from nptc.db.models.designation import DesignationStatus

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
