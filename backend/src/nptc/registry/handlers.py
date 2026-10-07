"""The datatype handler contract (FR-77, ADR-0013).

The Protocol has eleven members: ADR-0013's ten (``sort_key`` was dropped, its open question 5)
and ``uses_binding``.

``nptc.registry`` is a leaf (ADR-0013 SS2): it may import ``nptc_shared``, SQLAlchemy,
``jsonschema`` and the stdlib, and nothing else from ``nptc``. A handler's input is therefore
a frozen ``PropertyDefinitionSpec``, which the storage layer builds from a row, never the ORM
model.

``datatype`` is a plain ``str`` throughout, not an ``enum.Enum`` or a closed ``Literal``: either
would be a second enumeration of the valid set that ``BUILTIN_DATATYPES`` already is.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from typing import cast as type_cast

from sqlalchemy import ColumnElement, Text, literal_column

from nptc_shared.terminology import TerminologyClient

# --- value types the registry passes to and receives from a handler -----


@dataclass(frozen=True, slots=True)
class BindingSpec:
    """Populated only when datatype == "code" (FR-10)."""

    binding_target: str  # "value_set" | "local_code_system"
    value_set_uri: str | None
    strength: str  # "required" | "extensible" | "example"
    edition: str
    #: Names the `LocalCodeSystem.key` a `LocalCodeLookup` resolves against. Populated only when
    #: `binding_target == "local_code_system"` (FR-10, FR-90); `None` for a value-set binding.
    local_code_system_key: str | None = None


@dataclass(frozen=True, slots=True)
class PropertyDefinitionSpec:
    """The frozen view a handler is given, never the ORM model, so `registry/` does not import
    `db/` (ADR-0013 SS2) and a test can build one by hand with no database."""

    key: str
    label: str
    datatype: str
    cardinality: str  # "0..1" | "1..1" | "0..*" | "1..*"
    scope: frozenset[str]  # subset of {"submission", "maintenance"}
    required_for_submission: bool
    required_for_publication: bool
    binding: BindingSpec | None
    filterable: bool
    constraints: Mapping[str, Any]


class ControlKind(enum.Enum):
    """Named after the interaction, never after a datatype (ADR-0013 SS3)."""

    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    URI = "uri"
    CONCEPT_PICKER = "concept_picker"


@dataclass(frozen=True, slots=True)
class FormControlDescriptor:
    control: ControlKind
    params: Mapping[str, Any]  # JSON-serialisable only


class SerialisationTarget(enum.Enum):
    """Representations, not export formats (ADR-0013 SS7)."""

    PLAIN_TEXT = "plain_text"
    JSON = "json"
    FHIR_VALUE = "fhir_value"


class IndexKind(enum.Enum):
    """Not a handler-supplied field (see `IndexShape`). It is derived from `ValueExpression`
    through `INDEX_KIND_BY_EXPRESSION`, so a handler cannot return one of the six `IndexKind` x
    `ValueExpression` combinations when only three are meaningful."""

    GIN = "gin"
    EXPRESSION_BTREE = "expression_btree"


class ValueExpression(enum.Enum):
    """Closed set that index generation (`nptc.db.property_indexes`) switches over. It does not
    grow when a datatype is added (ADR-0013 SS8)."""

    RAW_JSONB = "raw_jsonb"
    TEXT_SCALAR = "text_scalar"
    NUMERIC_SCALAR = "numeric_scalar"


INDEX_KIND_BY_EXPRESSION: Mapping[ValueExpression, IndexKind] = {
    ValueExpression.RAW_JSONB: IndexKind.GIN,
    ValueExpression.TEXT_SCALAR: IndexKind.EXPRESSION_BTREE,
    ValueExpression.NUMERIC_SCALAR: IndexKind.EXPRESSION_BTREE,
}


@dataclass(frozen=True, slots=True)
class IndexShape:
    """No `kind` field: a handler cannot pair GIN with a numeric scalar, because
    `INDEX_KIND_BY_EXPRESSION` derives the `IndexKind` from `expression`."""

    expression: ValueExpression
    requires_conformance_sweep: bool


class FilterOp(enum.Enum):
    EQUALS = "equals"
    IN = "in"
    PREFIX = "prefix"
    RANGE = "range"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str | None = None  # ordinal / sub-field, for multi-valued properties


def jsonb_root_as_text(column: ColumnElement[Any]) -> ColumnElement[Any]:
    """``value #>> '{}'``: the whole JSONB document's own scalar text, unquoted (FR-13,
    ADR-0012's `TEXT_SCALAR`/`NUMERIC_SCALAR` index expression).

    Every `filter_clause()` serving those two shapes (`string`, `url`, `decimal`,
    `positiveInt`) builds its predicate from this, never `cast(column, String)`. The cast
    renders `CAST(value AS VARCHAR)`, which stays JSON-quoted (`'"abc"'`, not `abc`) and so
    never matches an unquoted filter value. For `decimal` and `positiveInt`,
    `CAST(value AS NUMERIC)` raises when the retained value is a JSONB *string*, the "cannot
    cast jsonb string to type numeric" failure ADR-0027 closes.

    `nptc.db.property_indexes.create_statement` composes the identical `(value #>> '{{}}')`
    text for the index (braces doubled because `sql.SQL.format` uses `str.format`). The two
    must not drift apart; `test_datatype_handlers.py`'s parity test asserts it.

    `literal_column`, not a bound parameter: the right-hand side is the fixed empty path
    `'{}'`, never caller data, so NFR-22 has nothing to flag.

    `return_type=Text`: a bare `.op(...)` result is untyped, which would drop `StringHandler`
    and `UrlHandler`'s `.startswith(..., autoescape=True)`, a `String` comparator behaviour."""
    return type_cast(
        "ColumnElement[Any]", column.op("#>>", return_type=Text)(literal_column("'{}'"))
    )


