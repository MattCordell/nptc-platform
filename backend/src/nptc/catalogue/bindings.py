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

**Blocking severity (issue #49, FR-08): one active binding per code, full
stop.** `CodeBindingAlreadyActiveError` above already checks the entry
side (`ix_code_binding_one_active_per_entry`); `create_binding` below
adds the code side too - the same code active on a *different* entry
(`ix_code_binding_one_active_entry_per_code`) - as its own domain error
rather than a raw `IntegrityError`. Unlike FR-05's error/warning pair,
this has no acknowledgement path: a code is either free or it isn't.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
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

#: `IntegrityError.orig.diag.constraint_name` for the two partial unique
#: indexes `create_binding` below pre-checks before insert. Read-then-write
#: pre-checks narrow the race but cannot close it (issue #219 review): two
#: concurrent inserts can both pass the pre-check and only one wins at
#: flush. Named here so the flush-time fallback maps the same constraint to
#: the same domain error the pre-check already raises, rather than letting
#: the loser surface as a raw `IntegrityError` (`nptc.auth.identity.
#: _is_username_collision` is the precedent for this constraint-name-based
#: recovery - `nptc.db.errors.unique_violation_constraint` is the unwrap
#: logic both now share). `test_race_translation_constraint_names_match_
#: the_actual_indexes` in `test_catalogue_bindings.py` pins these two
#: literals against `CodeBinding.__table_args__`'s actual `Index` names.
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
    a *different* entry - issue #49's blocking severity, the other half of
    FR-08's "one active binding" that `CodeBindingAlreadyActiveError`
    above doesn't cover. Checked before insert so it surfaces as a domain
    error rather than `ix_code_binding_one_active_entry_per_code`'s raw
    `IntegrityError`. 409: well-formed request, conflicting state."""

    http_status: ClassVar[int] = 409


class CodeBindingNotRetiredError(ValueError):
    """Raised by `link_replacement` when `superseded` is not already
    retired - `ck_code_binding_replaced_by_requires_retired` is the actual
    database invariant; this is the fail-loud Python-level layer, and the
    reason `link_replacement` is its own step rather than a parameter on
    `retire_binding` (see the module docstring)."""

    http_status: ClassVar[int] = 409


class CodeBindingSelfSupersessionError(ValueError):
    """Raised by `link_replacement` when `successor is superseded` -
    `ck_code_binding_no_self_supersession` is the actual database
    invariant; this is the fail-loud Python-level layer, checked before
    anything is assigned."""

    http_status: ClassVar[int] = 409


class InvalidCodeBindingEditionHintError(ValueError):
    """Raised by `create_binding` when `edition_hint` is not one of
    `CodeBindingEditionHint`'s values - `ck_code_binding_edition_hint` is
    the actual database invariant; this is the fail-loud Python-level
    layer, checked before anything is added to the session."""

    http_status: ClassVar[int] = 422


class CodeBindingNotFoundError(LookupError):
    """Raised by `load_active_binding` when `entry` has no *active* binding
    for `code` - the addressing scheme issue #219's write routes use, since
    the public `Binding` model (deliberately) carries no id a client could
    retire or replace by. A retired binding is not addressable this way: a
    caller retiring or replacing a binding by its code means the one that
    is currently in force, and `ix_code_binding_one_active_entry_per_code`
    guarantees at most one row can match. 404, matching
    `nptc.catalogue.errors.EntryNotFoundError`'s own reasoning for the
    identifier one level up."""

    http_status: ClassVar[int] = 404


class CodeBindingWriteNotFoundError(LookupError):
    """Raised when a route re-reads a binding it just wrote (by `id`,
    through `nptc.catalogue.queries.load_bindings`) and the row is not
    there. Distinct from `CodeBindingNotFoundError`: that one reports a
    caller's own path parameter not resolving; this one reports the write
    path's own invariant - "the row this function just flushed exists" -
    failing, which is a platform bug, not a caller mistake. 500, and mapped
    through `nptc.api.errors` like every other handled exception rather
    than surfacing as an unhandled `AssertionError`, so it is logged with
    the same discipline as every other refusal."""

    http_status: ClassVar[int] = 500


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


def load_active_binding(session: Session, *, entry_id: uuid.UUID, code: str) -> CodeBinding:
    """The entry's *active* binding for `code`, or `CodeBindingNotFoundError`.

    The one lookup issue #219's write routes need and none of the module's
    existing functions provide: they all take an already-loaded
    `CodeBinding`/`CatalogueEntry`, because the service layer has never
    before had an HTTP caller needing to resolve one from a path parameter.
    Scoped to `status == 'active'` deliberately - see the exception's own
    docstring for why a retired binding is not a valid target here."""
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

    # `entry.id` is read into the `where(...)` predicate below *before*
    # `session.execute()`'s own autoflush would otherwise populate it - a
    # brand-new, not-yet-flushed `entry` has no identity yet, which would
    # bake a stale value into the query and make this check vacuous (it
    # would find zero rows regardless of what `entry` actually binds to).
    # Flushing first, only when needed, closes that gap. Checked via
    # `sa_inspect(...).identity` (matching `nptc.audit.recording.
    # _default_entity_id`'s own precedent) rather than `entry.id is None`:
    # `Mapped[uuid.UUID]` is typed non-optional, so mypy would flag the
    # latter as an unreachable comparison even though it is true at
    # runtime before the first flush.
    if not sa_inspect(entry).identity:
        session.flush()

    # Read into a local once, up front: reused below both for the
    # pre-check query/message and (if the insert loses its race) inside
    # the `except` block, where re-reading `entry.id` from the ORM
    # instance would be the bug this guards against - a failed flush
    # leaves every instance the session tracks expired, so touching an
    # already-loaded attribute afterwards triggers a reload against a
    # session that is not yet rolled back, raising `PendingRollbackError`
    # in place of the domain error this is meant to raise (issue #225).
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

    # Issue #49's blocking severity: the code side of "one active binding",
    # not the entry side checked above - see `CodeBindingCodeAlreadyBoundError`'s
    # own docstring.
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
    # The two pre-checks above narrow the race but cannot close it: two
    # concurrent binds both pass them and only one wins at insert.
    # `record_change(kind=CREATED)` flushes the session itself (see its own
    # docstring) - that flush is what actually hits the database and is
    # where the loser's `IntegrityError` surfaces, so it is what this
    # translates, rather than reaching the caller raw.
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
    invariant this pre-empts. Raises `CodeBindingSelfSupersessionError` if
    `successor is superseded` - `ck_code_binding_no_self_supersession` is
    the database invariant this pre-empts. Raises a bare `ValueError`
    (deliberately with no `http_status`, unlike its siblings above: this
    is a caller sequencing bug - forgetting to flush the successor before
    linking it - not a state a well-formed API request could ever land in)
    if `successor.id` is `None` (not yet flushed) - assigning it directly
    would otherwise silently write `NULL` into `replaced_by_binding_id`,
    since `CodeBinding.id` has no Python-side default, only
    `server_default=func.gen_random_uuid()`."""
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
