"""Datatype-agnostic PropertyDefinition service errors and DTOs (issue #55,
FR-11, FR-12).

**A leaf module** (ADR-0013 SS2): imports only `nptc_shared`, the stdlib,
and sibling `nptc.registry` modules - never `nptc.db` or any other `nptc`
package. `nptc.db.definitions` is the ORM-touching half that actually reads
and writes `property_definition` rows; this module holds only the pure,
container-free pieces every caller of that service needs to catch:

- **Deprecation is one-way** (FR-11): there is no "reactivate" - an attempt
  to move a `deprecated` property back to `active` raises
  `PropertyReactivationRefusedError` rather than silently no-opping or
  quietly succeeding.
- **`key` is immutable** (FR-12): `PropertyKeyImmutableError` is raised by
  `nptc.db.definitions.amend_definition` before any attribute is touched,
  whenever a caller's amendment names a `key` different from the one it is
  amending - the database's own `@validates("key")` guard on
  `PropertyDefinition` is the second, storage-level layer for the same
  invariant; this is the fail-loud service-level layer, matching
  `nptc.catalogue.bindings`'s own precedent of raising before the ORM ever
  gets a chance to.
- **A property definition is never actually deleted** (FR-11): the API
  route always refuses a `DELETE` with `PropertyDefinitionDeleteRefusedError`,
  naming deprecation as the available action - `property_definition` has no
  `DELETE` grant at the database layer at all (issue #51), so this is the
  HTTP-visible refusal for the same invariant, not a second enforcement
  mechanism.
- **Deprecating an `origin = 'system'` property is refused**
  (`SystemPropertyDeprecationRefusedError`) - an explicit assumption this
  issue takes, flagged in the PR body as one the maintainer can strike: the
  PRD does not itself state this restriction.

`PropertyDefinitionNotFoundError` (404) is deliberately **not** redefined
here - `nptc.catalogue.property_values` already defines and raises it, and
every caller (the new registry service, the new HTTP router, the existing
value-write path) shares that one definition.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

__all__ = [
    "DefinitionAudience",
    "DeprecatedPropertyWriteError",
    "PropertyAlreadyDeprecatedError",
    "PropertyDefinitionDeleteRefusedError",
    "PropertyDefinitionKeyExistsError",
    "PropertyKeyImmutableError",
    "PropertyReactivationRefusedError",
    "SystemPropertyDeprecationRefusedError",
]


class DefinitionAudience(StrEnum):
    """Which listing a caller of `nptc.db.definitions.list_definitions`
    wants (issue #55's export-resolver-only scope decision).

    `DATA_ENTRY` is the audience for a submission/maintenance form: only
    `active` properties, since a deprecated one must never be offered as
    something new to record a value against (FR-11). `EXPORT` is every
    status, including `deprecated` - the audience a release/export needs,
    since a historical value recorded against a since-deprecated property
    must still resolve to a spec and a handler when re-serialised.
    """

    DATA_ENTRY = "data_entry"
    EXPORT = "export"


class PropertyDefinitionDeleteRefusedError(Exception):
    """Raised by every call reaching `DELETE /registry/properties/{key}` -
    that route never actually deletes a row (FR-11); `property_definition`
    has no `DELETE` grant at the database layer at all (issue #51), so this
    is the HTTP-visible, actionable refusal for the same invariant, naming
    deprecation as the available action rather than surfacing the
    underlying `42501` as an unhandled 500."""

    http_status: ClassVar[int] = 409


class PropertyKeyImmutableError(Exception):
    """Raised when an amendment attempts to change `key` (FR-12) - checked
    before any attribute is touched. `PropertyDefinition.key`'s own
    `@validates` guard (issue #51) is the storage-level backstop for the
    same invariant; this is the fail-loud service-level layer, matching
    `CatalogueEntry`'s own immutable-`business_key` precedent."""

    http_status: ClassVar[int] = 409


class PropertyAlreadyDeprecatedError(Exception):
    """Raised when `deprecate_definition` is called against a property
    whose `status` is already `deprecated` - deprecation is a one-time
    transition, not an idempotent no-op, so a repeat call is a caller
    mistake worth surfacing rather than silently succeeding again."""

    http_status: ClassVar[int] = 409


class PropertyReactivationRefusedError(Exception):
    """Raised whenever an amendment would move a property's `status` from
    `deprecated` back to `active` - deprecation is one-way (FR-11's own
    "deprecation, not deletion" framing does not extend to "and it can come
    back"). There is deliberately no `reactivate()` function to call
    instead."""

    http_status: ClassVar[int] = 409


class SystemPropertyDeprecationRefusedError(Exception):
    """Raised when `deprecate_definition` is called against an
    `origin = 'system'` property (Discipline, Subgroup, Specimen, Usage
    guidance - issue #51's seeded built-ins). An explicit assumption this
    issue takes, not a PRD requirement - flagged in the PR body as one the
    maintainer can strike."""

    http_status: ClassVar[int] = 409


class PropertyDefinitionKeyExistsError(Exception):
    """Raised by `create_definition` when `key` is already in use -
    `uq_property_definition_key` is the actual database invariant; this is
    the fail-loud, race-safe Python-level layer (see
    `nptc.db.definitions.create_definition`'s own docstring for the
    concurrent-insert handling), matching
    `nptc.catalogue.bindings.CodeBindingCodeAlreadyBoundError`'s own
    precedent of turning a unique-violation race into a typed 409 rather
    than an unhandled `IntegrityError`."""

    http_status: ClassVar[int] = 409


class DeprecatedPropertyWriteError(Exception):
    """Raised by `nptc.catalogue.property_values.save_property_values` when
    the resolved `PropertyDefinition.status` is `deprecated` - FR-11's
    corollary that a deprecated property retains its recorded values but
    accepts no new ones. Named after the specific property key so a caller
    or reviewer can act on it without decoding an entity id."""

    http_status: ClassVar[int] = 422

    def __init__(self, property_key: str) -> None:
        self.property_key = property_key
        super().__init__(f"property {property_key!r} is deprecated and cannot be written to")
