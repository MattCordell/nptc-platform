"""The one entry point domain code calls to emit a field-level audit event
(issue #37, NFR-08).

`record_change` and `record_snapshot_change` are thin wrappers around
`nptc.audit.diffing` plus `nptc.audit.writer.append_audit_event`: they
compute a diff, refuse to proceed if it is empty, and otherwise delegate to
the writer with the diff's `before`/`after` payloads. `append_audit_event`
itself is untouched by this issue and keeps its own signature - it stays
the general primitive a diff-free event (a future `release.published`, or
NFR-12's `audit.exported`) can still call directly; `close_account` already
proves a diff-free payload is sometimes exactly right.

No lenient `record_change_if_any` variant is added here: reaching
`record_change` is meant to assert a write happened, so an empty diff is
always a bug (see `AuditNoOpError`). A caller with a genuinely idempotent
no-op path short-circuits *before* reaching this module, exactly as
`close_account`'s early return already does. Adding a lenient variant later
is a small, reviewable act; a lenient default from day one is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Session

from nptc.audit.diffing import ChangeKind, diff_instance, diff_snapshots
from nptc.audit.policy import AuditFieldPolicy
from nptc.audit.writer import AuditContext, append_audit_event
from nptc.db.models.audit import AuditEvent


class AuditNoOpError(RuntimeError):
    """`record_change`/`record_snapshot_change` was called with an empty
    diff. Either nothing changed (the caller should have short-circuited
    before starting a write), or the session was flushed before the diff
    was taken and the change is now invisible to SQLAlchemy's attribute
    history. Both are bugs; a silently missing audit event is exactly the
    NFR-08 failure this refuses to produce."""


def _default_entity_id(instance: DeclarativeBase) -> str:
    identity = sa_inspect(instance).identity
    if not identity:
        raise ValueError(
            "cannot default entity_id: instance has no identity yet (its "
            "primary key is not assigned) - pass entity_id explicitly for a "
            "not-yet-flushed CREATED instance"
        )
    # FR-06: entity_id is always a string, even when the underlying primary
    # key is (for every model today) a UUID.
    return str(identity[0])


def record_change(
    session: Session,
    ctx: AuditContext,
    *,
    action: str,
    instance: DeclarativeBase,
    kind: ChangeKind,
    entity_type: str | None = None,
    entity_id: str | None = None,
    reason: str | None = None,
) -> AuditEvent:
    """Diffs `instance` via its own SQLAlchemy attribute history
    (`nptc.audit.diffing.diff_instance`) and appends the result. Raises
    `AuditNoOpError` if the diff is empty - see the module docstring and
    `nptc.audit.diffing`'s own docstring for why that is always a bug, not
    a legitimate no-op.

    For `kind=ChangeKind.CREATED` specifically, `instance` must still be in
    `session.new`: a flushed insert has already lost the attribute history
    this reads, and unlike `UPDATED`/`DELETED` that ordering bug would not
    otherwise show up as an empty diff (the `CREATED` branch of
    `diff_instance` reads current attribute values directly, not history).
    """
    if kind is ChangeKind.CREATED and instance not in session.new:
        raise AuditNoOpError(
            "record_change(kind=CREATED) called with an instance no longer in "
            "session.new - it has already been flushed, so computing its diff "
            "here would not reflect the insert this call is meant to record. "
            "Call record_change before the session flushes this instance."
        )

    diff = diff_instance(instance, kind=kind)
    if diff.is_empty():
        raise AuditNoOpError(
            "record_change was called with an empty diff - either nothing "
            "changed (the caller should have short-circuited before starting a "
            "write), or the session was flushed before the diff was taken and "
            "the change is now invisible to SQLAlchemy's attribute history. "
            "Both are bugs; a silently missing audit event is the NFR-08 "
            "failure this refuses to produce."
        )

    resolved_entity_type = (
        entity_type if entity_type is not None else cast(str, type(instance).__tablename__)
    )
    resolved_entity_id = entity_id if entity_id is not None else _default_entity_id(instance)

    return append_audit_event(
        session,
        ctx,
        action=action,
        entity_type=resolved_entity_type,
        entity_id=resolved_entity_id,
        # cast: `before_payload()`/`after_payload()` are typed as
        # `dict[str, JsonValue] | None`, and `dict` is invariant in mypy -
        # `dict[str, JsonValue]` is not structurally a `dict[str, object]`
        # even though every JsonValue is an object.
        before=cast("dict[str, object] | None", diff.before_payload()),
        after=cast("dict[str, object] | None", diff.after_payload()),
        reason=reason,
    )


def record_snapshot_change(
    session: Session,
    ctx: AuditContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    policy: AuditFieldPolicy,
    before: Mapping[str, object] | None,
    after: Mapping[str, object] | None,
    kind: ChangeKind,
    reason: str | None = None,
) -> AuditEvent:
    """The non-ORM counterpart to `record_change`: diffs `before`/`after`
    snapshots against `policy` (`nptc.audit.diffing.diff_snapshots`) rather
    than reading a mapped instance's attribute history. Raises
    `AuditNoOpError` on an empty diff, exactly as `record_change` does."""
    diff = diff_snapshots(policy=policy, before=before, after=after, kind=kind)
    if diff.is_empty():
        raise AuditNoOpError(
            "record_snapshot_change was called with an empty diff - nothing in "
            "before/after actually differs under this policy. A silently "
            "missing audit event is the NFR-08 failure this refuses to produce."
        )

    return append_audit_event(
        session,
        ctx,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        # cast: `before_payload()`/`after_payload()` are typed as
        # `dict[str, JsonValue] | None`, and `dict` is invariant in mypy -
        # `dict[str, JsonValue]` is not structurally a `dict[str, object]`
        # even though every JsonValue is an object.
        before=cast("dict[str, object] | None", diff.before_payload()),
        after=cast("dict[str, object] | None", diff.after_payload()),
        reason=reason,
    )
