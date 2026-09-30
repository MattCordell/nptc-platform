"""The one entry point domain code calls to emit a field-level audit event (NFR-08).

`record_change` and `record_snapshot_change` compute a diff with `nptc.audit.diffing`, refuse
an empty one, and delegate to `nptc.audit.writer.append_audit_event` with the diff's
`before`/`after` payloads. `append_audit_event` keeps its own signature as the general
primitive for a diff-free event (a future `release.published`, NFR-12's `audit.exported`).

A caller must not flush the session first: `flush()` clears attribute history, so the change
would read as an empty diff and raise `AuditNoOpError` (see `nptc.audit.diffing`).

There is no lenient `record_change_if_any`: reaching `record_change` asserts that a write
happened, so an empty diff is always a bug (`AuditNoOpError`). A caller with a genuinely
idempotent no-op path short-circuits before this module, as `close_account` does. ADR-0018
records the decision.

`record_batch_summary` is a third, narrower wrapper for a batch header event, which carries a
structured summary rather than a diff.
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
    # FR-06: entity_id is always a string, even for a UUID primary key.
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
    """Diffs `instance` from its own attribute history (`nptc.audit.diffing.diff_instance`) and
    appends the result. Raises `AuditNoOpError` on an empty diff, which is always a bug (see
    the module docstring and `nptc.audit.diffing`).

    For `kind=ChangeKind.CREATED`, `instance` must still be in `session.new` when this is
    called. A flushed insert has lost its attribute history, and unlike `UPDATED`/`DELETED`
    that would not show up as an empty diff, because the `CREATED` branch reads current
    attribute values. The function then flushes the *session* (no narrower flush exists) before
    diffing and resolving `entity_id`. Before the flush the instance has no primary key and its
    server-default columns (for example `User.status`) read as `None`, so the `after` payload
    would be incomplete or the call would fail. RETURNING-based eager defaults leave the
    attributes populated afterwards with no extra `SELECT`.
    """
    if kind is ChangeKind.CREATED:
        if instance not in session.new:
            raise AuditNoOpError(
                "record_change(kind=CREATED) called with an instance no longer in "
                "session.new - it has already been flushed, so computing its diff "
                "here would not reflect the insert this call is meant to record. "
                "Call record_change before the session flushes this instance."
            )
        session.flush()

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
        # cast: `dict` is invariant, so mypy rejects `dict[str, JsonValue]` as `dict[str, object]`.
        before=cast("dict[str, object] | None", diff.before_payload()),
        after=cast("dict[str, object] | None", diff.after_payload()),
        reason=reason,
    )


def record_batch_summary(
    session: Session,
    ctx: AuditContext,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    reason: str,
    tallies: Mapping[str, int],
) -> AuditEvent:
    """Appends a diff-free batch header event carrying `tallies` as a structured `after`
    payload.

    Not a diff: a batch header summarises N other events rather than changing a row, so
    `AuditNoOpError` does not apply. A batch where nothing applied is a reason not to call
    this (see the one caller's "only call this when `tallies['applied'] > 0`" rule), not an
    error for this function to raise on all-zero `tallies`.

    Lives here so `after=` satisfies `test_audit_write_path_guard.py`, which forbids a
    hand-built `before=`/`after=` outside this package and cannot tell a structured summary
    from a hand-rolled diff except by the file the call sits in. The one caller is
    `nptc.catalogue.property_values.save_property_values_for_entries`.
    """
    return append_audit_event(
        session,
        ctx,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        after=dict(tallies),
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
    """The non-ORM counterpart to `record_change`: diffs `before`/`after` snapshots against
    `policy` (`nptc.audit.diffing.diff_snapshots`). Raises `AuditNoOpError` on an empty diff."""
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
        # cast: `dict` is invariant, so mypy rejects `dict[str, JsonValue]` as `dict[str, object]`.
        before=cast("dict[str, object] | None", diff.before_payload()),
        after=cast("dict[str, object] | None", diff.after_payload()),
        reason=reason,
    )
