"""Field-level `before`/`after` diffs for an audit event (issue #37,
NFR-08, PRD Section 16).

`diff_instance` is the primary entry point: it reads a mapped instance's
own SQLAlchemy attribute history rather than a caller-supplied snapshot, so
a caller cannot forget or hand-roll the "before" - there is no `before=`
parameter to omit, and the "before" is the value SQLAlchemy actually
loaded, never a hand-copied approximation that can drift from it.

**Use `state.attrs[key].load_history()`, not `.history`.** `.history` runs
with `PASSIVE_NO_INITIALIZE` and returns `HISTORY_BLANK` for an unloaded or
expired attribute - i.e. it silently reports "no change" on an expired
instance. `load_history()` issues the `SELECT` needed to fetch the
committed value first. This is the single most important implementation
detail in this module: get it wrong and a genuinely-changed field on an
instance whose session called `expire_all()` (or committed and moved on)
reports as unchanged, which is a silent NFR-08 gap, not a loud one.

**The honest limitation.** SQLAlchemy's attribute history is cleared by
`flush()`. `nptc.audit.recording.record_change` computes the diff before
delegating to `nptc.audit.writer.append_audit_event` (which itself
flushes), so the ordinary call sequence is safe. But if the *caller*
flushes first, history is already empty by the time `diff_instance` runs,
and "already flushed, nothing to diff" becomes indistinguishable from
"nothing changed" - both report no changes. `record_change` raises on an
empty diff (see that module) so this surfaces as a loud `AuditNoOpError`
rather than a silently missing audit event; for `kind=CREATED` it
additionally asserts the instance is still in `session.new` (since a
flushed insert has already left that set) and then flushes the session
before diffing, so `after_payload()` reflects the instance's fully
populated, server-default-included state rather than its pre-flush Python
values. Both are documented here again, not only there, because this is
where the constraint actually bites.

**`diff_snapshots` is a first-class second path, not an escape hatch.**
Not every future auditable write has an ORM instance to read history from
- #51's `PropertyValue` is JSONB rather than columns, and a bulk
reclassify may materialise no per-row ORM object at all. Designing this in
now avoids a second, divergent diffing helper appearing later.
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
        # `frozen=True` only stops reassigning the `changes` attribute
        # itself - the dict it points to is otherwise freely mutable.
        # Wrapping it here makes the dataclass's own frozen-ness apply to
        # its contents too, not just its field bindings.
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
            # cast: `sorted(self.redacted)` is `list[str]`, and JsonValue's
            # recursive Union makes `list[str]` and `list[JsonValue]`
            # structurally identical here but not identical *types* under
            # mypy's invariant list checking.
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
    """The field-level diff for `instance`, derived from its own
    SQLAlchemy attribute history (or, for `CREATED`, its current attribute
    values - a transient instance has no history to read). See the module
    docstring for why `load_history()` and not `.history`, and for the
    flush-ordering constraint this function's caller must honour.
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
    """The non-ORM diffing path: `before`/`after` are plain snapshots (e.g.
    a JSONB property bag) rather than a mapped instance's own attribute
    history. Every key of both mappings is re-checked against
    `DENIED_FIELD_NAME_RE` here as well as at policy-construction time, so
    a hand-assembled dict cannot smuggle a credential-shaped key past a
    mapper-derived policy that never declared it.
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
            # A field present in only one of before/after is ambiguous for
            # an UPDATED diff - unlike diff_instance, which always knows
            # both the old and new value for a touched attribute, a
            # hand-built snapshot pair has no such guarantee. Silently
            # treating the missing side as null would record a spurious
            # null-to-value (or value-to-null) change for a field the
            # caller never actually reported on that side.
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
