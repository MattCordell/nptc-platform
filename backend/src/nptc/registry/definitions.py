"""Datatype-agnostic PropertyDefinition service errors and DTOs (FR-11, FR-12).

A leaf module (ADR-0013 SS2): it imports only `nptc_shared`, the stdlib and
sibling `nptc.registry` modules, never `nptc.db` or another `nptc` package.
`nptc.db.definitions` is the ORM-touching half.

Several errors here are the service-level layer of an invariant the database
also enforces (a `@validates` guard, a missing grant or a unique constraint),
so a caller gets a typed 4xx instead of a driver error. The rest (for example
reactivation, repeat or system-property deprecation, unknown datatype, invalid
constraints) are enforced only here. The deprecation lifecycle is in
`docs/architecture/data-model.md`. Refusing to deprecate an `origin = 'system'`
property is an assumption the PRD does not state, and that page records it too.

`PropertyDefinitionNotFoundError` (404) is not redefined here:
`nptc.catalogue.property_values` defines it and every caller shares that one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

__all__ = [
    "DefinitionAudience",
    "DeprecatedPropertyWriteError",
    "PropertyAlreadyDeprecatedError",
    "PropertyConstraintsInvalidError",
    "PropertyDatatypeUnknownError",
    "PropertyDefinitionDeleteRefusedError",
    "PropertyDefinitionKeyExistsError",
    "PropertyKeyImmutableError",
    "PropertyReactivationRefusedError",
    "SystemPropertyDeprecationRefusedError",
]


class DefinitionAudience(StrEnum):
    """Which listing a caller of `nptc.db.definitions.list_definitions` wants.

    `DATA_ENTRY` is only `active` properties, so a deprecated one is never
    offered for a new value (FR-11). `EXPORT` is every status, because a value
    recorded against a since-deprecated property must still resolve to a spec
    and a handler when re-serialised.
    """

    DATA_ENTRY = "data_entry"
    EXPORT = "export"


class PropertyDefinitionDeleteRefusedError(Exception):
    """Raised by every call to `DELETE /registry/properties/{key}`, which never
    deletes a row (FR-11).

    `property_definition` has no `DELETE` grant, so this is the actionable
    refusal, naming deprecation, instead of an unhandled `42501` 500."""

    http_status: ClassVar[int] = 409


class PropertyKeyImmutableError(Exception):
    """Raised when an amendment attempts to change `key` (FR-12), before any
    attribute is touched. `PropertyDefinition.key`'s `@validates` guard is the
    storage-level backstop."""

    http_status: ClassVar[int] = 409


class PropertyAlreadyDeprecatedError(Exception):
    """Raised when `deprecate_definition` meets a property already `deprecated`.

    A repeat call is a caller mistake worth surfacing, not an idempotent no-op."""

    http_status: ClassVar[int] = 409


class PropertyReactivationRefusedError(Exception):
    """Raised when an amendment would move `status` from `deprecated` back to
    `active`. Deprecation is one-way (FR-11), so there is deliberately no
    `reactivate()`."""

    http_status: ClassVar[int] = 409


class SystemPropertyDeprecationRefusedError(Exception):
    """Raised when `deprecate_definition` meets an `origin = 'system'` property
    (Discipline, Subgroup, Specimen, Usage guidance).

    An assumption the PRD does not state; see `docs/architecture/data-model.md`."""

    http_status: ClassVar[int] = 409


class PropertyDefinitionKeyExistsError(Exception):
    """Raised by `create_definition` when `key` is already in use.

    `uq_property_definition_key` is the database invariant. This turns a
    concurrent-insert race into a typed 409 instead of an `IntegrityError` (see
    `create_definition`)."""

    http_status: ClassVar[int] = 409


class PropertyDatatypeUnknownError(Exception):
    """Raised by `create_definition` and `amend_definition` when `datatype` does
    not resolve via `DatatypeRegistry.get()` (FR-77).

    `property_definition.datatype` has no database `CHECK`, because that is
    FR-77's extension point. Without this error an unknown datatype would be
    saved and fail at the first value write. Raised before the row is written."""

    http_status: ClassVar[int] = 422

    def __init__(self, datatype: str) -> None:
        self.datatype = datatype
        super().__init__(f"{datatype!r} is not a known datatype")


class PropertyConstraintsInvalidError(Exception):
    """Raised when `constraints` does not conform to the datatype handler's
    `constraints_schema()`.

    Wraps `nptc.registry.schema.MalformedConstraintsError`, a plain `ValueError`
    from a leaf module with no HTTP concept, so that it gains an `http_status`."""

    http_status: ClassVar[int] = 422


class DeprecatedPropertyWriteError(Exception):
    """Raised by `save_property_values` when the resolved property is `deprecated`.

    It keeps its recorded values but accepts no new ones (FR-11). The message
    names the property key so a caller can act on it without decoding an entity
    id."""

    http_status: ClassVar[int] = 422

    def __init__(self, property_key: str) -> None:
        self.property_key = property_key
        super().__init__(f"property {property_key!r} is deprecated and cannot be written to")
