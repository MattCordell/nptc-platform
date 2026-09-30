"""Property registry and datatype handler registry (FR-09 to FR-13, FR-77).

A leaf package (ADR-0013 SS2): it may import ``nptc_shared``, SQLAlchemy, ``jsonschema`` and
the stdlib, and nothing else from ``nptc``. The only datatype ``switch``/``match`` in the
codebase lives in ``registry/datatypes/``, enforced by
``backend/tests/test_datatype_dispatch.py``.

``registry/definitions.py`` holds the PropertyDefinition typed errors and the
``DefinitionAudience`` vocabulary, and ``registry/schema.py`` derives JSON Schema. The
ORM-backed service (create, amend, deprecate, list) is ``nptc.db.definitions``, and the DDL
executor for automatic index generation (FR-13) is ``nptc.db.property_indexes``. Both live
under ``nptc.db`` because they import the ``PropertyDefinition`` ORM model, which the leaf rule
keeps out of this package.
"""

from nptc.registry.datatypes import BUILTIN_DATATYPES, build_builtin_handlers
from nptc.registry.definitions import (
    DefinitionAudience,
    DeprecatedPropertyWriteError,
    PropertyAlreadyDeprecatedError,
    PropertyDefinitionDeleteRefusedError,
    PropertyDefinitionKeyExistsError,
    PropertyKeyImmutableError,
    PropertyReactivationRefusedError,
    SystemPropertyDeprecationRefusedError,
)
from nptc.registry.handlers import (
    INDEX_KIND_BY_EXPRESSION,
    BindingSpec,
    ControlKind,
    DatatypeHandler,
    DatatypeRegistry,
    DuplicateDatatypeError,
    FilterOp,
    FormControlDescriptor,
    HandlerDeps,
    IndexKind,
    IndexShape,
    LocalCodeLookup,
    PropertyDefinitionSpec,
    ResolvedLocalCode,
    SerialisationTarget,
    UnknownDatatypeError,
    UnsupportedBindingError,
    UnsupportedFilterOpError,
    ValidationIssue,
    ValueExpression,
    jsonb_root_as_text,
)

__all__ = [
    "BUILTIN_DATATYPES",
    "INDEX_KIND_BY_EXPRESSION",
    "BindingSpec",
    "ControlKind",
    "DatatypeHandler",
    "DatatypeRegistry",
    "DefinitionAudience",
    "DeprecatedPropertyWriteError",
    "DuplicateDatatypeError",
    "FilterOp",
    "FormControlDescriptor",
    "HandlerDeps",
    "IndexKind",
    "IndexShape",
    "LocalCodeLookup",
    "PropertyAlreadyDeprecatedError",
    "PropertyDefinitionDeleteRefusedError",
    "PropertyDefinitionKeyExistsError",
    "PropertyDefinitionSpec",
    "PropertyKeyImmutableError",
    "PropertyReactivationRefusedError",
    "ResolvedLocalCode",
    "SerialisationTarget",
    "SystemPropertyDeprecationRefusedError",
    "UnknownDatatypeError",
    "UnsupportedBindingError",
    "UnsupportedFilterOpError",
    "ValidationIssue",
    "ValueExpression",
    "build_builtin_handlers",
    "jsonb_root_as_text",
]
