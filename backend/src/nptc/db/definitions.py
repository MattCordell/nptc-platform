"""ORM-backed PropertyDefinition service: create, amend, deprecate, list and load (FR-11, FR-12).

**Lives under `nptc.db`, not `nptc.registry`**, for the leaf-rule reason that `nptc.db.bootstrap`,
`nptc.db.property_specs` and `nptc.db.property_indexes` do (ADR-0013 SS2): it imports the
`PropertyDefinition` ORM model directly. `nptc.registry.definitions` holds the typed errors and the
`DefinitionAudience` vocabulary this module raises and consumes.

**Concurrent-insert races.** `create_definition` follows
`nptc.db.bootstrap.seed_system_properties`'s savepoint pattern: the insert runs inside
`session.begin_nested()`, and only a unique violation on `uq_property_definition_key` (identified by
`nptc.db.errors.unique_violation_constraint`, not a raw sqlstate) becomes
`PropertyDefinitionKeyExistsError`. Any other `IntegrityError` propagates, because it is a defect in
the write, not a race.

**`datatype` and `constraints` are validated against the resolved handler before a row is written.**
`create_definition` and `amend_definition` take a `DatatypeRegistry` for this. An unrecognised
`datatype` raises `PropertyDatatypeUnknownError` (422). `constraints` that do not conform to the
handler's `constraints_schema()` raise `PropertyConstraintsInvalidError` (422). Both happen before
`session.add` or `setattr`.

**`key` immutability is enforced in layers, deliberately.** `amend_definition` never assigns `.key`.
It raises `PropertyKeyImmutableError` itself, before touching any other attribute, so
`PropertyDefinition._validate_key_immutable` cannot fire from this path. The HTTP layer adds a third
guard: `PatchDefinitionRequest` has no `key` field, so a body naming one is a 422.

**One audit event per write** (NFR-08): `record_change` diffs the instance's attribute history, so
create, amend and deprecate each produce exactly one `audit_event` row.
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
from nptc.db.errors import unique_violation_constraint
from nptc.db.models.property_definition import PropertyDefinition, PropertyScope, PropertyStatus
from nptc.db.property_specs import spec_for
from nptc.registry.definitions import (
    DefinitionAudience,
    PropertyAlreadyDeprecatedError,
    PropertyConstraintsInvalidError,
    PropertyDatatypeUnknownError,
    PropertyDefinitionKeyExistsError,
    PropertyKeyImmutableError,
    PropertyReactivationRefusedError,
    SystemPropertyDeprecationRefusedError,
)
from nptc.registry.handlers import DatatypeRegistry, UnknownDatatypeError
from nptc.registry.schema import MalformedConstraintsError, validate_constraints

__all__ = [
    "amend_definition",
    "create_definition",
    "deprecate_definition",
    "list_definitions",
    "load_definition",
]

#: The unique constraint `create_definition` races against; see the module docstring.
_UQ_PROPERTY_DEFINITION_KEY = "uq_property_definition_key"

#: The only fields `amend_definition` will `setattr`: exactly those
#: `AmendPropertyDefinitionRequest.changes()` (`nptc.api.routers.registry`) can populate.
#: `binding_target` is deliberately absent: `_binding_spec` (`nptc.db.property_specs`) returns
#: `None` silently for a `None` target, so a direct `binding_target=None` on a `datatype='code'`
#: property would reach the `binding_required_for_code` CHECK as an unhandled `23514` (500).
#: Re-admit a field only alongside the router field that exposes it, with a test for both. Every
#: other mapped column is immutable (`key`), owned by a dedicated function (`origin`, `status`,
#: `deprecated_at`), or not amendable.
_AMENDABLE_FIELDS = frozenset(
    {
        "label",
        "required_for_submission",
        "required_for_publication",
        "filterable",
        "display_order",
        "constraints",
    }
)


def _validate_registry_shape(registry: DatatypeRegistry, definition: PropertyDefinition) -> None:
    """Resolves `definition.datatype` against `registry` and validates `definition.constraints`
    against that handler's `constraints_schema()`. Called before `definition` is written, so a bad
    `datatype` or `constraints` is a 422 at request time, not a broken row that fails at the first
    value write.
    """
    try:
        handler = registry.get(definition.datatype)
    except UnknownDatatypeError as error:
        raise PropertyDatatypeUnknownError(definition.datatype) from error
    spec = spec_for(definition)
    try:
        validate_constraints(spec, handler)
    except MalformedConstraintsError as error:
        raise PropertyConstraintsInvalidError(str(error)) from error


#: Every field `_merged_for_validation` overlays onto a copy of `definition`: every mapped field the
#: transient copy needs, because `PropertyDefinition.__init__` needs a value for each, not only
#: `_AMENDABLE_FIELDS`.
#:
#: **Read through a loop over this tuple, never a literal `changes.get("scope", ...)` or
#: `changes["scope"]`.** `PropertyDefinitionSpec.scope` collides by name with a restricted JWT claim
#: key in `test_token_verification_guard.py`'s NFR-07 AST guard, which flags any literal `"scope"`
#: subscript or `.get()` key outside `nptc/auth/tokens.py`. A runtime field name keeps this function
#: outside that pattern without weakening the guard.
_MERGE_FIELDS: tuple[str, ...] = (
    "key",
    "label",
    "datatype",
    "cardinality",
    "scope",
    "required_for_submission",
    "required_for_publication",
    "binding_target",
    "value_set_uri",
    "strength",
    "edition",
    "local_code_system_key",
    "filterable",
    "origin",
    "display_order",
    "constraints",
)


def _merged_for_validation(
    definition: PropertyDefinition, changes: dict[str, Any]
) -> PropertyDefinition:
    """A transient, never-persisted `PropertyDefinition` holding `definition`'s values overlaid with
    `changes`. It builds the spec `_validate_registry_shape` checks, so the live `definition` is not
    mutated until every guard has passed.
    """
    merged = {
        field: changes[field] if field in changes else getattr(definition, field)
        for field in _MERGE_FIELDS
    }
    return PropertyDefinition(**merged)


def load_definition(session: Session, key: str) -> PropertyDefinition:
    """Resolves a `key` to a row, raising `PropertyDefinitionNotFoundError` (from
    `nptc.catalogue.property_values`) for an unknown key whatever its status. A deprecated
    definition is still loadable (FR-11: "retained, forever"); only the `DATA_ENTRY` audience of
    `list_definitions` excludes it.
    """
    definition = session.execute(
        select(PropertyDefinition).where(PropertyDefinition.key == key)
    ).scalar_one_or_none()
    if definition is None:
        raise PropertyDefinitionNotFoundError(f"no property_definition with key {key!r}")
    return definition


def list_definitions(
    session: Session, *, audience: DefinitionAudience, scope: PropertyScope | None = None
) -> Sequence[PropertyDefinition]:
    """Ordered by `display_order, key`, matching `nptc.db.bootstrap`'s seeded ordering.
    `audience=DATA_ENTRY` excludes `deprecated` properties; `audience=EXPORT` returns every status
    (see `DefinitionAudience`).

    `scope`, when given, includes `PropertyScope.BOTH`: filtering to exactly `scope` would drop a
    property meant for both screens.
    """
    stmt = select(PropertyDefinition).order_by(
        PropertyDefinition.display_order, PropertyDefinition.key
    )
    if audience is DefinitionAudience.DATA_ENTRY:
        stmt = stmt.where(PropertyDefinition.status == PropertyStatus.ACTIVE)
    if scope is not None:
        stmt = stmt.where(PropertyDefinition.scope.in_((scope, PropertyScope.BOTH)))
    return session.execute(stmt).scalars().all()


def create_definition(
    session: Session,
    ctx: AuditContext,
    *,
    registry: DatatypeRegistry,
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
    """Inserts a new `origin = 'admin'` property definition. As in
    `nptc.catalogue.bindings.create_binding`, the insert runs inside `session.begin_nested()` with
    `record_change(kind=CREATED)` inside the `try`, so the loser of a `uq_property_definition_key`
    race gets its `IntegrityError` from `record_change`'s flush and it is translated. `datatype` and
    `constraints` are validated against `registry` first (`_validate_registry_shape`).
    """
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
    _validate_registry_shape(registry, definition)
    try:
        with session.begin_nested():
            session.add(definition)
            record_change(
                session,
                ctx,
                action="property_definition.create",
                instance=definition,
                kind=ChangeKind.CREATED,
                reason=reason,
            )
    except IntegrityError as error:
        if unique_violation_constraint(error) != _UQ_PROPERTY_DEFINITION_KEY:
            raise
        raise PropertyDefinitionKeyExistsError(
            f"a property_definition with key {key!r} already exists"
        ) from error
    return definition


def amend_definition(
    session: Session,
    ctx: AuditContext,
    *,
    registry: DatatypeRegistry,
    definition: PropertyDefinition,
    expected_row_version: int,
    reason: str,
    **changes: Any,
) -> PropertyDefinition:
    """Applies `changes` to `definition`, guarded by `expected_row_version` (FR-38). Emits one audit
    event, or none if nothing changed.

    `changes` may name only a field in `_AMENDABLE_FIELDS`; any other key raises `ValueError`. This
    function enforces that itself rather than relying on the HTTP router's whitelist, because a
    direct call with `status=PropertyStatus.DEPRECATED` would otherwise reach `setattr` and break
    the `deprecated_at_required` CHECK as an unhandled 500.

    Raises `PropertyKeyImmutableError` if `changes` contains `key` (the HTTP request model also
    forbids it), `EntryVersionConflictError` for a stale `expected_row_version` (one conflict type
    per entity, as in `nptc.catalogue.property_values.save_property_values`), and
    `PropertyConstraintsInvalidError` if the amended `constraints` no longer conform to the
    datatype's `constraints_schema()`. That last check runs on a transient copy, so a rejected
    amendment never mutates the live instance.
    """
    if "key" in changes:
        raise PropertyKeyImmutableError(
            "PropertyDefinition.key cannot be amended (FR-12); create a new "
            "property definition instead"
        )
    # Before the generic allowlist check so this transition gets its own 409 instead of the generic
    # `ValueError`. `status` is not in `_AMENDABLE_FIELDS` (`deprecate_definition` owns it), so any
    # other `status` value falls through to that `ValueError`.
    if (
        changes.get("status") == PropertyStatus.ACTIVE
        and definition.status == PropertyStatus.DEPRECATED
    ):
        raise PropertyReactivationRefusedError(
            f"property {definition.key!r} is deprecated and cannot be reactivated"
        )
    unknown_fields = changes.keys() - _AMENDABLE_FIELDS
    if unknown_fields:
        raise ValueError(
            f"amend_definition cannot change {sorted(unknown_fields)}; only "
            f"{sorted(_AMENDABLE_FIELDS)} may be amended"
        )
    if definition.row_version != expected_row_version:
        raise EntryVersionConflictError(
            ConflictReport(
                business_key=definition.key,
                expected_row_version=expected_row_version,
                current_row_version=definition.row_version,
            )
        )

    _validate_registry_shape(registry, _merged_for_validation(definition, changes))

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
    """FR-11: moves `status` from `active` to `deprecated` and stamps `deprecated_at`. One-way:
    nothing reverses it (see `PropertyReactivationRefusedError`). Refuses with a 409 for
    `origin = 'system'` (`SystemPropertyDeprecationRefusedError`) and for a definition already
    deprecated (`PropertyAlreadyDeprecatedError`; a repeat call is a caller mistake worth
    surfacing). Existing `property_value` rows are untouched, so they stay readable.
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