# --- the contract itself -------------------------------------------------


class DatatypeHandler(Protocol):
    """Eleven members. Four are FR-77's own sentence (`json_schema_fragment`, `validate`,
    `form_control`, `serialise`); six are forced by the seams ADR-0012 left open; one is `uses_binding`."""

    @property
    def datatype(self) -> str: ...

    @property
    def uses_binding(self) -> bool:
        """Whether a property of this datatype takes a terminology binding. The database
        refuses a mismatch either way."""
        ...

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]: ...

    def constraints_schema(self) -> Mapping[str, Any]:
        """Validates the *interior* of the `constraints` JSONB column, which ADR-0012 reserved
        but did not define."""
        ...

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        """Local and structural. FR-10's binding check is a live terminology call that reaches
        the server through `self`: the code handler is constructed with a `TerminologyClient`."""
        ...

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor: ...

    def serialise(self, value: Any, target: SerialisationTarget) -> Any: ...

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        """None where indexing is meaningless for this datatype."""
        ...

    def supported_filter_ops(self) -> frozenset[FilterOp]: ...

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        """A SQLAlchemy expression, never a string - NFR-22 holds by
        construction, not by review."""
        ...

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        """None where faceting is meaningless (e.g. decimal)."""
        ...


# --- errors ---------------------------------------------------------------


class UnknownDatatypeError(LookupError):
    """Raised by DatatypeRegistry.get() for an unregistered datatype -
    never a default, never a silent fallthrough (FR-16's stated cost)."""


class DuplicateDatatypeError(ValueError):
    """Raised by `DatatypeRegistry.__init__()` when two handlers share a `datatype`, at
    construction and not as a runtime surprise. There is no `register()`: handlers are supplied
    as a tuple to the constructor (ADR-0013 SS4)."""


class UnsupportedFilterOpError(ValueError):
    """Raised by filter_clause() for an op absent from
    supported_filter_ops()."""


class UnsupportedBindingError(ValueError):
    """Raised by `CodeHandler.validate()` for a misconfigured `local_code_system` binding: the
    handler has no `LocalCodeLookup`, or the binding's `local_code_system_key` is `None`. A loud
    refusal, never a silent pass (ADR-0013 open question 1)."""


# --- the registry and its construction ------------------------------------


class DatatypeRegistry:
    """An instance, not module globals, so a test can build builtins plus a synthetic handler
    without mutating shared state."""

    def __init__(self, handlers: Sequence[DatatypeHandler]) -> None:
        by_datatype: dict[str, DatatypeHandler] = {}
        for handler in handlers:
            if handler.datatype in by_datatype:
                raise DuplicateDatatypeError(
                    f"handler for datatype {handler.datatype!r} registered more than once"
                )
            by_datatype[handler.datatype] = handler
        self._by_datatype = by_datatype

    def get(self, datatype: str) -> DatatypeHandler:
        """No default, no fallback. Return type is not Optional."""
        try:
            return self._by_datatype[datatype]
        except KeyError:
            known = ", ".join(sorted(self._by_datatype)) or "(none registered)"
            raise UnknownDatatypeError(
                f"no handler registered for datatype {datatype!r}; known datatypes: {known}"
            ) from None

    def known_datatypes(self) -> frozenset[str]:
        """The runtime set of valid datatypes, used for write-time resolution and startup
        reconciliation."""
        return frozenset(self._by_datatype)

    def handlers(self) -> tuple[DatatypeHandler, ...]:
        return tuple(self._by_datatype[name] for name in sorted(self._by_datatype))


@dataclass(frozen=True, slots=True)
class ResolvedLocalCode:
    """What a `LocalCodeLookup` returns for a code that exists: enough for `CodeHandler` to
    validate a `property_value` and render a display term, without exposing the ORM row.
    `nptc.registry` is a leaf, so this dataclass crosses the boundary instead of
    `nptc.db.models.local_code.LocalCode`.

    **`status` and `system_status` are deliberately separate.**
    `nptc.catalogue.local_codes.deprecate_local_code_system` deprecates a system without
    touching its member codes' `status`. A handler that checked only `status` would treat a
    code as fine after its system was retired wholesale."""

    code: str
    display: str
    status: str
    system_status: str
    provisional: bool


class LocalCodeLookup(Protocol):
    """The read contract a `code` handler needs to validate a value bound to
    `binding_target = 'local_code_system'` (FR-10, FR-90): such codes are validated against the
    platform's own `LocalCode` table because Ontoserver does not hold them. Deliberately
    narrow, with no write methods: management goes through `nptc.catalogue.local_codes`, gated
    on `Permission.REGISTRY_MANAGE` (FR-90), which is why that module lives outside this leaf
    package. `nptc.catalogue.local_codes.DatabaseLocalCodeLookup` is the database-backed
    implementation."""

    def resolve(self, system_key: str, code: str) -> ResolvedLocalCode | None:
        """Returns the resolved code, or `None` if `code` does not exist
        in the `system_key` system at all - a handler distinguishes "does
        not exist" from "exists but deprecated" via `ResolvedLocalCode.
        status`, the same distinction `code_binding.status` supports for
        SNOMED bindings."""
        ...


@dataclass(frozen=True, slots=True)
class HandlerDeps:
    """Constructor dependencies for builtin handlers - handlers are
    constructed, not imported as singletons, so StubTerminologyClient
    needs no second injection mechanism (NFR-37)."""

    terminology_client: TerminologyClient
    local_code_lookup: LocalCodeLookup | None = None  # FR-90
