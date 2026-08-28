"""Property registry and datatype handler registry (FR-09 to FR-13, FR-77).

A leaf package (ADR-0013 SS2): may import ``nptc_shared``, SQLAlchemy,
``jsonschema`` and the stdlib, and nothing else from ``nptc``. Everyone else
imports it. The only datatype ``switch``/``match`` in the codebase may live in
``registry/datatypes/`` - enforced by ``backend/tests/test_datatype_dispatch.py``.

``registry/definitions.py`` (PropertyDefinition service, #51/#55) is not yet
implemented. ``registry/schema.py`` (JSON Schema derivation, #52) is.

``nptc.db.property_indexes`` (issue #54, FR-13) - the DDL executor for
automatic index generation - lives under ``nptc.db``, not here, for the
same leaf-rule reason ``nptc.db.bootstrap`` does: it imports the
``PropertyDefinition`` ORM model directly.
"""

from nptc.registry.datatypes import BUILTIN_DATATYPES, build_builtin_handlers
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
    "DuplicateDatatypeError",
    "FilterOp",
    "FormControlDescriptor",
    "HandlerDeps",
    "IndexKind",
    "IndexShape",
    "LocalCodeLookup",
    "PropertyDefinitionSpec",
    "ResolvedLocalCode",
    "SerialisationTarget",
    "UnknownDatatypeError",
    "UnsupportedBindingError",
    "UnsupportedFilterOpError",
    "ValidationIssue",
    "ValueExpression",
    "build_builtin_handlers",
    "jsonb_root_as_text",
]
