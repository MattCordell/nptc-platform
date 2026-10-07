"""The `code` datatype handler (FR-77, ADR-0013).

The one handler with constructor arguments beyond the Protocol: a `TerminologyClient` for
FR-10's live binding check, and an optional `LocalCodeLookup` for
`binding_target == "local_code_system"` (FR-90). `__init__` belongs to the concrete class, not
to the ten-member contract every handler satisfies structurally.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from typing import cast as type_cast

from sqlalchemy import ColumnElement, false, or_

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
        # `forbidden_codes`: the seam ADR-0012 reserved for a property-specific rule
        # expressed as data, not a hardcoded property key.
        return {
            "type": "object",
            "properties": {"forbidden_codes": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        }

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
        system = value["system"]
        code = value["code"]
        # `json_schema_fragment` declares both as strings. A numeric `code` must fail here as a
        # ValidationIssue, not raise TypeError in `has_valid_format` or reach storage as a
        # number (FR-06).
        if not isinstance(system, str) or not isinstance(code, str):
            return [
                ValidationIssue(
                    code="wrong-type", message="'system' and 'code' must both be strings"
                )
            ]
        forbidden_codes = spec.constraints.get("forbidden_codes")
        # Defensive against a malformed `constraints` document. `validate_constraints` should
        # catch `forbidden_codes` stored as a bare string, but a `str` iterates as characters
        # and must not fail open by forbidding single letters while missing the intended codes.
        if isinstance(forbidden_codes, list) and code.casefold() in {
            forbidden.casefold() for forbidden in forbidden_codes if isinstance(forbidden, str)
        }:
            # Checked before the SCTID and binding checks so a forbidden code is refused on
            # its own terms, not reported beside an unrelated complaint.
            return [
                ValidationIssue(
                    code="forbidden-code",
                    message=(
                        f"{code!r} is not a valid value for {spec.label} - leave it "
                        "unrecorded rather than entering this value"
                    ),
                )
            ]
        issues: list[ValidationIssue] = []
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
        """Raises `UnsupportedBindingError` for a *misconfigured binding*, never for a bad
        *value*. A definition whose binding this handler cannot service is a configuration
        defect the value could not have avoided, so it is not a `ValidationIssue` (ADR-0013
        open question 1)."""
        binding = spec.binding
        assert binding is not None  # narrowed by the caller
        if binding.binding_target == "local_code_system":
            if self._local_code_lookup is None:
                raise UnsupportedBindingError(
                    "local_code_system binding requires a LocalCodeLookup, "
                    "none was supplied to CodeHandler (see #56/FR-90)"
                )
            if binding.local_code_system_key is None:
                raise UnsupportedBindingError(
                    "binding_target 'local_code_system' requires a "
                    "local_code_system_key; got None - this is a malformed "
                    "PropertyDefinitionSpec, not a bad value"
                )
            return self._validate_local_code_binding(code, binding.local_code_system_key)
        if binding.value_set_uri is None:
            raise UnsupportedBindingError(
                "binding_target 'value_set' requires a value_set_uri; "
                "got None - this is a malformed PropertyDefinitionSpec, not "
                "a bad value"
            )
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

    def _validate_local_code_binding(self, code: str, system_key: str) -> Sequence[ValidationIssue]:
        """FR-10: local codes are validated against the platform's own `LocalCode` table, with
        no `self._terminology` call, because Ontoserver does not hold them. `resolve()` has
        three outcomes and each gets its own issue code, so a field-level message can say what
        happened."""
        assert self._local_code_lookup is not None  # narrowed by the caller
        resolved = self._local_code_lookup.resolve(system_key, code)
        if resolved is None:
            return [
                ValidationIssue(
                    code="not-a-local-code",
                    message=f"{code!r} is not a code in the {system_key!r} local code system",
                )
            ]
        if resolved.system_status == "deprecated":
            return [
                ValidationIssue(
                    code="local-code-system-deprecated",
                    message=f"the {system_key!r} local code system has been deprecated",
                )
            ]
        if resolved.status == "deprecated":
            return [
                ValidationIssue(
                    code="local-code-deprecated",
                    message=f"{code!r} has been deprecated in the {system_key!r} local code system",
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
                # Computed here so the frontend never branches on `strength` (ADR-0013 SS3).
                "allowJustification": strength == "extensible",
            },
        )

    def serialise(self, value: Any, target: SerialisationTarget) -> Any:
        """Three different shapes, one per representation. `dict(value)` for all three would put
        a Python dict in a `PLAIN_TEXT` cell (CSV, SPIA xlsx).

        No handler may strip a semantic tag (FR-83); this one never touches `display` or FSN
        text, so there is nothing to strip.
        """
        if target is SerialisationTarget.PLAIN_TEXT:
            return value["code"]
        if target is SerialisationTarget.JSON:
            return dict(value)
        if target is SerialisationTarget.FHIR_VALUE:
            coding: dict[str, Any] = {"system": value["system"], "code": value["code"]}
            if "display" in value:
                coding["display"] = value["display"]
            return coding
        raise AssertionError(f"unhandled SerialisationTarget: {target}")

    def index_shape(self, spec: PropertyDefinitionSpec) -> IndexShape | None:
        if not spec.filterable:
            return None
        return IndexShape(expression=ValueExpression.RAW_JSONB, requires_conformance_sweep=False)

    def supported_filter_ops(self) -> frozenset[FilterOp]:
        return _SUPPORTED_OPS

    def filter_clause(
        self, op: FilterOp, value: Any, column: ColumnElement[Any]
    ) -> ColumnElement[bool]:
        """`@>` containment, not `->>'code' = ...` (FR-13). `index_shape()` declares this
        property's index as a `jsonb_path_ops` GIN, which serves only `@>`, `@?` and `@@`, so a
        `->>` equality predicate cannot use it. `FilterOp.IN` is an `OR` of per-code
        containments, not `@> ANY(array)`: the array form is not indexable under
        `jsonb_path_ops`, and the `OR` form is. `nptc.db.property_indexes.create_statement`
        builds the index on the same assumption."""
        if op is FilterOp.EQUALS:
            return column.contains({"code": value})
        if op is FilterOp.IN:
            # `false()` first: SQLAlchemy 2.0's `or_()` with no arguments (an empty `value`)
            # renders nothing rather than an always-false predicate, which drops the clause and
            # matches every row. `in_([])` never had that failure mode. `false()` restores
            # always-false on empty input and changes nothing for a non-empty list.
            return or_(false(), *(column.contains({"code": candidate}) for candidate in value))
        raise UnsupportedFilterOpError(f"code handler does not support {op}")

    def facet_expression(self, column: ColumnElement[Any]) -> ColumnElement[Any] | None:
        # Groups by `code` alone, not `(system, code)` (ADR-0032).
        return type_cast("ColumnElement[Any]", column["code"].astext)
