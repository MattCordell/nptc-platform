"""The datatype handler contract (FR-77, ADR-0013).

Transcribed from ADR-0013 SS10, with one deliberate change: the ADR's ``sort_key``
member is dropped (open question 5, which the ADR explicitly authorises #53 to
resolve either way) - no caller needs it, and an unused member is a cost every
future handler pays. The Protocol below has ten members, not eleven.

``nptc.registry`` is a leaf (ADR-0013 SS2): it may import ``nptc_shared``,
SQLAlchemy, ``jsonschema`` and the stdlib, and nothing else from ``nptc``. This
is what keeps a handler's input a frozen ``PropertyDefinitionSpec`` rather than
the ORM model - #51's storage layer builds one of these from a row, but this
module never imports #51's model to do it.

``datatype`` is plain ``str`` throughout - deliberately not ``enum.Enum`` or a
closed ``typing.Literal`` union, both of which would be a second enumeration of
the valid set that ``BUILTIN_DATATYPES`` (in ``registry.datatypes``) already is.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import ColumnElement

from nptc_shared.terminology import TerminologyClient

# --- value types the registry passes to and receives from a handler -----


@dataclass(frozen=True, slots=True)
class BindingSpec:
    """Populated only when datatype == "code" (FR-10)."""

    binding_target: str  # "value_set" | "local_code_system"
    value_set_uri: str | None
    strength: str  # "required" | "extensible" | "example"
    edition: str


@dataclass(frozen=True, slots=True)
class PropertyDefinitionSpec:
    """The frozen view a handler is given - never the ORM model, so
    registry/ never imports db/ (ADR-0013 SS2) and #53's synthetic-datatype
    test can build one by hand with no database."""

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
    """Not a handler-supplied field (see IndexShape below) - #54 derives it
    from ValueExpression via INDEX_KIND_BY_EXPRESSION, a fixed two-entry
    mapping, so a handler cannot return one of the six IndexKind x
    ValueExpression combinations when only three are meaningful."""

    GIN = "gin"
    EXPRESSION_BTREE = "expression_btree"


class ValueExpression(enum.Enum):
    """Closed set #54's `match` switches over - does not grow when a
    datatype is added (ADR-0013 SS8)."""

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
    """No `kind` field - it is unrepresentable-by-construction that a
    handler pairs GIN with a numeric scalar. #54 looks up IndexKind from
    `expression` via INDEX_KIND_BY_EXPRESSION."""

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


# --- the contract itself -------------------------------------------------


class DatatypeHandler(Protocol):
    """Ten members. Four are FR-77's own sentence (json_schema_fragment,
    validate, form_control, serialise); six are forced by the seams
    ADR-0012 left open. (ADR-0013 names an eleventh, `sort_key`; #53 drops
    it per open question 5 - see the module docstring.)"""

    @property
    def datatype(self) -> str: ...

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]: ...

    def constraints_schema(self) -> Mapping[str, Any]:
        """Validates the *interior* of the constraints JSONB column
        ADR-0012 reserved but did not define (#52)."""
        ...

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        """Local and structural. FR-10's binding check is a live terminology
        call and reaches the server through self, not this method - the
        code handler is constructed with a TerminologyClient (below)."""
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
    """Raised by DatatypeRegistry.__init__() if the handler sequence
    contains two handlers with the same `datatype` - construction-time,
    not a runtime surprise. There is no register() method (ADR-0013 SS4:
    handlers are supplied to the constructor as a tuple, never added one
    at a time)."""


class UnsupportedFilterOpError(ValueError):
    """Raised by filter_clause() for an op absent from
    supported_filter_ops()."""


class UnsupportedBindingError(ValueError):
    """Raised by CodeHandler.validate() when binding_target =
    'local_code_system' and the handler was constructed with
    local_code_lookup=None - a loud refusal, never a silent pass
    (ADR-0013 open question 1). #56 has now supplied LocalCodeLookup's
    real shape below; CodeHandler._validate_binding still returns []
    unconditionally for a supplied lookup rather than calling resolve()
    - wiring that call through is left to a follow-up rather than edited
    here, alongside #53's own merged module."""


# --- the registry and its construction ------------------------------------


class DatatypeRegistry:
    """An instance, not module globals - #53 builds builtins-plus-synthetic
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
        """The runtime valid-datatype set, used by #51's write-time
        registry.get() resolution and startup reconciliation."""
        return frozenset(self._by_datatype)


@dataclass(frozen=True, slots=True)
class ResolvedLocalCode:
    """What a `LocalCodeLookup` returns for a code that exists - just
    enough for `CodeHandler` to validate a `property_value` and render a
    display term, without exposing the ORM row that backs it (`nptc.
    registry` is a leaf - see the module docstring - so this dataclass,
    not `nptc.db.models.local_code.LocalCode`, is what crosses the
    boundary; mirrors `nptc.terminology`'s own served-label-shaped return
    types for the same reason).

    **`status` and `system_status` are deliberately two separate fields.**
    `nptc.catalogue.local_codes.deprecate_local_code_system` deprecates a
    system without touching its member codes' own `status` - the two
    facts are independent, and a handler that only checked `status` would
    treat a code as fine after its owning system had been retired
    wholesale."""

    code: str
    display: str
    status: str
    system_status: str
    provisional: bool


class LocalCodeLookup(Protocol):
    """#56 (FR-90)'s real shape for this Protocol. The read contract a
    `code`-datatype handler needs to validate a value bound to
    `binding_target = 'local_code_system'` (PRD line 415: "validated
    internally against the platform's own `LocalCode` table, because
    Ontoserver does not hold them"). Deliberately narrow - no write
    methods; management goes through `nptc.catalogue.local_codes`, gated
    on `Permission.REGISTRY_MANAGE` (FR-90's "administrator-only
    management"), which is exactly why that module lives outside this
    leaf package rather than in it. `nptc.catalogue.local_codes.
    DatabaseLocalCodeLookup` is the database-backed implementation -
    constructing one, and wiring `CodeHandler.validate()`'s
    `local_code_system` branch to actually call `resolve()` rather than
    return `[]` unconditionally, remains #53's job (see
    `UnsupportedBindingError`'s own docstring and `CodeHandler.
    _validate_binding`'s comment)."""

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
    local_code_lookup: LocalCodeLookup | None = None  # #56, FR-90
