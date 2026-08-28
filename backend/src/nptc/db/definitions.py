"""ORM-backed PropertyDefinition service: create/amend/deprecate/list/load
(issue #55, FR-11, FR-12). This absorbed the full admin router's backing
service - #151 (frontend) depends on it and no other issue owns it.

**Lives under `nptc.db`, not `nptc.registry`**, for the same leaf-rule
reason `nptc.db.bootstrap`/`nptc.db.property_specs`/`nptc.db.property_indexes`
do (ADR-0013 SS2): it imports the `PropertyDefinition` ORM model directly.
`nptc.registry.definitions` holds the datatype-agnostic typed errors and the
`DefinitionAudience` vocabulary this module raises/consumes.

**Concurrent-insert races.** `create_definition` follows
`nptc.db.bootstrap.seed_system_properties`'s own savepoint pattern: the
insert runs inside `session.begin_nested()`, and only a `23505` (unique
violation on `uq_property_definition_key`) is caught and re-raised as the
typed `PropertyDefinitionKeyExistsError` - any other `IntegrityError`
propagates unchanged, since it is a genuine defect in the write, not a race.

**`key` immutability is enforced twice, deliberately.** `amend_definition`
never assigns to `.key` at all (so `PropertyDefinition._validate_key_immutable`
can never even fire from this path) and instead raises
`PropertyKeyImmutableError` itself, before touching any other attribute, the
moment a caller's amendment would move `key` away from what it already is.
The HTTP layer closes the same gap a third way: `PatchDefinitionRequest`
carries no `key` field at all, so a request body naming one is a 422 at the
pydantic layer, never reaching this function.

**One audit event per write**, matching `nptc.catalogue.bindings`'s own
precedent: `record_change` diffs the mapped instance's own attribute
history, so `create`/`amend`/`deprecate` each produce exactly one
`audit_event` row (NFR-08).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext
from nptc.catalogue.errors import ConflictReport, EntryVersionConflictError
from nptc.catalogue.property_values import PropertyDefinitionNotFoundError
from nptc.db.models.property_definition import PropertyDefinition, PropertyStatus
from nptc.registry.definitions import (
    DefinitionAudience,
    PropertyAlreadyDeprecatedError,
    PropertyDefinitionKeyExistsError,
    PropertyKeyImmutableError,
    PropertyReactivationRefusedError,
    SystemPropertyDeprecationRefusedError,
)

__all__ = [
    "amend_definition",
    "create_definition",
    "deprecate_definition",
    "list_definitions",
    "load_definition",
]

#: Postgres sqlstate for a unique-violation - the same narrow catch
#: `nptc.db.bootstrap.seed_system_properties` uses for the identical race on
#: this table's `key` column.
_UNIQUE_VIOLATION = "23505"


def load_definition(session: Session, key: str) -> PropertyDefinition:
    """The one place a caller resolves a `key` to a row - raises
    `PropertyDefinitionNotFoundError` (reused from `nptc.catalogue.
    property_values`, see `nptc.registry.definitions`'s own docstring) for
    an unknown key, whatever its status - a deprecated definition is still
    loadable by key (FR-11: "retained, forever"), only excluded from the
    `DATA_ENTRY` audience of `list_definitions`."""
    definition = session.execute(
        select(PropertyDefinition).where(PropertyDefinition.key == key)
    ).scalar_one_or_none()
    if definition is None:
        raise PropertyDefinitionNotFoundError(f"no property_definition with key {key!r}")
    return definition


def list_definitions(
    session: Session, *, audience: DefinitionAudience, scope: str | None = None
) -> Sequence[PropertyDefinition]:
    """Ordered by `display_order, key` (matching `nptc.db.bootstrap`'s own
    seeded ordering). `audience=DATA_ENTRY` excludes a `deprecated` property
    entirely; `audience=EXPORT` returns every status - see
    `DefinitionAudience`'s own docstring for why these two callers need
    different sets. `scope`, when given, filters to rows whose `scope`
    column is either the given value or `'both'`."""
    stmt = select(PropertyDefinition).order_by(
        PropertyDefinition.display_order, PropertyDefinition.key
    )
    if audience is DefinitionAudience.DATA_ENTRY:
        stmt = stmt.where(PropertyDefinition.status == PropertyStatus.ACTIVE)
    if scope is not None:
        stmt = stmt.where(PropertyDefinition.scope.in_((scope, "both")))
    return session.execute(stmt).scalars().all()


def create_definition(
    session: Session,
    ctx: AuditContext,
    *,
    key: str,
    label: str,
    datatype: str,
    cardinality: str,
    scope: str,
    required_for_submission: bool,
    required_for_publication: bool,
    filterable: bool,
    display_order: int,
    binding_target: str | None = None,
    value_set_uri: str | None = None,
    strength: str | None = None,
    edition: str | None = None,
    local_code_system_key: str | None = None,
    constraints: dict[str, Any] | None = None,
    reason: str,
) -> PropertyDefinition:
    """Inserts a new `origin = 'admin'` property definition, racing the
    same `uq_property_definition_key` unique-violation
    `nptc.db.bootstrap.seed_system_properties` guards against - translated
    the same way `nptc.catalogue.bindings.create_binding` translates its
    own unique-violation race: `session.add` with no flush of its own,
    then `record_change(kind=CREATED)` (which flushes the session as part
    of building the CREATED diff, per its own docstring) inside the `try`,
    so the loser's `IntegrityError` surfaces from that flush rather than a
    separate one this function would otherwise have to add itself."""
    definition = PropertyDefinition(
        key=key,
        label=label,
        datatype=datatype,
        cardinality=cardinality,
        scope=scope,
        required_for_submission=required_for_submission,
        required_for_publication=required_for_publication,
        binding_target=binding_target,
        value_set_uri=value_set_uri,
        strength=strength,
        edition=edition,
        local_code_system_key=local_code_system_key,
        filterable=filterable,
        origin="admin",
        display_order=display_order,
        constraints=constraints or {},
    )
    session.add(definition)
    try:
        record_change(
            session,
            ctx,
            action="property_definition.create",
            instance=definition,
            kind=ChangeKind.CREATED,
            reason=reason,
        )
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
            raise
        raise PropertyDefinitionKeyExistsError(
            f"a property_definition with key {key!r} already exists"
        ) from error
    return definition


def amend_definition(
    session: Session,
    ctx: AuditContext,
    *,
    definition: PropertyDefinition,
    expected_row_version: int,
    reason: str,
    **changes: Any,
) -> PropertyDefinition:
    """Applies `changes` (any mapped column except `key`, `origin`, `status`,
    `deprecated_at` - those are either immutable or owned by a dedicated
    function) to `definition`, guarded by `expected_row_version` (FR-38),
    exactly one audit event, or none if nothing actually changed.

    Raises `PropertyKeyImmutableError` if `changes` contains a `key` field
    at all (belt-and-braces: the HTTP request model already forbids the
    field outright) and `EntryVersionConflictError`-shaped
    `EntryVersionConflictError` is reused here for the stale-version case,
    matching `nptc.catalogue.property_values.save_property_values`'s own
    precedent of reusing that one conflict type rather than inventing a
    second per entity.
    """
    if "key" in changes:
        raise PropertyKeyImmutableError(
            "PropertyDefinition.key cannot be amended (FR-12); create a new "
            "property definition instead"
        )
    if (
        changes.get("status") == PropertyStatus.ACTIVE
        and definition.status == PropertyStatus.DEPRECATED
    ):
        raise PropertyReactivationRefusedError(
            f"property {definition.key!r} is deprecated and cannot be reactivated"
        )
    if definition.row_version != expected_row_version:
        raise EntryVersionConflictError(
            ConflictReport(
                business_key=definition.key,
                expected_row_version=expected_row_version,
                current_row_version=definition.row_version,
            )
        )

    for field, value in changes.items():
        setattr(definition, field, value)

    record_change(
        session,
        ctx,
        action="property_definition.amend",
        instance=definition,
        kind=ChangeKind.UPDATED,
        reason=reason,
    )
    return definition


def deprecate_definition(
    session: Session,
    ctx: AuditContext,
    *,
    definition: PropertyDefinition,
    expected_row_version: int,
    reason: str,
) -> PropertyDefinition:
    """FR-11: moves `status` from `active` to `deprecated`, stamping
    `deprecated_at`. One-way - see `nptc.registry.definitions.
    PropertyReactivationRefusedError`'s own docstring; there is no function
    that reverses this.

    Refuses (`SystemPropertyDeprecationRefusedError`, 409) for
    `origin = 'system'` - see that error's own docstring for why this is an
    explicit assumption, not a PRD requirement. Refuses
    (`PropertyAlreadyDeprecatedError`, 409) for a definition already
    deprecated - not idempotent-success, since a repeat call is a caller
    mistake worth surfacing.

    Existing `property_value` rows for this property are untouched - FR-11's
    whole point is that they remain readable; nothing here deletes or
    rewrites a single one.
    """
    if definition.row_version != expected_row_version:
        raise EntryVersionConflictError(
            ConflictReport(
                business_key=definition.key,
                expected_row_version=expected_row_version,
                current_row_version=definition.row_version,
            )
        )
    if definition.origin == "system":
        raise SystemPropertyDeprecationRefusedError(
            f"property {definition.key!r} is a system property and cannot be deprecated"
        )
    if definition.status == PropertyStatus.DEPRECATED:
        raise PropertyAlreadyDeprecatedError(f"property {definition.key!r} is already deprecated")

    definition.status = PropertyStatus.DEPRECATED
    definition.deprecated_at = datetime.now(UTC)
    record_change(
        session,
        ctx,
        action="property_definition.deprecate",
        instance=definition,
        kind=ChangeKind.UPDATED,
        reason=reason,
    )
    return definition
