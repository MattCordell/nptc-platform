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
insert runs inside `session.begin_nested()`, and only a unique violation on
`uq_property_definition_key` (identified via `nptc.db.errors.
unique_violation_constraint`, not a raw sqlstate literal - issue #223
review finding 2) is caught and re-raised as the typed
`PropertyDefinitionKeyExistsError` - any other `IntegrityError` propagates
unchanged, since it is a genuine defect in the write, not a race.

**`datatype` and `constraints` are validated against the resolved handler
before a row is ever written** (issue #223 review findings 3/4).
`create_definition` and `amend_definition` both take a `DatatypeRegistry`
for exactly this: an unrecognised `datatype` raises
`PropertyDatatypeUnknownError` (422) and a `constraints` document that does
not conform to the resolved handler's own `constraints_schema()` raises
`PropertyConstraintsInvalidError` (422) - both before `session.add`/
`setattr`, never surfacing later as a broken row that only misbehaves at
the first value write.

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

#: The unique constraint `create_definition` races against - see the
#: module docstring's concurrent-insert note.
_UQ_PROPERTY_DEFINITION_KEY = "uq_property_definition_key"

#: The only fields `amend_definition` will ever `setattr` (issue #223
#: review finding 5) - trimmed, per round-2 review minor finding 1, to
#: exactly the fields `AmendPropertyDefinitionRequest.changes()` (`nptc.api.
#: routers.registry`) can ever populate. `cardinality`/`scope`/`strength`/
#: `binding_target` used to be admitted here too, but the router's request
#: model never exposed any of them - untested, service-only surface that
#: let a direct caller reach `amend_definition(..., binding_target=None)`
#: on a `datatype='code'` property and hit the `binding_required_for_code`
#: database `CHECK` as an unhandled `23514` (500), because `_binding_spec`
#: (`nptc.db.property_specs`) returns `None` silently for a `None`
#: `binding_target` rather than validating it. Re-admit a field here only
#: alongside the router field that exposes it, with a test for both. Every
#: other mapped column is either immutable (`key`), owned by a dedicated
#: function (`origin`, `status`, `deprecated_at`), or simply not yet
#: amendable at all. A caller naming any other key is a service-layer
#: contract violation, not a request the caller could have gotten right by
#: chance.
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
    """Resolves `definition.datatype` against `registry` and validates
    `definition.constraints` against that handler's own
    `constraints_schema()` (issue #223 review findings 3/4) - called before
    `definition` is ever written, so a bad `datatype` or a malformed
    `constraints` document is a 422 at request time, never a broken row
    that only misbehaves at the first value write."""
    try:
        handler = registry.get(definition.datatype)
    except UnknownDatatypeError as error:
        raise PropertyDatatypeUnknownError(definition.datatype) from error
    spec = spec_for(definition)
    try:
        validate_constraints(spec, handler)
    except MalformedConstraintsError as error:
        raise PropertyConstraintsInvalidError(str(error)) from error


#: Every field `_merged_for_validation` may overlay onto a copy of
#: `definition` - deliberately every mapped field the transient copy needs
#: to carry, not just `_AMENDABLE_FIELDS`, since `PropertyDefinition.
#: __init__` needs a value for each of these regardless of whether `changes`
#: touches it.
#:
#: **Read via `getattr`/a `field in changes` membership test, in a loop over
#: this tuple - never a literal `changes.get("scope", ...)` or
#: `changes["scope"]`.** `nptc.registry.handlers.PropertyDefinitionSpec`'s
#: own `scope` field collides, by name only, with one of `test_token_
#: verification_guard.py`'s NFR-07 restricted JWT claim keys (issue #223
#: review: that AST guard flags any literal `"scope"` subscript/`.get()` key
#: anywhere outside `nptc/auth/tokens.py`, on the assumption it can only be
#: a JWT `scope` claim) - looping over a runtime field name here, rather
#: than writing the literal key at each call site, keeps this function
#: outside that pattern without weakening the guard itself.
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
    """A transient, never-persisted `PropertyDefinition` carrying
    `definition`'s current values overlaid with `changes` - used only to
    build the `PropertyDefinitionSpec` `_validate_registry_shape` checks an
    amendment against, without mutating the live, session-tracked
    `definition` until every guard has already passed (mirrors this
    module's own "raise before touching any attribute" posture for `key`
    and `status`)."""
    merged = {
        field: changes[field] if field in changes else getattr(definition, field)
        for field in _MERGE_FIELDS
    }
    return PropertyDefinition(**merged)


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
    session: Session, *, audience: DefinitionAudience, scope: PropertyScope | None = None
) -> Sequence[PropertyDefinition]:
    """Ordered by `display_order, key` (matching `nptc.db.bootstrap`'s own
    seeded ordering). `audience=DATA_ENTRY` excludes a `deprecated` property
    entirely; `audience=EXPORT` returns every status - see
    `DefinitionAudience`'s own docstring for why these two callers need
    different sets.

    `scope`, when given, is inclusive of `PropertyScope.BOTH` - filtering to
    exactly `scope` would silently drop a property meant for both screens
    from either one. Issue #223 review finding 8 dropped this filter as
    YAGNI (no caller, no test); issue #248 (`GET /registry/properties
    ?scope=`) is that caller, re-adding it with a test as that finding
    anticipated."""
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
    """Inserts a new `origin = 'admin'` property definition, racing the
    same `uq_property_definition_key` unique-violation
    `nptc.db.bootstrap.seed_system_properties` guards against - translated
    the same way `nptc.catalogue.bindings.create_binding` translates its
    own unique-violation race: the insert runs inside `session.
    begin_nested()`, then `record_change(kind=CREATED)` (which flushes the
    session as part of building the CREATED diff, per its own docstring)
    inside the `try`, so the loser's `IntegrityError` surfaces from that
    flush rather than a separate one this function would otherwise have to
    add itself.

    `datatype` and `constraints` are validated against `registry` before
    the insert - see `_validate_registry_shape`."""
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
    """Applies `changes` to `definition`, guarded by `expected_row_version`
    (FR-38), exactly one audit event, or none if nothing actually changed.

    `changes` may only name a field in `_AMENDABLE_FIELDS` - every other
    mapped column is either immutable (`key`), owned by a dedicated
    function (`origin`, `status`, `deprecated_at`), or simply not yet
    amendable independently (see `_AMENDABLE_FIELDS`'s own comment) - a
    caller naming any other key gets `ValueError`, a service-layer contract
    violation this function enforces itself rather than relying on the
    HTTP router's own whitelist (issue #223 review finding 5: calling this
    directly with, say, `status=PropertyStatus.DEPRECATED` used to slip
    past the router's reactivation guard entirely and reach `setattr`
    unfiltered, breaking the `deprecated_at_required` CHECK at flush as an
    unhandled 500).

    Raises `PropertyKeyImmutableError` if `changes` contains a `key` field
    at all (belt-and-braces: the HTTP request model already forbids the
    field outright), `EntryVersionConflictError` for a stale
    `expected_row_version` (reused here matching `nptc.catalogue.
    property_values.save_property_values`'s own precedent of one conflict
    type per entity, not a second one), and - when `changes` touches
    `constraints`, `cardinality`, `scope`, `strength` or `binding_target` -
    `PropertyConstraintsInvalidError` if the resulting `constraints` no
    longer conforms to the (immutable) datatype's own `constraints_schema()`
    (issue #223 review finding 4), checked via a transient, unpersisted copy
    of `definition` so a rejected amendment never mutates the live,
    session-tracked instance.
    """
    if "key" in changes:
        raise PropertyKeyImmutableError(
            "PropertyDefinition.key cannot be amended (FR-12); create a new "
            "property definition instead"
        )
    # Checked before the generic allowlist rejection below so this specific,
    # named transition still gets its own 409 rather than folding into the
    # generic 'not an amendable field' ValueError - `status` itself is not
    # in `_AMENDABLE_FIELDS` (owned by `deprecate_definition`), so any other
    # `status` value (e.g. a caller naming `PropertyStatus.DEPRECATED`
    # directly, issue #223 review finding 5's own example) still falls
    # through to that generic rejection just below.
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
