"""The `code_binding` service layer (issue #48, FR-06, FR-08, FR-82, FR-83).

`fsn`/`au_preferred_term` are passed straight through, untouched - unlike
`nptc.catalogue.designations.add_designation`, there is no cleaning step to
re-export from here, because FR-82 forbids one. `code` is validated through
`nptc_shared.sctid.SCTID` before a row is even constructed, the same
fail-loud-before-mutation posture `add_designation` already uses for its
changelog note: a rejected code leaves nothing behind to roll back. The
database's own `nptc_sctid_is_valid` check (issue #48/ADR-0023) is the actual
invariant; this is the same two-layer treatment
`CatalogueEntry._validate_business_key_immutable` already gets.

FR-84's subsumption check (every binding subsumed by `71388002`
\\|Procedure\\|) is deliberately **not** here - it is the FR-45 validation
sweep's own concern, layered on top of the rows this module creates, the
same relationship FR-05 collision detection (#49) has to `designations.py`.
"""

from __future__ import annotations

from typing import ClassVar

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
    "CodeBindingAlreadyRetiredError",
    "create_binding",
    "retire_binding",
]


class CodeBindingAlreadyRetiredError(ValueError):
    """Raised by `retire_binding` when the binding is already retired,
    rather than silently no-opping - mirrors
    `nptc.catalogue.designations.DesignationAlreadyRetiredError`. 409, not
    422: the request is well-formed, it just conflicts with the resource's
    current state."""

    http_status: ClassVar[int] = 409


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
    """Adds one active code binding to `entry`. `code` is validated via
    `SCTID` before the row is constructed - a malformed or Verhoeff-failing
    code raises `InvalidSCTIDError` here, before anything is added to the
    session, rather than surfacing as an opaque `IntegrityError` at flush.
    `fsn`/`au_preferred_term` are stored exactly as passed (FR-82) - neither
    is cleaned, trimmed, or otherwise transformed."""
    validated_reason = validate_changelog_note(reason)
    validated_code = SCTID(code).value
    binding = CodeBinding(
        entry_id=entry.id,
        system=system,
        code=validated_code,
        fsn=fsn,
        au_preferred_term=au_preferred_term,
        edition_hint=edition_hint,
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
    replaced_by: CodeBinding | None = None,
) -> CodeBinding:
    """Retires `binding` via a `status` transition - never a `DELETE`
    (`nptc.db.roles.REVOKE_CODE_BINDING_DELETE_SQL` makes this a
    privilege-level guarantee). `replaced_by` is the FR-08 replacement
    case ("where a code is being replaced following inactivation") - pass
    it explicitly rather than defaulting into either reading; a binding
    withdrawn with no successor leaves `replaced_by_binding_id` `NULL`.
    Raises `CodeBindingAlreadyRetiredError` rather than silently no-opping,
    mirroring `nptc.catalogue.designations.retire_designation`."""
    if binding.status == str(CodeBindingStatus.RETIRED):
        raise CodeBindingAlreadyRetiredError(f"code binding {binding.id} is already retired")

    validated_reason = validate_changelog_note(reason)
    binding.status = str(CodeBindingStatus.RETIRED)
    binding.retirement_reason = validated_reason
    if replaced_by is not None:
        binding.replaced_by_binding_id = replaced_by.id
    record_change(
        session,
        ctx,
        action="code_binding.retired",
        instance=binding,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return binding
