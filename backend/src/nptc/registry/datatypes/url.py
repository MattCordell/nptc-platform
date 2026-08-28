"""The `url` datatype handler (FR-77, ADR-0013)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as type_cast
from urllib.parse import urlsplit

from sqlalchemy import ColumnElement

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
        # `urlsplit` lowercases the parsed scheme (validate() compares
        # against this same tuple), so a constraint of "HTTPS" must be
        # normalised here or it can never match.
        return tuple(scheme.lower() for scheme in schemes)

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
        """`jsonb_root_as_text(column)`, not `cast(column, String)` - see
        `string.py`'s `filter_clause` for why (issue #54, FR-13)."""
        text_value = jsonb_root_as_text(column)
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", text_value == value)
        if op is FilterOp.IN:
            return type_cast("ColumnElement[bool]", text_value.in_(value))
        if op is FilterOp.PREFIX:
            # autoescape=True: see string.py's filter_clause for why.
            return text_value.startswith(value, autoescape=True)
        raise UnsupportedFilterOpError(f"url handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        """`jsonb_root_as_text(column)`, not `cast(column, String)` - see
        `string.py`'s `facet_expression` for why (issue #54 review)."""
        return jsonb_root_as_text(column)
