"""The `url` datatype handler (FR-77, ADR-0013)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as type_cast
from urllib.parse import urlsplit

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
)

_SUPPORTED_OPS = frozenset({FilterOp.EQUALS, FilterOp.IN, FilterOp.PREFIX})
_DEFAULT_SCHEMES: tuple[str, ...] = ("https",)


class UrlHandler:
    """A URI. `constraints` may carry `schemes` (defaults to `["https"])`)."""

    datatype = "url"

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        return {"type": "string", "format": "uri"}

    def constraints_schema(self) -> Mapping[str, Any]:
        return {
            "type": "object",
            "properties": {"schemes": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        }

    def _allowed_schemes(self, spec: PropertyDefinitionSpec) -> tuple[str, ...]:
        schemes = spec.constraints.get("schemes")
        if schemes is None:
            return _DEFAULT_SCHEMES
        return tuple(schemes)

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        if not isinstance(value, str):
            return [ValidationIssue(code="wrong-type", message="value must be a string")]
        parsed = urlsplit(value)
        allowed = self._allowed_schemes(spec)
        if not parsed.scheme or not parsed.netloc:
            return [ValidationIssue(code="not-a-url", message="value is not an absolute URL")]
        if parsed.scheme not in allowed:
            return [
                ValidationIssue(
                    code="scheme-not-allowed",
                    message=f"scheme {parsed.scheme!r} not in {allowed}",
                )
            ]
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        return FormControlDescriptor(
            control=ControlKind.URI, params={"schemes": list(self._allowed_schemes(spec))}
        )

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
        text_column = cast(column, String)
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", text_column == value)
        if op is FilterOp.IN:
            return type_cast("ColumnElement[bool]", text_column.in_(value))
        if op is FilterOp.PREFIX:
            return text_column.startswith(value)
        raise UnsupportedFilterOpError(f"url handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        return cast(column, String)
