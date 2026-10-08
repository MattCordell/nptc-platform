"""The `code_binding` service layer (FR-06, FR-08, FR-82, FR-83).

`fsn` and `au_preferred_term` are passed through untouched: FR-82 forbids a
cleaning step, unlike `nptc.catalogue.designations.add_designation`. `code`,
`edition_hint` and `system` are validated before a row is constructed, and a
second active binding on one entry is checked before insert too, so every
rejection is a domain error the caller can act on, never an opaque
`IntegrityError` at flush. The database constraints (`nptc_sctid_is_valid`,
`ix_code_binding_one_active_per_entry`; ADR-0023) remain the actual
invariants; this module is the Python-level layer over them, as
`CatalogueEntry._validate_business_key_immutable` is for the entry.

**Replacing a binding is a three-step sequence, not one call.**
`ix_code_binding_one_active_per_entry` forbids inserting a successor active
while its predecessor still is, so the only valid order is `retire_binding`,
then `create_binding`, then `link_replacement`. No single function does all
three: the second step needs the caller's successor details between the
other two, and each step is independently auditable (NFR-08).

FR-84's subsumption check (every binding subsumed by `71388002`
\\|Procedure\\|) is not here. It belongs to the FR-45 validation sweep,
layered on the rows this module creates, as FR-05 collision detection is on
`designations.py`.

**Blocking severity (FR-08): one active binding per code.** `create_binding`
also checks the code side (`ix_code_binding_one_active_entry_per_code`), the
same code active on a *different* entry, as its own domain error. Unlike
FR-05's error and warning pair, it has no acknowledgement path.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.changelog import validate_changelog_note
from nptc.db.errors import unique_violation_constraint
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
    "CodeBindingCodeAlreadyBoundError",
    "CodeBindingNotFoundError",
    "CodeBindingNotRetiredError",
    "CodeBindingSelfSupersessionError",
    "CodeBindingWriteNotFoundError",
    "InvalidCodeBindingEditionHintError",
    "InvalidCodeBindingSystemError",
    "create_binding",
    "link_replacement",
    "load_active_binding",
    "retire_binding",
]

#: The two partial unique indexes `create_binding` pre-checks before insert.
#: A pre-check narrows the race but cannot close it: two concurrent inserts
#: can both pass it and only one wins at flush. The names let the flush-time
#: fallback map the same constraint to the domain error the pre-check raises
#: (`nptc.auth.identity._is_username_collision` is the precedent; both use
#: `nptc.db.errors.unique_violation_constraint`).
#: `test_race_translation_constraint_names_match_the_actual_indexes` in
#: `test_catalogue_bindings.py` pins these literals against
#: `CodeBinding.__table_args__`.
_ONE_ACTIVE_PER_ENTRY_CONSTRAINT = "ix_code_binding_one_active_per_entry"
_ONE_ACTIVE_PER_CODE_CONSTRAINT = "ix_code_binding_one_active_entry_per_code"


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


class CodeBindingCodeAlreadyBoundError(ValueError):
    """Raised by `create_binding` when `code` is already actively bound to
    a *different* entry - FR-08's blocking severity, the code-side half of
    "one active binding" that `CodeBindingAlreadyActiveError` does not
    cover. Checked before insert so it surfaces as a domain error rather
    than `ix_code_binding_one_active_entry_per_code`'s raw `IntegrityError`.
    409: well-formed request, conflicting state."""

    http_status: ClassVar[int] = 409


class CodeBindingNotRetiredError(ValueError):
    """Raised by `link_replacement` when `superseded` is not already retired:
    the Python-level layer over `ck_code_binding_replaced_by_requires_retired`,
    and the reason `link_replacement` is its own step rather than a parameter
    on `retire_binding` (see the module docstring)."""

    http_status: ClassVar[int] = 409


class CodeBindingSelfSupersessionError(ValueError):
    """Raised by `link_replacement` when `successor is superseded`: the
    Python-level layer over `ck_code_binding_no_self_supersession`, checked
    before anything is assigned."""

    http_status: ClassVar[int] = 409


class InvalidCodeBindingEditionHintError(ValueError):
    """Raised by `create_binding` when `edition_hint` is not one of
    `CodeBindingEditionHint`'s values: the Python-level layer over
    `ck_code_binding_edition_hint`."""

    http_status: ClassVar[int] = 422


class CodeBindingNotFoundError(LookupError):
    """Raised by `load_active_binding` when `entry` has no *active* binding
    for `code`. The write routes address a binding this way because the public
    `Binding` model carries no id. A retired binding is not addressable: a
    caller retiring or replacing by code means the one in force, and
    `ix_code_binding_one_active_entry_per_code` guarantees at most one row
    matches. 404, as for `nptc.catalogue.errors.EntryNotFoundError`."""

    http_status: ClassVar[int] = 404


class CodeBindingWriteNotFoundError(LookupError):
    """Raised when a route re-reads a binding it just wrote (by `id`, through
    `nptc.catalogue.queries.load_bindings`) and the row is not there. Distinct
    from `CodeBindingNotFoundError`, which reports a caller's own path
    parameter not resolving: this reports the write path's invariant, "the row
    this function just flushed exists", failing. That is a platform bug, so
    500, mapped through `nptc.api.errors` like every other handled exception
    rather than surfacing as an unhandled `AssertionError`."""

    http_status: ClassVar[int] = 500


class InvalidCodeBindingSystemError(ValueError):
    """Raised by `create_binding` when `system` is blank: the Python-level
    layer over `ck_code_binding_system_not_blank`."""

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


def load_active_binding(session: Session, *, entry_id: uuid.UUID, code: str) -> CodeBinding:
    """The entry's *active* binding for `code`, or `CodeBindingNotFoundError`.

    The write routes resolve a binding from a path parameter, which no other
    function here does: they all take an already-loaded `CodeBinding` or
    `CatalogueEntry`. Scoped to `status == 'active'`; see the exception's
    docstring for why a retired binding is not a valid target."""
    binding = session.execute(
        select(CodeBinding).where(
            CodeBinding.entry_id == entry_id,
            CodeBinding.code == code,
            CodeBinding.status == str(CodeBindingStatus.ACTIVE),
        )
    ).scalar_one_or_none()
    if binding is None:
        raise CodeBindingNotFoundError(
            f"entry {entry_id} has no active code binding for code {code!r}"
        )
    return binding


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
    """Adds one active code binding to `entry`. Every rejection is a domain
    error raised before anything is added to the session (module docstring).
    `fsn` and `au_preferred_term` are stored exactly as passed (FR-82)."""
    validated_reason = validate_changelog_note(reason)
    validated_code = SCTID(code).value
    validated_edition_hint = _validate_edition_hint(edition_hint)
    validated_system = _validate_system(system)
    acquire_append_lock(session)

    # A new, unflushed `entry` has no identity, so its `id` would be baked
    # into the `where(...)` below as a stale value and the check would find
    # nothing. Flush first, only when needed. `sa_inspect(...).identity`
    # rather than `entry.id is None`, which mypy flags as unreachable because
    # `Mapped[uuid.UUID]` is non-optional.
    if not sa_inspect(entry).identity:
        session.flush()

    # Read once up front: a failed flush expires every instance the session
    # tracks, so reading `entry.id` in the `except` block would reload against
    # a session not yet rolled back and raise `PendingRollbackError` in place
    # of the domain error.
    entry_id = entry.id

    existing_active_id = session.execute(
        select(CodeBinding.id).where(
            CodeBinding.entry_id == entry_id,
            CodeBinding.status == str(CodeBindingStatus.ACTIVE),
        )
    ).scalar_one_or_none()
    if existing_active_id is not None:
        raise CodeBindingAlreadyActiveError(
            f"entry {entry_id} already has an active code binding ({existing_active_id})"
        )

    # The code side of "one active binding"; the entry side is checked above.
    already_bound_entry_id = session.execute(
        select(CodeBinding.entry_id).where(
            CodeBinding.system == validated_system,
            CodeBinding.code == validated_code,
            CodeBinding.status == str(CodeBindingStatus.ACTIVE),
        )
    ).scalar_one_or_none()
    if already_bound_entry_id is not None:
        raise CodeBindingCodeAlreadyBoundError(
            f"code {validated_code!r} on {validated_system!r} is already actively bound "
            f"to entry {already_bound_entry_id}"
        )

    binding = CodeBinding(
        entry_id=entry_id,
        system=validated_system,
        code=validated_code,
        fsn=fsn,
        au_preferred_term=au_preferred_term,
        edition_hint=validated_edition_hint,
    )
    session.add(binding)
    # The pre-checks narrow the race but cannot close it. `record_change`
    # flushes, and that flush is where the loser's `IntegrityError` surfaces,
    # so it is translated here.
    try:
        record_change(
            session,
            ctx,
            action="code_binding.created",
            instance=binding,
            kind=ChangeKind.CREATED,
            reason=validated_reason,
        )
    except IntegrityError as exc:
        constraint_name = unique_violation_constraint(exc)
        if constraint_name == _ONE_ACTIVE_PER_ENTRY_CONSTRAINT:
            raise CodeBindingAlreadyActiveError(
                f"entry {entry_id} already has an active code binding"
            ) from exc
        if constraint_name == _ONE_ACTIVE_PER_CODE_CONSTRAINT:
            raise CodeBindingCodeAlreadyBoundError(
                f"code {validated_code!r} on {validated_system!r} is already actively bound "
                "to another entry"
            ) from exc
        raise
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
    rather than silently no-opping, as
    `nptc.catalogue.designations.retire_designation` does.

    Does not accept a successor: `ix_code_binding_one_active_per_entry` makes
    retiring with a successor already linked an impossible first step (module
    docstring). Use `link_replacement` once the successor exists.

    Sets `retired_at` (FR-17; `ck_code_binding_retired_at` requires it
    exactly when `status = 'retired'`) alongside `status` and
    `retirement_reason`. `nptc.catalogue.queries.get_entry_by_code` orders
    retired-code collisions by it (ADR-0033). It is written with `func.now()`,
    the database clock, not the application's: it orders rows written by
    different app instances, whose clocks need not agree."""
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)
    if binding.status == str(CodeBindingStatus.RETIRED):
        raise CodeBindingAlreadyRetiredError(f"code binding {binding.id} is already retired")

    binding.status = str(CodeBindingStatus.RETIRED)
    binding.retirement_reason = validated_reason
    binding.retired_at = func.now()
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
    """The third step of a replacement (module docstring): populates
    `superseded.replaced_by_binding_id` once `superseded` is retired and
    `successor` has a flushed `id`.

    Raises `CodeBindingNotRetiredError` if `superseded` is not retired, and
    `CodeBindingSelfSupersessionError` if `successor is superseded`;
    `ck_code_binding_replaced_by_requires_retired` and
    `ck_code_binding_no_self_supersession` are the database invariants these
    pre-empt. Raises a bare `ValueError`, deliberately without `http_status`,
    if `successor.id` is `None`: that is a caller sequencing bug, not a state
    an API request can reach, and assigning it would write `NULL` into
    `replaced_by_binding_id` because `CodeBinding.id` has only a
    `server_default=func.gen_random_uuid()`."""
    validated_reason = validate_changelog_note(reason)
    acquire_append_lock(session)
    if superseded.status != str(CodeBindingStatus.RETIRED):
        raise CodeBindingNotRetiredError(
            f"code binding {superseded.id} must be retired before it can name a successor"
        )
    if successor is superseded:
        raise CodeBindingSelfSupersessionError(
            f"code binding {superseded.id} cannot be its own replacement"
        )
    if successor.id is None:
        raise ValueError(
            "successor has not been flushed yet - its id is None, and linking now would "
            "silently write NULL into replaced_by_binding_id"
        )

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
