"""`DatatypeRegistry` tests (FR-77, ADR-0013).

No fixtures - a `DatatypeRegistry` is a plain object built from a sequence of
handlers, so these tests need no database and never start Docker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import ColumnElement

from nptc.registry import (
    ControlKind,
    DatatypeRegistry,
    DuplicateDatatypeError,
    FilterOp,
    FormControlDescriptor,
    PropertyDefinitionSpec,
    SerialisationTarget,
    UnknownDatatypeError,
    ValidationIssue,
)


class _StubHandler:
    """The minimum structural shape of a `DatatypeHandler` - just enough to
    exercise `DatatypeRegistry` without depending on any builtin handler."""

    def __init__(self, datatype: str) -> None:
        self.datatype = datatype

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        return {"type": "string"}

    def constraints_schema(self) -> Mapping[str, Any]:
        return {}

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        return FormControlDescriptor(control=ControlKind.TEXT, params={})

    def serialise(self, value: Any, target: SerialisationTarget) -> Any:
        return value

    def index_shape(self, spec: PropertyDefinitionSpec) -> None:
        return None

    def supported_filter_ops(self) -> frozenset[FilterOp]:
        return frozenset()

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        raise NotImplementedError

    def facet_expression(self, column: ColumnElement[Any]) -> None:
        return None


@pytest.mark.req("FR-77")
def test_get_raises_for_unregistered_datatype_never_a_default() -> None:
    """FR-16's stated cost: a default/fallback handler makes a property
    silently unfilterable. `get()` must raise, not return `None` and not a
    fallback handler, for a datatype nothing registered."""
    registry = DatatypeRegistry([_StubHandler("string")])

    with pytest.raises(UnknownDatatypeError, match="unregistered"):
        registry.get("unregistered")


@pytest.mark.req("FR-77")
def test_unknown_datatype_error_names_the_registered_set() -> None:
    """The error message is the "clear no-handler-registered error" #53's
    acceptance criterion requires, not a silent fallthrough - it must name
    what *is* registered so the caller can act on it."""
    registry = DatatypeRegistry([_StubHandler("string"), _StubHandler("decimal")])

    with pytest.raises(UnknownDatatypeError) as exc_info:
        registry.get("boolean")

    message = str(exc_info.value)
    assert "decimal" in message
    assert "string" in message


@pytest.mark.req("FR-77")
def test_get_on_empty_registry_raises() -> None:
    """The principal failure mode at the boundary: a registry built with no
    handlers at all still raises rather than crashing some other way."""
    registry = DatatypeRegistry([])

    with pytest.raises(UnknownDatatypeError):
        registry.get("string")


@pytest.mark.req("FR-77")
def test_duplicate_datatype_raises_at_construction_not_at_get() -> None:
    """ADR-0013 SS4: two handlers claiming the same datatype is a
    construction-time error, not a runtime surprise discovered later at
    `get()`."""
    with pytest.raises(DuplicateDatatypeError, match="string"):
        DatatypeRegistry([_StubHandler("string"), _StubHandler("string")])


@pytest.mark.req("FR-77")
def test_known_datatypes_reflects_exactly_what_was_registered() -> None:
    registry = DatatypeRegistry([_StubHandler("string"), _StubHandler("decimal")])

    assert registry.known_datatypes() == frozenset({"string", "decimal"})


@pytest.mark.req("FR-77")
def test_two_registries_built_from_different_handlers_do_not_share_state() -> None:
    """`DatatypeRegistry` is an instance, not module globals (ADR-0013 SS4) -
    #53's synthetic-datatype test depends on being able to build a
    builtins-plus-synthetic registry without mutating any registry any other
    test or caller holds."""
    first = DatatypeRegistry([_StubHandler("string")])
    second = DatatypeRegistry([_StubHandler("string"), _StubHandler("duration")])

    assert first.known_datatypes() == frozenset({"string"})
    assert second.known_datatypes() == frozenset({"string", "duration"})
    with pytest.raises(UnknownDatatypeError):
        first.get("duration")
