"""FR-77's own acceptance criterion, made executable: a single test that
registers a wholly synthetic new datatype and exercises it end to end
through save, validate, filter and export - using only a new handler.

This file's own diff is the claim: adding `duration` below touches nothing
in `backend/src/nptc/` - no storage, no export renderer, no search layer -
because none of those layers exist yet (#51/#52/#54 are still open). "End to
end" is therefore read at the contract level (agreed with the maintainer,
see the plan comment on #53): `validate()` for save+validate,
`filter_clause()`/`facet_expression()` compiled to SQL text against a
fabricated `Table` (no database, no container), and `serialise()` across all
three `SerialisationTarget` members for export. `grep -rn "duration"
backend/src/` returning nothing is the honest form of "this test's diff
touches only itself" while those layers remain unbuilt.

No fixtures - a `DurationHandler` is a plain object, and building a second,
independent `DatatypeRegistry` around it needs no database.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.dialects import postgresql

from nptc.registry import (
    ControlKind,
    DatatypeRegistry,
    FilterOp,
    FormControlDescriptor,
    HandlerDeps,
    IndexShape,
    PropertyDefinitionSpec,
    SerialisationTarget,
    UnknownDatatypeError,
    ValidationIssue,
    ValueExpression,
    build_builtin_handlers,
)
from nptc_shared.terminology.stub import StubTerminologyClient

_DURATION_RE = re.compile(r"^P(?:\d+Y)?(?:\d+M)?(?:\d+D)?$")


class DurationHandler:
    """A synthetic datatype - a whole number of days, stored as an ISO 8601
    duration string (`"P3D"`) and serialised as an integer day-count.

    Not one of PRD SS6.5's five and never registered as a builtin - it
    exists only inside this test, to prove FR-77's extensibility claim
    without pre-registering a speculative real datatype.
    """

    datatype = "duration"

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        return {"type": "string", "pattern": _DURATION_RE.pattern}

    def constraints_schema(self) -> Mapping[str, Any]:
        return {"type": "object", "additionalProperties": False}

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        if not isinstance(value, str) or not _DURATION_RE.fullmatch(value):
            return [
                ValidationIssue(
                    code="not-a-duration", message=f"{value!r} is not an ISO 8601 duration"
                )
            ]
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        # A duration selects an existing interaction (plain text) - no new
        # ControlKind, no frontend edit (ADR-0013 SS3).
        return FormControlDescriptor(control=ControlKind.TEXT, params={})

    def _to_days(self, value: str) -> int:
        match = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?", value)
        assert match is not None
        years, months, days = (int(group) if group else 0 for group in match.groups())
        return years * 365 + months * 30 + days

    def serialise(self, value: Any, target: SerialisationTarget) -> Any:
        if target is SerialisationTarget.PLAIN_TEXT:
            return value
        if target is SerialisationTarget.JSON:
            return {"iso8601": value, "days": self._to_days(value)}
        if target is SerialisationTarget.FHIR_VALUE:
            return self._to_days(value)
        raise AssertionError(f"unhandled SerialisationTarget: {target}")

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        if not spec.filterable:
            return None
        return IndexShape(
            expression=ValueExpression.NUMERIC_SCALAR, requires_conformance_sweep=True
        )

    def supported_filter_ops(self) -> frozenset[FilterOp]:
        return frozenset({FilterOp.EQUALS})

    def filter_clause(self, op: FilterOp, value: Any, column: Any) -> Any:
        if op is FilterOp.EQUALS:
            return column == self._to_days(value)
        raise AssertionError(f"unhandled FilterOp: {op}")

    def facet_expression(self, column: Any) -> Any:
        return column


def _spec(**overrides: object) -> PropertyDefinitionSpec:
    defaults: dict[str, object] = {
        "key": "turnaround_time",
        "label": "Turnaround time",
        "datatype": "duration",
        "cardinality": "0..1",
        "scope": frozenset({"maintenance"}),
        "required_for_submission": False,
        "required_for_publication": False,
        "binding": None,
        "filterable": True,
        "constraints": {},
    }
    defaults.update(overrides)
    return PropertyDefinitionSpec(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def registry_with_duration() -> DatatypeRegistry:
    """Builtins-plus-synthetic, built without mutating any other registry -
    `DatatypeRegistry` is an instance, not module globals (ADR-0013 SS4)."""
    deps = HandlerDeps(terminology_client=StubTerminologyClient())
    return DatatypeRegistry([*build_builtin_handlers(deps), DurationHandler()])


@pytest.mark.req("FR-77")
def test_save_and_validate_a_well_formed_synthetic_value(
    registry_with_duration: DatatypeRegistry,
) -> None:
    handler = registry_with_duration.get("duration")

    assert handler.validate("P3D", _spec()) == []


@pytest.mark.req("FR-77")
def test_save_and_validate_rejects_a_malformed_synthetic_value(
    registry_with_duration: DatatypeRegistry,
) -> None:
    handler = registry_with_duration.get("duration")

    issues = handler.validate("three days", _spec())

    assert [issue.code for issue in issues] == ["not-a-duration"]


@pytest.mark.req("FR-77")
def test_filter_a_synthetic_value_with_no_database(
    registry_with_duration: DatatypeRegistry,
) -> None:
    handler = registry_with_duration.get("duration")
    assert handler.supported_filter_ops() == frozenset({FilterOp.EQUALS})

    table = Table("t", MetaData(), Column("value", Integer))
    clause = handler.filter_clause(FilterOp.EQUALS, "P3D", table.c.value)
    compiled = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "3" in compiled

    facet = handler.facet_expression(table.c.value)
    assert facet is not None


@pytest.mark.req("FR-77")
def test_export_a_synthetic_value_across_every_serialisation_target(
    registry_with_duration: DatatypeRegistry,
) -> None:
    handler = registry_with_duration.get("duration")

    assert handler.serialise("P3D", SerialisationTarget.PLAIN_TEXT) == "P3D"
    assert handler.serialise("P3D", SerialisationTarget.JSON) == {"iso8601": "P3D", "days": 3}
    assert handler.serialise("P3D", SerialisationTarget.FHIR_VALUE) == 3


@pytest.mark.req("FR-77")
def test_removing_the_handler_fails_loudly_not_a_silent_fallthrough() -> None:
    """Stands in for "removing a handler module": a registry built without
    `DurationHandler` behaves exactly as if the module had been deleted -
    `get("duration")` raises, naming the registered set, rather than
    returning `None` or a default handler (#53's acceptance criterion)."""
    deps = HandlerDeps(terminology_client=StubTerminologyClient())
    registry_without_duration = DatatypeRegistry(build_builtin_handlers(deps))

    with pytest.raises(UnknownDatatypeError, match="duration"):
        registry_without_duration.get("duration")
