"""The `decimal` datatype handler (FR-77, ADR-0013)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from numbers import Real
from typing import Any
from typing import cast as type_cast

from sqlalchemy import ColumnElement, Numeric, and_, cast

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

_SUPPORTED_OPS = frozenset({FilterOp.EQUALS, FilterOp.RANGE})


class DecimalHandler:
    """A real number. Not `positiveInt` with a `minimum` constraint -
    `positiveInt` is its own handler (see positive_int.py's docstring)."""

    datatype = "decimal"

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        return {"type": "number"}

    def constraints_schema(self) -> Mapping[str, Any]:
        return {"type": "object", "additionalProperties": False}

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        # `decimal.Decimal` registers as `numbers.Number`, not
        # `numbers.Real` - checked explicitly, since a JSONB/Numeric
        # round-trip commonly hands a handler a Decimal, not a float.
        if isinstance(value, bool) or not isinstance(value, Real | Decimal):
            return [ValidationIssue(code="wrong-type", message="value must be a number")]
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        return FormControlDescriptor(control=ControlKind.NUMBER, params={"step": "any"})

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
        numeric_column = cast(column, Numeric)
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", numeric_column == value)
        if op is FilterOp.RANGE:
            minimum, maximum = value
            return and_(numeric_column >= minimum, numeric_column <= maximum)
        raise UnsupportedFilterOpError(f"decimal handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        # Faceting a continuous value is meaningless - every value is its own
        # facet, so there is nothing to group by (ADR-0013's table).
        return None
