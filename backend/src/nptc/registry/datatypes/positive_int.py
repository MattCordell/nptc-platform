"""The `positiveInt` datatype handler (FR-77, ADR-0013).

A separate handler, not `decimal` with a `minimum` constraint: the schema
fragment `{"type": "integer", "minimum": 1}` makes `1.5` *unrepresentable*
rather than merely refused at validation time - ADR-0012's "unrepresentable
rather than merely refused" principle, applied here one layer up from the
column CHECK it was first stated against.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as type_cast

from sqlalchemy import ColumnElement, Integer, and_, cast

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
)

_SUPPORTED_OPS = frozenset({FilterOp.EQUALS, FilterOp.IN, FilterOp.RANGE})


class PositiveIntHandler:
    """A positive integer (>= 1)."""

    datatype = "positiveInt"

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        return {"type": "integer", "minimum": 1}

    def constraints_schema(self) -> Mapping[str, Any]:
        return {"type": "object", "additionalProperties": False}

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        if isinstance(value, bool) or not isinstance(value, int):
            return [ValidationIssue(code="wrong-type", message="value must be an integer")]
        if value < 1:
            return [ValidationIssue(code="not-positive", message="value must be >= 1")]
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        return FormControlDescriptor(control=ControlKind.NUMBER, params={"step": 1, "minimum": 1})

    def serialise(self, value: Any, target: SerialisationTarget) -> Any:
        return value

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        if not spec.filterable:
            return None
        return IndexShape(
            expression=ValueExpression.NUMERIC_SCALAR, requires_conformance_sweep=True
        )

    def supported_filter_ops(self) -> frozenset[FilterOp]:
        return _SUPPORTED_OPS

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        numeric_column = cast(column, Integer)
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", numeric_column == value)
        if op is FilterOp.IN:
            return type_cast("ColumnElement[bool]", numeric_column.in_(value))
        if op is FilterOp.RANGE:
            minimum, maximum = value
            return and_(numeric_column >= minimum, numeric_column <= maximum)
        raise UnsupportedFilterOpError(f"positiveInt handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        return cast(column, Integer)
