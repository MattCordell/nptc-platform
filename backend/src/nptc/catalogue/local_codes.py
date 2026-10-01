"""The `local_code_system`/`local_code`/`local_code_snomed_map` service
layer (FR-90, FR-91, FR-92). See PRD SS6.6 and the models
`nptc.db.models.local_code_system`/`local_code`/`local_code_snomed_map` for
the per-table reasoning.

**Administrator-only management (FR-90).** Every write requires
`Permission.REGISTRY_MANAGE`, checked against a permission, never a role name
(FR-44), as `nptc.catalogue.collisions.acknowledge_collision` does.
`PermissionDeniedError` is raised before anything is added to the session.

**Every write carries a changelog note and one audit event.** The note is
validated first (`nptc.catalogue.changelog.validate_changelog_note`, FR-37),
and one audit event is recorded per successful write (NFR-08), with actions
`local_code_system.<verb>`, `local_code.<verb>` and
`local_code_snomed_map.<verb>`.

**No `create_snomed_map_row` uniqueness check.** `local_code_snomed_map` has
no uniqueness constraint on `local_code_id`, unlike the pre-insert check in
`nptc.catalogue.bindings.create_binding`. PRD SS6.6 verifies `Microbiology`
as genuinely ambiguous between two SNOMED candidates, so a local code may
carry more than one map row.

**Lives in `nptc.catalogue`, not `nptc.registry`**, though FR-90/91/92 sit
in the property-registry part of the PRD. `nptc.registry` is a leaf package:
it may import `nptc_shared`, SQLAlchemy, `jsonschema` and the stdlib, and
nothing else from `nptc` (ADR-0013 SS2). This module needs `nptc.audit`,
`nptc.auth`, `nptc.catalogue.changelog` and the ORM models. Only
`LocalCodeLookup` and `ResolvedLocalCode`, the dependency-free read contract
`CodeHandler` consumes, live in `nptc.registry.handlers`.
`DatabaseLocalCodeLookup`, which needs a `Session`, lives here beside the
write path it is built on.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Permission
from nptc.auth.principal import Principal
from nptc.catalogue.changelog import validate_changelog_note
from nptc.db.models.code_binding import SNOMED_CT_SYSTEM
from nptc.db.models.local_code import LocalCode, LocalCodeStatus
from nptc.db.models.local_code_snomed_map import LocalCodeSnomedMap, SnomedMapMatchStrength
from nptc.db.models.local_code_system import KEY_PATTERN, LocalCodeSystem, LocalCodeSystemStatus
from nptc.registry.handlers import ResolvedLocalCode
from nptc_shared.sctid import SCTID

__all__ = [
    "DatabaseLocalCodeLookup",
    "InvalidLocalCodeSystemKeyError",
    "InvalidMatchStrengthError",
    "LocalCodeAlreadyDeprecatedError",
    "LocalCodeSystemAlreadyDeprecatedError",
    "create_local_code",
    "create_local_code_system",
    "create_snomed_map_row",
    "deprecate_local_code",
    "deprecate_local_code_system",
    "find_local_code",
    "find_local_code_with_system_status",
    "find_local_codes",
    "list_local_codes",
]


class LocalCodeSystemAlreadyDeprecatedError(ValueError):
    """Raised by `deprecate_local_code_system` when `system` is already
    deprecated, rather than silently no-opping - mirrors
    `nptc.catalogue.bindings.CodeBindingAlreadyRetiredError`. 409: the
    request is well-formed, it just conflicts with the resource's current
    state."""

    http_status: ClassVar[int] = 409


class LocalCodeAlreadyDeprecatedError(ValueError):
    """Raised by `deprecate_local_code` when `code` is already deprecated -
    same posture as `LocalCodeSystemAlreadyDeprecatedError` above."""

    http_status: ClassVar[int] = 409


class InvalidLocalCodeSystemKeyError(ValueError):
    """Raised by `create_local_code_system` when `key` does not match
    `KEY_PATTERN`: the Python-level layer over `ck_local_code_system_key`,
    checked before anything is added to the session."""

    http_status: ClassVar[int] = 422


class InvalidMatchStrengthError(ValueError):
    """Raised by `create_snomed_map_row` when `match_strength` is not one of
    `SnomedMapMatchStrength`'s values: the Python-level layer over
    `ck_local_code_snomed_map_match_strength`, checked before anything is
    added to the session."""

    http_status: ClassVar[int] = 422


def _require_registry_manage(actor: Principal) -> None:
    if not actor.has(Permission.REGISTRY_MANAGE):
        raise PermissionDeniedError(f"permission {Permission.REGISTRY_MANAGE.value!r} is required")


def create_local_code_system(
    session: Session,
    ctx: AuditContext,
    *,
    actor: Principal,
    key: str,
    uri: str,
    title: str,
    description: str,
    owner: str,
    reason: str,
) -> LocalCodeSystem:
    """Creates a governed local code system (FR-90). Requires
    `Permission.REGISTRY_MANAGE`."""
    _require_registry_manage(actor)
    validated_reason = validate_changelog_note(reason)
    if not KEY_PATTERN.fullmatch(key):
        raise InvalidLocalCodeSystemKeyError(
            f"{key!r} is not a valid local code system key - expected {KEY_PATTERN.pattern!r}"
        )

    system = LocalCodeSystem(key=key, uri=uri, title=title, description=description, owner=owner)
    session.add(system)
    record_change(
        session,
        ctx,
        action="local_code_system.created",
        instance=system,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
    return system


def deprecate_local_code_system(
    session: Session,
    ctx: AuditContext,
    *,
    actor: Principal,
    system: LocalCodeSystem,
    reason: str,
) -> LocalCodeSystem:
    """Deprecates `system` via a `status` transition - never a `DELETE`
    (`nptc.db.roles.REVOKE_LOCAL_CODE_SYSTEM_DELETE_SQL` makes this a
    privilege-level guarantee). Requires `Permission.REGISTRY_MANAGE`."""
    _require_registry_manage(actor)
    if system.status == str(LocalCodeSystemStatus.DEPRECATED):
        raise LocalCodeSystemAlreadyDeprecatedError(
            f"local code system {system.id} is already deprecated"
        )

    validated_reason = validate_changelog_note(reason)
    system.status = str(LocalCodeSystemStatus.DEPRECATED)
    record_change(
        session,
        ctx,
        action="local_code_system.deprecated",
        instance=system,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return system


def create_local_code(
    session: Session,
    ctx: AuditContext,
    *,
    actor: Principal,
    system: LocalCodeSystem,
    code: str,
    display: str,
    definition: str | None = None,
    provisional: bool = False,
    reason: str,
) -> LocalCode:
    """Adds one code to `system` (FR-90). `provisional=True` marks a value
    migrated verbatim ahead of RCPA-QAP settling its vocabulary (FR-92) -
    see `nptc.db.models.local_code`'s own docstring. Requires
    `Permission.REGISTRY_MANAGE`."""
    _require_registry_manage(actor)
    validated_reason = validate_changelog_note(reason)

    local_code = LocalCode(
        system_id=system.id,
        code=code,
        display=display,
        definition=definition,
        provisional=provisional,
    )
    session.add(local_code)
    record_change(
        session,
        ctx,
        action="local_code.created",
        instance=local_code,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
    return local_code


def deprecate_local_code(
    session: Session,
    ctx: AuditContext,
    *,
    actor: Principal,
    code: LocalCode,
    reason: str,
) -> LocalCode:
    """Deprecates `code` via a `status` transition - never a `DELETE`
    (`nptc.db.roles.REVOKE_LOCAL_CODE_DELETE_SQL` makes this a
    privilege-level guarantee). The FR-45 validation sweep's
    `local_code_retired` warning keys off it. Sets `deprecated_at`, which
    `ck_local_code_deprecated_at` requires exactly when
    `status = 'deprecated'` and which the deferred version-history argument
    in the `local_code` model docstring depends on; it uses
    `datetime.now(UTC)`, as `AppUser.closed_at` does. Requires
    `Permission.REGISTRY_MANAGE`."""
    _require_registry_manage(actor)
    if code.status == str(LocalCodeStatus.DEPRECATED):
        raise LocalCodeAlreadyDeprecatedError(f"local code {code.id} is already deprecated")

    validated_reason = validate_changelog_note(reason)
    code.status = str(LocalCodeStatus.DEPRECATED)
    code.deprecated_at = datetime.now(UTC)
    code.deprecation_reason = validated_reason
    record_change(
        session,
        ctx,
        action="local_code.deprecated",
        instance=code,
        kind=ChangeKind.UPDATED,
        reason=validated_reason,
    )
    return code


def create_snomed_map_row(
    session: Session,
    ctx: AuditContext,
    *,
    actor: Principal,
    local_code: LocalCode,
    code: str,
    display: str,
    match_strength: str,
    advisory_note: str,
    system: str = SNOMED_CT_SYSTEM,
    reason: str,
) -> LocalCodeSnomedMap:
    """Adds one advisory SNOMED map row for `local_code` (FR-91), with no
    uniqueness check against existing rows (module docstring). `code` is
    validated via `SCTID` before the row is constructed:
    `ck_local_code_snomed_map_code` (`nptc_sctid_is_valid`) is the database
    invariant, and this gives the caller an `InvalidSCTIDError` instead of a
    raw `IntegrityError` at flush, as in `bindings.create_binding`. Requires
    `Permission.REGISTRY_MANAGE`."""
    _require_registry_manage(actor)
    validated_reason = validate_changelog_note(reason)
    validated_code = SCTID(code).value

    try:
        validated_match_strength = str(SnomedMapMatchStrength(match_strength))
    except ValueError as exc:
        valid = ", ".join(repr(str(member)) for member in SnomedMapMatchStrength)
        raise InvalidMatchStrengthError(
            f"{match_strength!r} is not a valid match strength - expected one of {valid}"
        ) from exc

    map_row = LocalCodeSnomedMap(
        local_code_id=local_code.id,
        system=system,
        code=validated_code,
        display=display,
        match_strength=validated_match_strength,
        advisory_note=advisory_note,
    )
    session.add(map_row)
    record_change(
        session,
        ctx,
        action="local_code_snomed_map.created",
        instance=map_row,
        kind=ChangeKind.CREATED,
        reason=validated_reason,
    )
    return map_row


def find_local_code(session: Session, *, system_key: str, code: str) -> LocalCode | None:
    """Looks up a `local_code` by its owning system's `key` and its own
    `code`, without the owning system's `status` (see
    `find_local_code_with_system_status`). Its callers already hold the
    `LocalCodeSystem` they created the code under."""
    return session.execute(
        select(LocalCode)
        .join(LocalCodeSystem, LocalCode.system_id == LocalCodeSystem.id)
        .where(LocalCodeSystem.key == system_key, LocalCode.code == code)
    ).scalar_one_or_none()


def find_local_code_with_system_status(
    session: Session, *, system_key: str, code: str
) -> tuple[LocalCode, str] | None:
    """`DatabaseLocalCodeLookup`'s read path. Unlike `find_local_code`, also
    returns the owning system's `status`: `deprecate_local_code_system` does
    not touch member codes' own `status` (deprecating each code is a separate
    editorial decision), so a caller needs both facts to tell "this code is
    fine but its system is retired" from "this code itself is retired"."""
    row = session.execute(
        select(LocalCode, LocalCodeSystem.status)
        .join(LocalCodeSystem, LocalCode.system_id == LocalCodeSystem.id)
        .where(LocalCodeSystem.key == system_key, LocalCode.code == code)
    ).one_or_none()
    return None if row is None else (row[0], row[1])


