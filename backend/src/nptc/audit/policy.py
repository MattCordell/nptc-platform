"""Which columns of a mapped model are ever allowed into an audit diff
(issue #37, NFR-08, NFR-26, PRD OI-15).

**Why both an allowlist and a deny-list.** A deny-list alone fails open:
the first credential-shaped column nobody thought to pattern-match leaks
into `before`/`after` the moment a diff is taken. An allowlist alone fails
to copy-paste: nothing stops `password_hash` from being pasted into a
model's `__audit_fields__` by someone who didn't think to check. Both are
enforced together, and the deny check runs at `AuditFieldPolicy`
*construction* time - a call site cannot even build a policy that declares
a credential-shaped field, let alone use one.

**Declared on the model, not a central registry.** `nptc.audit` importing
`nptc.db.models` would be fine, but the reverse - a model importing this
module to build its own policy eagerly - would create a cycle, since
`policy.py` never needs to import a concrete model. So a model declares two
`ClassVar`s and `policy_for` reads them by name:

    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"status"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"username"})

A model deliberately never diffed (`AuditEvent` - diffing the log itself is
circular) sets `__audit_fields__ = None` **and** a mandatory
`__audit_exempt_reason__: ClassVar[str]`, so the exemption carries its
justification in code rather than being inferred from absence.
`policy_for` treats "not declared at all" and "declared `None`" the same
way - both raise `MissingAuditPolicyError` - since both mean this module
has nothing to resolve; distinguishing an exemption from an oversight is
`test_audit_redaction.py`'s job (it separately requires
`__audit_exempt_reason__` whenever `__audit_fields__` is exactly `None`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Final

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase

#: Matched case-insensitively against every declared field *name* (never a
#: field's value) at `AuditFieldPolicy` construction time, and again by
#: `nptc.audit.diffing.diff_snapshots` against every key of a hand-built
#: snapshot - so a name that looks credential-shaped can never reach
#: `before`/`after`, whether it arrived via a model's declared policy or a
#: caller-assembled dict.
DENIED_FIELD_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(secret|passwo?r?d|passwd|token|credential|api[_-]?key|private[_-]?key"
    r"|salt|nonce|otp|totp|recovery[_-]?code|session[_-]?id|cookie)",
    re.IGNORECASE,
)


class AuditPolicyError(RuntimeError):
    """Base class for every way an `AuditFieldPolicy` can fail to resolve
    or construct."""


class MissingAuditPolicyError(AuditPolicyError):
    """Raised by `policy_for` when `model` declares no `__audit_fields__`
    at all, or declares it as `None` (a model's own exemption marker,
    which carries its justification in `__audit_exempt_reason__` instead -
    see the module docstring). Fails closed: a model with no policy simply
    cannot be diffed, rather than silently falling back to some default
    set of fields."""


class DeniedAuditFieldError(AuditPolicyError):
    """Raised when a declared (or hand-supplied) field name matches
    `DENIED_FIELD_NAME_RE` - a credential-shaped name must never be
    declared auditable or withheld, only omitted from a policy entirely."""


@dataclass(frozen=True)
class AuditFieldPolicy:
    """Which fields of `entity_type` may appear in a diff, and how.

    `auditable` fields are recorded in full (normalised, but at their real
    value); `withheld` fields that change are recorded by *name only*,
    under `nptc.audit.diffing.REDACTED_KEY`, in both `before` and `after`
    (NFR-16/NFR-17, PRD OI-15) - a change to a withheld field is never
    invisible in the log merely because its value must not be recorded.
    `known` is every real column on the model, used only to catch a
    declared name that no longer exists (a rename or typo that would
    otherwise silently un-audit a field without anyone noticing).
    """

    entity_type: str
    auditable: frozenset[str]
    withheld: frozenset[str]
    known: frozenset[str]

    def __post_init__(self) -> None:
        overlap = self.auditable & self.withheld
        if overlap:
            raise AuditPolicyError(
                f"{self.entity_type}: field(s) {sorted(overlap)} declared both "
                "auditable and withheld - a field must be exactly one or the other"
            )

        declared = self.auditable | self.withheld
        for name in declared:
            if name.startswith("_"):
                raise AuditPolicyError(
                    f"{self.entity_type}: {name!r} is a reserved leading-underscore "
                    "name (nptc.audit.diffing.REDACTED_KEY lives in that namespace) "
                    "and cannot be declared as an audit field"
                )
            if name not in self.known:
                raise AuditPolicyError(
                    f"{self.entity_type}: {name!r} is not a real column on this "
                    "model - a rename or typo here would otherwise silently "
                    "un-audit a field rather than fail loudly"
                )
            if DENIED_FIELD_NAME_RE.search(name):
                raise DeniedAuditFieldError(
                    f"{self.entity_type}: {name!r} looks credential-shaped and must "
                    "never be declared auditable or withheld, only omitted entirely"
                )

    def is_auditable(self, name: str) -> bool:
        return name in self.auditable

    def is_withheld(self, name: str) -> bool:
        return name in self.withheld

    def is_declared(self, name: str) -> bool:
        return name in self.auditable or name in self.withheld


_MISSING: Final[object] = object()


@cache
def policy_for(model: type[DeclarativeBase]) -> AuditFieldPolicy:
    """The `AuditFieldPolicy` for `model`, combining its declared
    `__audit_fields__`/`__audit_withheld_fields__` with the real column set
    from `sqlalchemy.inspect(model)`. Cached - a model's policy cannot
    change at runtime, and this runs on every `diff_instance` call."""
    declared = getattr(model, "__audit_fields__", _MISSING)
    if declared is _MISSING or declared is None:
        raise MissingAuditPolicyError(
            f"{model.__name__} declares no __audit_fields__ (or declares it as "
            "None, its own exemption marker) - every mapped model must either "
            "resolve a policy here or carry an explicit __audit_exempt_reason__"
        )
    if not isinstance(declared, frozenset):
        raise AuditPolicyError(
            f"{model.__name__}.__audit_fields__ must be a frozenset[str], got "
            f"{type(declared).__name__}"
        )

    withheld: object = getattr(model, "__audit_withheld_fields__", frozenset())
    if not isinstance(withheld, frozenset):
        raise AuditPolicyError(
            f"{model.__name__}.__audit_withheld_fields__ must be a frozenset[str], "
            f"got {type(withheld).__name__}"
        )

    mapper = sa_inspect(model)
    known = frozenset(mapper.columns.keys())
    policy = AuditFieldPolicy(
        entity_type=model.__tablename__,
        auditable=declared,
        withheld=withheld,
        known=known,
    )

    for name in policy.auditable | policy.withheld:
        if not mapper.attrs[name].active_history:
            raise AuditPolicyError(
                f"{model.__name__}.{name} is declared auditable/withheld but its "
                "mapped column lacks active_history=True - nptc.audit.diffing."
                "diff_instance's load_history() cannot recover a prior value for an "
                "attribute that gets reassigned before ever being loaded without it "
                "(see nptc.db.models.user's own comment on this same requirement)"
            )

    return policy
