"""Which columns of a mapped model may appear in an audit diff (NFR-08, NFR-26, PRD OI-15).

An allowlist and a deny-list both apply, and the deny check runs when an `AuditFieldPolicy` is
constructed, so no call site can build a policy that declares a credential-shaped field.
ADR-0018 records why neither list alone is enough.

A model declares its policy as three `ClassVar`s, which `policy_for` reads by name. That keeps
this module from importing a model, and a model from importing this module (a cycle):

    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset({"status"})
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"username"})
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "created_at"})

Every real column must be `auditable`, `withheld` or `ignored`, or `policy_for` refuses to
resolve a policy. A new column then fails a test instead of escaping auditing by default.
`ignored` fields are not deny-list checked: keeping a credential-shaped column out of every
diff is the right outcome for it.

A model that is deliberately never diffed (`AuditEvent`: diffing the log is circular) sets
`__audit_fields__ = None` and a mandatory `__audit_exempt_reason__`. `policy_for` raises
`MissingAuditPolicyError` for that case and for an undeclared model alike;
`test_audit_redaction.py` tells an exemption from an oversight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Final

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase

#: Matched case-insensitively against declared field *names*, never values: when an
#: `AuditFieldPolicy` is constructed, and in `nptc.audit.diffing.diff_snapshots` against every
#: key of a hand-built snapshot.
DENIED_FIELD_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(secret|passwo?r?d|passwd|token|credential|api[_-]?key|private[_-]?key"
    r"|salt|nonce|otp|totp|recovery[_-]?code|session[_-]?id|cookie)",
    re.IGNORECASE,
)


class AuditPolicyError(RuntimeError):
    """Base class for every refusal to resolve or construct an `AuditFieldPolicy`, or to build a
    diff against one. Catch it to handle all of them at once."""


class MissingAuditPolicyError(AuditPolicyError):
    """Raised by `policy_for` when `model` declares no `__audit_fields__`, or declares it `None`
    (the exemption marker; see the module docstring). Fails closed: a model with no policy
    cannot be diffed."""


class DeniedAuditFieldError(AuditPolicyError):
    """Raised when a declared (or hand-supplied) field name matches
    `DENIED_FIELD_NAME_RE` - a credential-shaped name must never be
    declared auditable or withheld, only omitted from a policy entirely."""


class AmbiguousSnapshotFieldError(AuditPolicyError):
    """Raised by `nptc.audit.diffing.diff_snapshots` when a field is present in only one of
    `before`/`after` for an `UPDATED` diff. Treating the missing side as null would fabricate a
    change nobody reported."""


@dataclass(frozen=True)
class AuditFieldPolicy:
    """Which fields of `entity_type` may appear in a diff, and how.

    - `auditable`: recorded in full, normalised.
    - `withheld`: a change is recorded by name only, under `nptc.audit.diffing.REDACTED_KEY`,
      in both `before` and `after` (NFR-16, NFR-17, PRD OI-15). The change stays visible
      although its value must not be recorded.
    - `ignored`: never appears in a diff (the primary key, bookkeeping timestamps).
    - `known`: every real column. `auditable | withheld | ignored` must equal it exactly, or
      construction fails.
    """

    entity_type: str
    auditable: frozenset[str]
    withheld: frozenset[str]
    ignored: frozenset[str]
    known: frozenset[str]

    def __post_init__(self) -> None:
        overlap = (
            (self.auditable & self.withheld)
            | (self.auditable & self.ignored)
            | (self.withheld & self.ignored)
        )
        if overlap:
            raise AuditPolicyError(
                f"{self.entity_type}: field(s) {sorted(overlap)} declared in more "
                "than one of auditable/withheld/ignored - a field must be exactly "
                "one of the three"
            )

        classified = self.auditable | self.withheld | self.ignored
        for name in classified:
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

        for name in self.auditable | self.withheld:
            if DENIED_FIELD_NAME_RE.search(name):
                raise DeniedAuditFieldError(
                    f"{self.entity_type}: {name!r} looks credential-shaped and must "
                    "never be declared auditable or withheld - omit it entirely, or "
                    "declare it ignored"
                )

        unclassified = self.known - classified
        if unclassified:
            raise AuditPolicyError(
                f"{self.entity_type}: column(s) {sorted(unclassified)} are not "
                "classified as auditable, withheld, or ignored - every real column "
                "must be one of the three so a future column cannot silently "
                "escape classification"
            )

    def is_auditable(self, name: str) -> bool:
        return name in self.auditable

    def is_withheld(self, name: str) -> bool:
        return name in self.withheld

    def is_ignored(self, name: str) -> bool:
        return name in self.ignored

    def is_declared(self, name: str) -> bool:
        return name in self.auditable or name in self.withheld


_MISSING: Final[object] = object()


@cache
def policy_for(model: type[DeclarativeBase]) -> AuditFieldPolicy:
    """The `AuditFieldPolicy` for `model`: its declared `__audit_fields__` and
    `__audit_withheld_fields__`, checked against the real columns from `sqlalchemy.inspect`.
    Cached, because a policy cannot change at runtime and this runs on every `diff_instance`."""
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

    ignored: object = getattr(model, "__audit_ignored_fields__", frozenset())
    if not isinstance(ignored, frozenset):
        raise AuditPolicyError(
            f"{model.__name__}.__audit_ignored_fields__ must be a frozenset[str], "
            f"got {type(ignored).__name__}"
        )

    mapper = sa_inspect(model)
    known = frozenset(mapper.columns.keys())
    policy = AuditFieldPolicy(
        entity_type=model.__tablename__,
        auditable=declared,
        withheld=withheld,
        ignored=ignored,
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
