"""The `code_binding` service layer (issue #48, FR-06, FR-08, FR-82, FR-83).

`fsn`/`au_preferred_term` are passed straight through, untouched - unlike
`nptc.catalogue.designations.add_designation`, there is no cleaning step to
re-export from here, because FR-82 forbids one. `code`, `edition_hint` and
`system` are all validated before a row is even constructed, and the most
common real conflict - a second active binding on one entry - is checked
before insert too, so every rejection here is a domain error the caller can
act on, never an opaque `IntegrityError` at flush. The database's own
constraints (`nptc_sctid_is_valid`, `ix_code_binding_one_active_per_entry`,
issue #48/ADR-0023) remain the actual invariants; this is the same
two-layer treatment `CatalogueEntry._validate_business_key_immutable`
already gets.

**Replacing a binding is a three-step sequence, not one call.**
`ix_code_binding_one_active_per_entry` means a successor cannot be
inserted active while its predecessor still is - so the only valid order
is retire the predecessor (`retire_binding`), create the successor
(`create_binding`), then link the two (`link_replacement`). There is no
single function that does all three, because the second step needs the
caller's own successor details (`code`/`fsn`/...) in between the other two,
and each step is independently auditable (NFR-08).

FR-84's subsumption check (every binding subsumed by `71388002`
\\|Procedure\\|) is deliberately **not** here - it is the FR-45 validation
sweep's own concern, layered on top of the rows this module creates, the
same relationship FR-05 collision detection (#49) has to `designations.py`.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.changelog import validate_changelog_note
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.code_binding import (
    SNOMED_CT_SYSTEM,
    CodeBinding,
    CodeBindingEditionHint,
    CodeBindingStatus,
)
from nptc_shared.sctid import SCTID

__all__ = [
    "CodeBindingAlreadyActiveError",
    "CodeBindingAlreadyRetiredError",
    "CodeBindingNotRetiredError",
    "InvalidCodeBindingEditionHintError",
    "InvalidCodeBindingSystemError",
    "create_binding",
    "link_replacement",
    "retire_binding",
]


class CodeBindingAlreadyRetiredError(ValueError):
    """Raised by `retire_binding` when the binding is already retired,
    rather than silently no-opping - mirrors
    `nptc.catalogue.designations.DesignationAlreadyRetiredError`. 409, not
    422: the request is well-formed, it just conflicts with the resource's
    current state."""

    http_status: ClassVar[int] = 409


class CodeBindingAlreadyActiveError(ValueError):
    """Raised by `create_binding` when `entry` already has an active
    binding (FR-08's "at most one active code binding") - the most common
    real conflict on this table, checked before insert so it surfaces as
    a domain error rather than `ix_code_binding_one_active_per_entry`'s
    raw `IntegrityError`. 409: well-formed request, conflicting state."""

    http_status: ClassVar[int] = 409


class CodeBindingNotRetiredError(ValueError):
    """Raised by `link_replacement` when `superseded` is not already
    retired - `ck_code_binding_replaced_by_requires_retired` is the actual
    database invariant; this is the fail-loud Python-level layer, and the
    reason `link_replacement` is its own step rather than a parameter on
    `retire_binding` (see the module docstring)."""

    http_status: ClassVar[int] = 409


class InvalidCodeBindingEditionHintError(ValueError):
    """Raised by `create_binding` when `edition_hint` is not one of
    `CodeBindingEditionHint`'s values - `ck_code_binding_edition_hint` is
    the actual database invariant; this is the fail-loud Python-level
    layer, checked before anything is added to the session."""

    http_status: ClassVar[int] = 422


class InvalidCodeBindingSystemError(ValueError):
    """Raised by `create_binding` when `system` is blank -
    `ck_code_binding_system_not_blank` is the actual database invariant;
    this is the fail-loud Python-level layer, checked before anything is
    added to the session."""

    http_status: ClassVar[int] = 422


def _validate_edition_hint(edition_hint: str) -> str:
    try:
        return str(CodeBindingEditionHint(edition_hint))
    except ValueError as exc:
        valid = ", ".join(repr(str(member)) for member in CodeBindingEditionHint)
        raise InvalidCodeBindingEditionHintError(
            f"{edition_hint!r} is not a valid edition hint - expected one of {valid}"
        ) from exc


def _validate_system(system: str) -> str:
    if not system.strip():
        raise InvalidCodeBindingSystemError("system cannot be blank")
    return system


def create_binding(
    session: Session,
    ctx: AuditContext,
    *,
    entry: CatalogueEntry,
    code: str,
    fsn: str,
    au_preferred_term: str | None = None,
    edition_hint: str = str(CodeBindingEditionHint.UNKNOWN),
    system: str = SNOMED_CT_SYSTEM,
    reason: str,
) -> CodeBinding:
    """Adds one active code binding to `entry`. `code`, `edition_hint` and
    `system` are all validated before the row is constructed, and `entry`
    is checked for an existing active binding first (FR-08) - every
    rejection here is a domain error raised before anything is added to
    the session, rather than an opaque `IntegrityError` at flush.
    `fsn`/`au_preferred_term` are stored exactly as passed (FR-82) - neither
    is cleaned, trimmed, or otherwise transformed."""
    validated_reason = validate_changelog_note(reason)
    validated_code = SCTID(code).value
    validated_edition_hint = _validate_edition_hint(edition_hint)
    validated_system = _validate_system(system)

    existing_active_id = session.execute(
        select(CodeBinding.id).where(
            CodeBinding.entry_id == entry.id,
            CodeBinding.status == str(CodeBindingStatus.ACTIVE),
        )
    ).scalar_one_or_none()
    if existing_active_id is not None:
        raise CodeBindingAlreadyActiveError(
            f"entry {entry.id} already has an active code binding ({existing_active_id})"
        )

    binding = CodeBinding(
        entry_id=entry.id,
        system=validated_system,
        code=validated_code,
        fsn=fsn,
        au_preferred_term=au_preferred_term,
        edition_hint=validated_edition_hint,
    )
    session.add(binding)
    record_change(
        session,
        ctx,
        action="code_binding.created",
        instance=binding,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
    return binding


def retire_binding(
    session: Session,
    ctx: AuditContext,
    *,
    binding: CodeBinding,
    reason: str,
) -> CodeBinding:
    """Retires `binding` via a `status` transition - never a `DELETE`
    (`nptc.db.roles.REVOKE_CODE_BINDING_DELETE_SQL` makes this a
    privilege-level guarantee). Raises `CodeBindingAlreadyRetiredError`
    rather than silently no-opping, mirroring
    `nptc.catalogue.designations.retire_designation`.

    Does not accept a successor - see the module docstring for why
    `ix_code_binding_one_active_per_entry` makes "retire with a successor
    already linked" an impossible first step, and use `link_replacement`
    once the successor exists instead."""
    if binding.status == str(CodeBindingStatus.RETIRED):
        raise CodeBindingAlreadyRetiredError(f"code binding {binding.id} is already retired")

    validated_reason = validate_changelog_note(reason)
    binding.status = str(CodeBindingStatus.RETIRED)
    binding.retirement_reason = validated_reason
    record_change(
        session,
        ctx,
        action="code_binding.retired",
        instance=binding,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return binding


def link_replacement(
    session: Session,
    ctx: AuditContext,
    *,
    superseded: CodeBinding,
    successor: CodeBinding,
    reason: str,
) -> CodeBinding:
    """The third step of a replacement (see the module docstring):
    populates `superseded.replaced_by_binding_id` once both `superseded`
    is already retired and `successor` has a real, flushed `id`.

    Raises `CodeBindingNotRetiredError` if `superseded` is not retired -
    `ck_code_binding_replaced_by_requires_retired` is the database
    invariant this pre-empts. Raises `ValueError` if `successor.id` is
    `None` (not yet flushed) - assigning it directly would otherwise
    silently write `NULL` into `replaced_by_binding_id`, since `CodeBinding.
    id` has no Python-side default, only `server_default=func.
    gen_random_uuid()`."""
    if superseded.status != str(CodeBindingStatus.RETIRED):
        raise CodeBindingNotRetiredError(
            f"code binding {superseded.id} must be retired before it can name a successor"
        )
    if successor.id is None:
        raise ValueError(
            "successor has not been flushed yet - its id is None, and linking now would "
            "silently write NULL into replaced_by_binding_id"
        )

    validated_reason = validate_changelog_note(reason)
    superseded.replaced_by_binding_id = successor.id
    record_change(
        session,
        ctx,
        action="code_binding.replacement_linked",
        instance=superseded,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return superseded
