"""Field-level `before`/`after` diffs for an audit event (NFR-08, PRD Section 16).

`diff_instance` reads a mapped instance's own SQLAlchemy attribute history, so a caller
cannot omit or hand-copy the "before". `diff_snapshots` is the second path, for a write with
no ORM instance to read (a JSONB property bag, a bulk reclassify). Design and rejected
alternatives: ADR-0018.

**`load_history()`, not `.history`.** `.history` is passive and returns `HISTORY_BLANK` for an
unloaded or expired attribute, which reports "no change". `load_history()` issues the `SELECT`
for the committed value first.

**`flush()` clears history.** `record_change` diffs before `append_audit_event` flushes, so the
ordinary call order is safe. If the caller flushes first, an empty diff is indistinguishable
from no change, so `record_change` raises `AuditNoOpError` on an empty diff. For `CREATED` it
also asserts the instance is still in `session.new`, then flushes before diffing so
`after_payload()` includes server defaults.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import History

from nptc.audit.policy import (
    DENIED_FIELD_NAME_RE,
    AmbiguousSnapshotFieldError,
    AuditFieldPolicy,
    AuditPolicyError,
    DeniedAuditFieldError,
    policy_for,
)
from nptc.audit.serialisation import JsonValue, normalise_json_value


class ChangeKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


#: Reserved key naming fields that changed but whose values are withheld.
#: Leading underscore so it can never collide with a real column name -
#: `AuditFieldPolicy` refuses any declared field name starting with `_`.
REDACTED_KEY: Final[str] = "_redacted"

_Pick = Callable[["FieldChange"], JsonValue]


@dataclass(frozen=True)
class FieldChange:
    before: JsonValue
    after: JsonValue


@dataclass(frozen=True)
class FieldDiff:
    kind: ChangeKind
    changes: Mapping[str, FieldChange] = field(default_factory=dict)
    redacted: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # `frozen=True` stops rebinding `changes`, not mutating the dict it holds. Wrapping it
        # makes the contents read-only too.
        object.__setattr__(self, "changes", MappingProxyType(dict(self.changes)))

    def is_empty(self) -> bool:
        return not self.changes and not self.redacted

    def before_payload(self) -> dict[str, JsonValue] | None:
        """`None` for `CREATED` (there is no "before" a creation) -
        otherwise every changed field's prior value, plus `REDACTED_KEY`
        naming any withheld field that also changed."""
        if self.kind is ChangeKind.CREATED:
            return None
        return self._payload(lambda change: change.before)

    def after_payload(self) -> dict[str, JsonValue] | None:
        """`None` for `DELETED` (there is no "after" a deletion) -
        otherwise every changed field's new value, plus `REDACTED_KEY`
        naming any withheld field that also changed."""
        if self.kind is ChangeKind.DELETED:
            return None
        return self._payload(lambda change: change.after)

    def _payload(self, pick: _Pick) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            name: pick(change) for name, change in self.changes.items()
        }
        if self.redacted:
            # cast: `list` is invariant, so mypy rejects `list[str]` as `list[JsonValue]`.
            payload[REDACTED_KEY] = cast("list[JsonValue]", sorted(self.redacted))
        return payload


def _history_old(history: History) -> object:
    if history.deleted:
        return history.deleted[0]
    if history.unchanged:
        return history.unchanged[0]
    return None


def _history_new(history: History) -> object:
    if history.added:
        return history.added[0]
    if history.unchanged:
        return history.unchanged[0]
    return None


def diff_instance(instance: DeclarativeBase, *, kind: ChangeKind) -> FieldDiff:
    """The field-level diff for `instance`, from its SQLAlchemy attribute history (for
    `CREATED`, from its current values: a transient instance has no history). The module
    docstring covers the flush-ordering constraint on callers.
    """
    policy = policy_for(type(instance))
    state = sa_inspect(instance)
    changes: dict[str, FieldChange] = {}
    redacted: set[str] = set()

    for name in sorted(policy.auditable | policy.withheld):
        before_value: object
        after_value: object

        if kind is ChangeKind.CREATED:
            after_value = getattr(instance, name)
            before_value = None
            changed = after_value is not None
        else:
            history = state.attrs[name].load_history()
            before_value = _history_old(history)
            if kind is ChangeKind.DELETED:
                after_value = None
                changed = before_value is not None
            else:
                after_value = _history_new(history)
                changed = before_value != after_value

        if not changed:
            continue

        if policy.is_withheld(name):
            redacted.add(name)
            continue

        changes[name] = FieldChange(
            before=normalise_json_value(before_value),
            after=normalise_json_value(after_value),
        )

    return FieldDiff(kind=kind, changes=changes, redacted=frozenset(redacted))


def diff_snapshots(
    *,
    policy: AuditFieldPolicy,
    before: Mapping[str, object] | None,
    after: Mapping[str, object] | None,
    kind: ChangeKind,
) -> FieldDiff:
    """The non-ORM diffing path: `before`/`after` are plain snapshots, such as a JSONB property
    bag. Every key is re-checked against `DENIED_FIELD_NAME_RE`, so a hand-built dict cannot
    carry a credential-shaped key past a mapper-derived policy.
    """
    before = before or {}
    after = after or {}

    for key in set(before) | set(after):
        if DENIED_FIELD_NAME_RE.search(key):
            raise DeniedAuditFieldError(
                f"{policy.entity_type}: snapshot key {key!r} looks credential-shaped "
                "and must never reach an audit diff"
            )
        if not policy.is_declared(key):
            raise AuditPolicyError(
                f"{policy.entity_type}: snapshot key {key!r} is not declared "
                "auditable or withheld by this policy"
            )

    changes: dict[str, FieldChange] = {}
    redacted: set[str] = set()

    for name in sorted(policy.auditable | policy.withheld):
        has_before = name in before
        has_after = name in after
        if not has_before and not has_after:
            continue
        if kind is ChangeKind.UPDATED and has_before != has_after:
            # A field on one side only is ambiguous for UPDATED. Treating the missing side as
            # null would record a change nobody reported.
            raise AmbiguousSnapshotFieldError(
                f"{policy.entity_type}: snapshot key {name!r} is present in only "
                "one of before/after for an UPDATED diff - include it in both "
                "(even if unchanged) or omit it from both"
            )

        before_value = before.get(name)
        after_value = after.get(name)

        if kind is ChangeKind.CREATED:
            changed = after_value is not None
            before_value = None
        elif kind is ChangeKind.DELETED:
            changed = before_value is not None
            after_value = None
        else:
            changed = before_value != after_value

        if not changed:
            continue

        if policy.is_withheld(name):
            redacted.add(name)
            continue

        changes[name] = FieldChange(
            before=normalise_json_value(before_value),
            after=normalise_json_value(after_value),
        )

    return FieldDiff(kind=kind, changes=changes, redacted=frozenset(redacted))
