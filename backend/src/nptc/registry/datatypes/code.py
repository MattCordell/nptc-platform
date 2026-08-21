"""The `code` datatype handler (FR-77, ADR-0013).

The one handler with its own constructor arguments beyond the Protocol - a
`TerminologyClient` for FR-10's live binding check, and an optional
`LocalCodeLookup` for `binding_target == "local_code_system"` (#56, FR-90).
`__init__` is part of the concrete class, not the ten-member contract every
handler satisfies structurally.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as type_cast

from sqlalchemy import ColumnElement

from nptc.registry.handlers import (
    ControlKind,
    FilterOp,
    FormControlDescriptor,
    IndexShape,
    LocalCodeLookup,
    PropertyDefinitionSpec,
    SerialisationTarget,
    UnsupportedBindingError,
    UnsupportedFilterOpError,
    ValidationIssue,
    ValueExpression,
)
from nptc_shared.sctid import has_valid_check_digit, has_valid_format
from nptc_shared.terminology import SNOMED_SYSTEM, Edition, TerminologyClient

_SUPPORTED_OPS = frozenset({FilterOp.EQUALS, FilterOp.IN})
_REQUIRED_KEYS = frozenset({"system", "code"})


class CodeHandler:
    """A coded value: `{"system": ..., "code": ..., "display": ...}`.

    `validate()` is local and structural (shape, and for a SNOMED CT system,
    SCTID format/check-digit); the live binding check against a value set or
    local code system reaches the terminology server through `self`.
    """

    datatype = "code"

    def __init__(
        self,
        terminology_client: TerminologyClient,
        local_code_lookup: LocalCodeLookup | None = None,
    ) -> None:
        self._terminology = terminology_client
        self._local_code_lookup = local_code_lookup

    def json_schema_fragment(self, spec: PropertyDefinitionSpec) -> Mapping[str, Any]:
        return {
            "type": "object",
            "properties": {
                "system": {"type": "string"},
                "code": {"type": "string"},
                "display": {"type": "string"},
            },
            "required": ["system", "code"],
            "additionalProperties": False,
        }

    def constraints_schema(self) -> Mapping[str, Any]:
        return {"type": "object", "additionalProperties": False}

    def validate(self, value: Any, spec: PropertyDefinitionSpec) -> Sequence[ValidationIssue]:
        if not isinstance(value, Mapping):
            return [ValidationIssue(code="wrong-type", message="value must be a coding object")]
        missing = _REQUIRED_KEYS - value.keys()
        if missing:
            return [
                ValidationIssue(
                    code="missing-field", message=f"missing required field(s): {sorted(missing)}"
                )
            ]
        issues: list[ValidationIssue] = []
        system = value["system"]
        code = value["code"]
        if system == SNOMED_SYSTEM:
            if not has_valid_format(code):
                issues.append(
                    ValidationIssue(code="invalid-sctid-format", message=f"{code!r} is not a SCTID")
                )
            elif not has_valid_check_digit(code):
                issues.append(
                    ValidationIssue(
                        code="invalid-sctid-check-digit",
                        message=f"{code!r} fails the Verhoeff check digit",
                    )
                )
        if spec.binding is not None:
            issues.extend(self._validate_binding(code, spec))
        return issues

    def _validate_binding(
        self, code: str, spec: PropertyDefinitionSpec
    ) -> Sequence[ValidationIssue]:
        binding = spec.binding
        assert binding is not None  # narrowed by the caller
        if binding.binding_target == "local_code_system":
            if self._local_code_lookup is None:
                raise UnsupportedBindingError(
                    "local_code_system binding requires a LocalCodeLookup, "
                    "none was supplied to CodeHandler (see #56/FR-90)"
                )
            # #56 has not yet defined LocalCodeLookup's shape (ADR-0013 open
            # question 1) - nothing further to call here until it does.
            return []
        edition = Edition(module_id=binding.edition, label=binding.edition)
        result = self._terminology.validate_code(
            code, edition=edition, value_set_url=binding.value_set_uri
        )
        if not result.result:
            return [
                ValidationIssue(
                    code="not-in-value-set",
                    message=result.message or f"{code!r} is not valid for {binding.value_set_uri}",
                )
            ]
        return []

    def form_control(self, spec: PropertyDefinitionSpec) -> FormControlDescriptor:
        binding = spec.binding
        value_set_uri = binding.value_set_uri if binding is not None else None
        strength = binding.strength if binding is not None else None
        edition = binding.edition if binding is not None else None
        return FormControlDescriptor(
            control=ControlKind.CONCEPT_PICKER,
            params={
                "valueSetUri": value_set_uri,
                "strength": strength,
                "edition": edition,
                # Computed here, never by the frontend, so it never branches
                # on `strength` (ADR-0013 SS3).
                "allowJustification": strength == "extensible",
            },
        )

    def serialise(self, value: Any, target: SerialisationTarget) -> Any:
        # No handler may strip a semantic tag (FR-83) - this handler never
        # touches `display`/FSN text at all, so there is nothing to strip.
        return dict(value)

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        if not spec.filterable:
            return None
        return IndexShape(expression=ValueExpression.RAW_JSONB, requires_conformance_sweep=False)

    def supported_filter_ops(self) -> frozenset[FilterOp]:
        return _SUPPORTED_OPS

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        if op is FilterOp.EQUALS:
            return type_cast("ColumnElement[bool]", column["code"].astext == value)
        if op is FilterOp.IN:
            return type_cast("ColumnElement[bool]", column["code"].astext.in_(value))
        raise UnsupportedFilterOpError(f"code handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        # Grouping key (code alone vs (system, code)) is undecided pending
        # #56 - ADR-0013 open question 2, deferred to #139.
        return type_cast("ColumnElement[Any]", column["code"].astext)
