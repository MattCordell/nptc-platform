"""The `string` datatype handler (FR-77, ADR-0013)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as type_cast

from sqlalchemy import ColumnElement, String, cast

from nptc.registry.handlers import (
    ControlKind,
    FilterOp,
    FormControlDescriptor,
    IndexShape,
    PropertyDefinitionSpec,
    SerialisationTarget,
    UnsupportedFilterOpError,
    ValidationIssue,
    ValueExpression,
    jsonb_root_as_text,
)

_TEXTAREA_THRESHOLD = 200
_SUPPORTED_OPS = frozenset({FilterOp.EQUALS, FilterOp.IN, FilterOp.PREFIX})


class StringHandler:
    """Free text. `constraints` may carry `maxLength` (ADR-0012's reserved,
    handler-owned interior)."""

    datatype = "string"

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        fragment: dict[str, Any] = {"type": "string"}
        max_length = spec.constraints.get("maxLength")
        if max_length is not None:
            fragment["maxLength"] = max_length
        return fragment

    def constraints_schema(self) -> Mapping[str, Any]:
        return {
            "type": "object",
            "properties": {"maxLength": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        }

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        if not isinstance(value, str):
            return [ValidationIssue(code="wrong-type", message="value must be a string")]
        max_length = spec.constraints.get("maxLength")
        if max_length is not None and len(value) > max_length:
            return [
                ValidationIssue(
                    code="max-length-exceeded",
                    message=f"value exceeds maxLength of {max_length}",
                )
            ]
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        max_length = spec.constraints.get("maxLength")
        if max_length is not None and max_length > _TEXTAREA_THRESHOLD:
            return FormControlDescriptor(control=ControlKind.TEXTAREA, params={})
        return FormControlDescriptor(control=ControlKind.TEXT, params={})

    def serialise(self, value: Any, target: SerialisationTarget) -> Any:
        return value

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        if not spec.filterable:
            return None
        return IndexShape(expression=ValueExpression.TEXT_SCALAR, requires_conformance_sweep=False)

    def supported_filter_ops(self) -> frozenset[FilterOp]:
        return _SUPPORTED_OPS

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        """`jsonb_root_as_text(column)`, not `cast(column, String)` (issue
        #54, FR-13): the latter renders `CAST(value AS VARCHAR)`, which
        stays JSON-quoted (`'"abc"'`, never `abc`) and so can never match
        an unquoted filter value - see that function's own docstring, and
        `nptc.db.property_indexes.create_statement`, which builds this
        property's `TEXT_SCALAR` index over the identical expression."""
        text_value = jsonb_root_as_text(column)
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", text_value == value)
        if op is FilterOp.IN:
            return type_cast("ColumnElement[bool]", text_value.in_(value))
        if op is FilterOp.PREFIX:
            # autoescape=True: without it, a caller-supplied `%`/`_` in
            # `value` is a LIKE wildcard, not a literal character - "a_c"
            # would otherwise match "abc".
            return text_value.startswith(value, autoescape=True)
        raise UnsupportedFilterOpError(f"string handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        return cast(column, String)
