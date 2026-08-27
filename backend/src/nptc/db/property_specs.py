"""Builds the frozen `PropertyDefinitionSpec` view `nptc.registry` handlers
take, from the `PropertyDefinition` ORM row (issue #54).

Moved out of `nptc.catalogue.property_values` (#52), where it originated as
the one write path's own helper, because `nptc.db.property_indexes` (#54,
FR-13) needs the identical conversion to compute a property's desired index
shape and must not import `nptc.catalogue` to get it - `nptc.catalogue`
imports `nptc.db`, not the other way around. Living under `nptc.db`, not
`nptc.registry`, for the same reason `nptc.db.bootstrap` does: it imports
the `PropertyDefinition` ORM model directly, which ADR-0013 SS2's leaf rule
keeps out of `registry/` (`registry/definitions.py`, #51/#55, is the
not-yet-built service that will eventually own this concern from the
registry side, building a spec from something other than the ORM row).

A single source of truth for this conversion also matters directly: if
`nptc.catalogue.property_values` and `nptc.db.property_indexes` each grew
their own copy, a future column added to `PropertyDefinition` could update
one and silently leave the other stale.
"""

from __future__ import annotations

from nptc.db.models.property_definition import PropertyDefinition
from nptc.registry.handlers import BindingSpec, PropertyDefinitionSpec

__all__ = ["spec_for"]


def _binding_spec(definition: PropertyDefinition) -> BindingSpec | None:
    if definition.binding_target is None:
        return None
    return BindingSpec(
        binding_target=definition.binding_target,
        value_set_uri=definition.value_set_uri,
        strength=definition.strength or "",
        edition=definition.edition or "",
        local_code_system_key=definition.local_code_system_key,
    )


def _scope(definition: PropertyDefinition) -> frozenset[str]:
    if definition.scope == "both":
        return frozenset({"submission", "maintenance"})
    return frozenset({definition.scope})


def spec_for(definition: PropertyDefinition) -> PropertyDefinitionSpec:
    """The frozen view `nptc.registry` handlers and `validate_values` take -
    never the ORM row itself (ADR-0013 SS2's leaf rule: `nptc.registry` must
    not import `nptc.db`, so the conversion happens here, on the `nptc.db`
    side of that boundary)."""
    return PropertyDefinitionSpec(
        key=definition.key,
        label=definition.label,
        datatype=definition.datatype,
        cardinality=definition.cardinality,
        scope=_scope(definition),
        required_for_submission=definition.required_for_submission,
        required_for_publication=definition.required_for_publication,
        binding=_binding_spec(definition),
        filterable=definition.filterable,
        constraints=definition.constraints,
    )
