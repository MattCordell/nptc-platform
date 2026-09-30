"""Builds the frozen `PropertyDefinitionSpec` view `nptc.registry` handlers take, from the
`PropertyDefinition` ORM row.

It lives under `nptc.db`, not `nptc.registry`, for the reason `nptc.db.bootstrap` does: it imports
the `PropertyDefinition` ORM model, which ADR-0013 SS2's leaf rule keeps out of `registry/`. It is
not in `nptc.catalogue` because `nptc.db.property_indexes` needs the identical conversion to compute
a property's desired index shape, and `nptc.catalogue` imports `nptc.db`, not the reverse. One
conversion also means a column added to `PropertyDefinition` cannot update one copy and leave
another stale.
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
    """The frozen view `nptc.registry` handlers and `validate_values` take, never the ORM row
    itself: `nptc.registry` must not import `nptc.db` (ADR-0013 SS2's leaf rule), so the conversion
    happens on the `nptc.db` side.
    """
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