def find_local_codes(
    session: Session, *, system_key: str, codes: Sequence[str]
) -> Sequence[LocalCode]:
    """Batch sibling of `find_local_code_with_system_status`: the same
    `local_code`/`local_code_system` join, with one `SELECT ... code IN (...)`
    for a set of codes instead of a round trip per code.
    `nptc.catalogue.property_value_sources._resolve_local_code_system_values`
    is the caller it exists for (ADR-0038). No `status` filter on either
    side, as in `find_local_code_with_system_status`: a deprecated code, or
    one in a deprecated system, still resolves.

    Returns whatever rows matched, in no particular order, and not one row
    per element of `codes`: a code with no match contributes nothing. A
    caller that needs `codes`' order, or the unmatched codes, builds a
    `{row.code: row}` map from the result.
    """
    return (
        session.execute(
            select(LocalCode)
            .join(LocalCodeSystem, LocalCode.system_id == LocalCodeSystem.id)
            .where(LocalCodeSystem.key == system_key, LocalCode.code.in_(codes))
        )
        .scalars()
        .all()
    )


def _escape_like(text: str) -> str:
    """Escapes `%`, `_` and the escape character itself, so a literal `%` or
    `_` in a filter matches as text, not as a wildcard. `list_local_codes`
    pairs it with `ILIKE ... ESCAPE '\\'`."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_local_codes(
    session: Session,
    *,
    system_key: str,
    filter: str | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[Sequence[LocalCode], int]:
    """Every active code in the local code system named `system_key`: the
    FR-90 read path behind a `binding_target = 'local_code_system'`
    property's values route.

    Active-only on both the code and its owning system. A deprecated code is
    excluded, mirroring `TerminologyClient.expand(active_only=True)` on the
    SNOMED side. So is every code of a deprecated system:
    `deprecate_local_code_system` does not cascade to member codes, but a
    retired vocabulary must not keep offering its codes for new entry. A value
    already recorded against such a code or system stays resolvable through
    `DatabaseLocalCodeLookup.resolve` (FR-11 posture); this function adds a
    read path and does not replace that one. Ordered by `display_order`, then
    `code`, as a governed vocabulary is curated to read.

    `filter`, when given, is a case-insensitive substring match against
    `display`. That is proportionate to a governed vocabulary's size (a
    handful of codes for `discipline` or `subgroup`), unlike the trigram and
    full-text ranking built for catalogue-wide search (ADR-0024, ADR-0029).

    Returns `(page, total)`; `total` counts every active, filter-matching
    code, not just this page, so a paging caller knows when to stop. An
    unknown `system_key` is not a special case: it has no matching codes, as
    an empty `Expansion` is a valid result, not a failure.
    """
    where_clauses = [
        LocalCodeSystem.key == system_key,
        LocalCodeSystem.status == str(LocalCodeSystemStatus.ACTIVE),
        LocalCode.status == str(LocalCodeStatus.ACTIVE),
    ]
    if filter:
        where_clauses.append(LocalCode.display.ilike(f"%{_escape_like(filter)}%", escape="\\"))

    total = session.execute(
        select(func.count(LocalCode.id))
        .select_from(LocalCode)
        .join(LocalCodeSystem, LocalCode.system_id == LocalCodeSystem.id)
        .where(*where_clauses)
    ).scalar_one()
    page = (
        session.execute(
            select(LocalCode)
            .join(LocalCodeSystem, LocalCode.system_id == LocalCodeSystem.id)
            .where(*where_clauses)
            .order_by(LocalCode.display_order, LocalCode.code)
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return page, total


class DatabaseLocalCodeLookup:
    """The database-backed implementation of
    `nptc.registry.handlers.LocalCodeLookup`, built on
    `find_local_code_with_system_status`. It does not subclass that
    `Protocol`; structural typing is the point (ADR-0003 treats
    `nptc.terminology`'s stub client the same way).
    `backend/tests/test_catalogue_local_codes.py` pins that it satisfies the
    protocol, with an assignment mypy would flag if the shapes drifted.

    Holds a `Session` for one request or job, as every service-layer caller
    does. It is not a singleton, so `HandlerDeps` construction (ADR-0013)
    stays per-request, as `TerminologyClient`'s does (NFR-37)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(self, system_key: str, code: str) -> ResolvedLocalCode | None:
        found = find_local_code_with_system_status(self._session, system_key=system_key, code=code)
        if found is None:
            return None
        local_code, system_status = found
        return ResolvedLocalCode(
            code=local_code.code,
            display=local_code.display,
            status=local_code.status,
            system_status=system_status,
            provisional=local_code.provisional,
        )
