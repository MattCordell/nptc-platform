"""Pure FHIR-JSON parsers for the FR-53 terminology client.

No I/O. Turns the ``dict[str, Any]`` a JSON body decodes to into the frozen
value types in ``models.py``. This is deliberately the *only* place that
interprets a raw response body: both ``ontoserver.py`` (real bytes off the
wire) and the ``shared/tests`` contract suite's stub-seeding path (replaying
a captured fixture) go through these same functions, so the stub is a
transport-less replay of one interpretation of the wire format, never a
second, independently drifting one.

There is deliberately no helper that turns a code into an ``int``. The FR-06
chokepoint lives in ``as_str``, which refuses a JSON number outright: a
SNOMED CT identifier that arrives typed as a number has already lost
precision or leading-zero semantics before it reached this module, and
coercing it with ``str()`` would hide exactly the defect this platform
exists to eliminate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from nptc_shared.terminology.errors import (
    OperationOutcomeIssue,
    TerminologyOutcomeError,
    TerminologyProtocolError,
)
from nptc_shared.terminology.models import (
    ConceptProperty,
    Designation,
    ExpandedConcept,
    Expansion,
    LookupResult,
    Operation,
    SubsumptionOutcome,
    ValidationResult,
)

_EXPECTED_RESOURCE_TYPE: dict[Operation, str] = {
    Operation.EXPAND: "ValueSet",
    Operation.LOOKUP: "Parameters",
    Operation.SUBSUMES: "Parameters",
    Operation.CODE_SYSTEM_VALIDATE_CODE: "Parameters",
    Operation.VALUE_SET_VALIDATE_CODE: "Parameters",
}


def as_mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TerminologyProtocolError(f"{context}: expected an object, got {type(value).__name__}")
    return cast(Mapping[str, object], value)


def as_sequence(value: object, *, context: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TerminologyProtocolError(f"{context}: expected an array, got {type(value).__name__}")
    return cast(Sequence[object], value)


def as_str(value: object, *, context: str) -> str:
    """Refuses a JSON number: see the module docstring's FR-06 note."""
    if not isinstance(value, str):
        raise TerminologyProtocolError(
            f"{context}: expected a string, got {type(value).__name__} ({value!r})"
        )
    return value


def as_bool(value: object, *, context: str) -> bool:
    if not isinstance(value, bool):
        raise TerminologyProtocolError(f"{context}: expected a boolean, got {type(value).__name__}")
    return value


def as_int(value: object, *, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TerminologyProtocolError(
            f"{context}: expected an integer, got {type(value).__name__}"
        )
    return value


def parse_response_body(body: object, *, operation: Operation) -> Mapping[str, object]:
    """Validates ``body`` is a mapping with the resource type ``operation``
    expects, raising before any operation-specific parser has to run.

    A body typed as ``OperationOutcome`` always raises ``TerminologyOutcomeError``
    here, regardless of ``operation`` - a 2xx OperationOutcome parsed leniently
    as the expected resource is FR-54's hazard: it would read as an empty,
    "nothing matched" result instead of the refusal it actually is.
    """
    mapping = as_mapping(body, context=f"{operation.value} response")
    resource_type = mapping.get("resourceType")
    if resource_type == "OperationOutcome":
        raise TerminologyOutcomeError(
            f"{operation.value} returned an OperationOutcome",
            operation=operation,
            issues=parse_operation_outcome(mapping),
        )
    expected = _EXPECTED_RESOURCE_TYPE[operation]
    if resource_type != expected:
        raise TerminologyProtocolError(
            f"{operation.value}: expected resourceType {expected!r}, got {resource_type!r}",
            operation=operation,
        )
    return mapping


def parse_operation_outcome(body: Mapping[str, object]) -> tuple[OperationOutcomeIssue, ...]:
    issues = []
    for raw_issue in as_sequence(body.get("issue", []), context="OperationOutcome.issue"):
        issue = as_mapping(raw_issue, context="OperationOutcome.issue[]")
        diagnostics = issue.get("diagnostics")
        expression = tuple(
            as_str(item, context="OperationOutcome.issue[].expression[]")
            for item in as_sequence(
                issue.get("expression", []), context="OperationOutcome.issue[].expression"
            )
        )
        issues.append(
            OperationOutcomeIssue(
                severity=as_str(issue.get("severity"), context="OperationOutcome.issue[].severity"),
                code=as_str(issue.get("code"), context="OperationOutcome.issue[].code"),
                diagnostics=as_str(diagnostics, context="OperationOutcome.issue[].diagnostics")
                if diagnostics is not None
                else None,
                expression=expression,
            )
        )
    return tuple(issues)


def _parse_expansion_designation(raw: Mapping[str, object]) -> Designation:
    """Parses one entry of ``expansion.contains[].designation`` - a flat
    object, unlike ``$lookup``'s ``Parameters``-part-based designations
    (see ``_parse_lookup_designation``)."""
    use = raw.get("use")
    use_mapping = as_mapping(use, context="designation.use") if use is not None else None
    return Designation(
        value=as_str(raw.get("value"), context="designation.value"),
        language=as_str(raw["language"], context="designation.language")
        if "language" in raw
        else None,
        use_system=as_str(use_mapping["system"], context="designation.use.system")
        if use_mapping and "system" in use_mapping
        else None,
        use_code=as_str(use_mapping["code"], context="designation.use.code")
        if use_mapping and "code" in use_mapping
        else None,
        use_display=as_str(use_mapping["display"], context="designation.use.display")
        if use_mapping and "display" in use_mapping
        else None,
    )


def parse_expansion(body: Mapping[str, object]) -> Expansion:
    """Parses a ``ValueSet`` with an ``expansion`` (``ValueSet/$expand``).

    ``expansion`` is required - a ``ValueSet`` returned with no ``expansion``
    element is a *definition*, not a result, and defaulting it to ``{}``
    would parse as a clean, empty ``Expansion``. That is exactly FR-54's
    hazard: a response the server never actually expanded would read as
    "nothing matched" instead of the protocol violation it is.
    """
    if "expansion" not in body:
        raise TerminologyProtocolError(
            "ValueSet response has no 'expansion' element - it was not expanded"
        )
    expansion = as_mapping(body["expansion"], context="ValueSet.expansion")
    total = expansion.get("total")
    offset = expansion.get("offset")

    resolved_versions: list[str] = []
    for raw_param in as_sequence(
        expansion.get("parameter", []), context="ValueSet.expansion.parameter"
    ):
        param = as_mapping(raw_param, context="ValueSet.expansion.parameter[]")
        name = param.get("name")
        if name not in ("version", "used-codesystem"):
            continue
        uri = param.get("valueUri") or param.get("valueString")
        if uri is None:
            continue
        text = as_str(uri, context=f"ValueSet.expansion.parameter[{name}]")
        # "used-codesystem" may carry "system|version"; the version half is
        # what FR-48 wants recorded.
        version = text.rsplit("|", maxsplit=1)[-1] if "|" in text else text
        if version not in resolved_versions:
            resolved_versions.append(version)

    concepts = []
    for raw_concept in as_sequence(
        expansion.get("contains", []), context="ValueSet.expansion.contains"
    ):
        concept = as_mapping(raw_concept, context="ValueSet.expansion.contains[]")
        designations = tuple(
            _parse_expansion_designation(
                as_mapping(item, context="expansion.contains[].designation[]")
            )
            for item in as_sequence(
                concept.get("designation", []), context="expansion.contains[].designation"
            )
        )
        display = concept.get("display")
        version_value = concept.get("version")
        concepts.append(
            ExpandedConcept(
                code=as_str(concept.get("code"), context="expansion.contains[].code"),
                system=as_str(concept.get("system"), context="expansion.contains[].system"),
                display=as_str(display, context="expansion.contains[].display")
                if display is not None
                else None,
                version=as_str(version_value, context="expansion.contains[].version")
                if version_value is not None
                else None,
                designations=designations,
            )
        )

    return Expansion(
        concepts=tuple(concepts),
        total=as_int(total, context="ValueSet.expansion.total") if total is not None else None,
        offset=as_int(offset, context="ValueSet.expansion.offset") if offset is not None else None,
        resolved_versions=tuple(resolved_versions),
    )


_VALUE_PREFIX = "value"


def _value_and_type(part: Mapping[str, object], *, context: str) -> tuple[str, str]:
    """Finds the ``value[x]`` entry in a ``Parameters.parameter`` part and
    returns its lexical string form plus the FHIR type suffix, e.g.
    ``("true", "boolean")`` for ``valueBoolean: true``.

    A ``Coding`` value (``valueCoding``) resolves to its own ``code`` - every
    caller here wants the code, never the whole Coding object.
    """
    for key, value in part.items():
        if key == _VALUE_PREFIX or not key.startswith(_VALUE_PREFIX):
            continue
        suffix = key[len(_VALUE_PREFIX) :]
        type_name = suffix[0].lower() + suffix[1:]
        if isinstance(value, Mapping):
            coding = as_mapping(value, context=f"{context}.{key}")
            return as_str(coding.get("code"), context=f"{context}.{key}.code"), type_name
        if isinstance(value, bool):
            return ("true" if value else "false"), type_name
        if isinstance(value, str):
            return value, type_name
        # No int/float branch: a value[x] that arrives as a bare JSON number
        # (e.g. a SAME_AS historical-association target sent as valueDecimal
        # instead of valueCode) is exactly the FR-06 defect class as_str()
        # refuses elsewhere - str()-coercing it here would silently let a
        # numeric SNOMED CT identifier back in through a side door.
        raise TerminologyProtocolError(
            f"{context}.{key}: unsupported value type {type(value).__name__}"
        )
    raise TerminologyProtocolError(f"{context}: no value[x] entry found")


def _find_parameters(body: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    matches = []
    for raw_param in as_sequence(body.get("parameter", []), context="Parameters.parameter"):
        param = as_mapping(raw_param, context="Parameters.parameter[]")
        if param.get("name") == name:
            matches.append(param)
    return matches


def _parameter_parts(parameter: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [
        as_mapping(raw_part, context="Parameters.parameter[].part[]")
        for raw_part in as_sequence(
            parameter.get("part", []), context="Parameters.parameter[].part"
        )
    ]


def _first_value(body: Mapping[str, object], name: str) -> str | None:
    matches = _find_parameters(body, name)
    if not matches:
        return None
    lexical, _ = _value_and_type(matches[0], context=f"Parameters.parameter[{name}]")
    return lexical


def _parse_lookup_designation(parameter: Mapping[str, object]) -> Designation:
    """Parses one ``Parameters.parameter`` entry named ``designation`` from a
    ``$lookup`` response - a ``part``-based structure, unlike ``$expand``'s
    flat designation objects (see ``_parse_expansion_designation``)."""
    language: str | None = None
    use_system: str | None = None
    use_code: str | None = None
    use_display: str | None = None
    value: str | None = None
    for part in _parameter_parts(parameter):
        part_name = part.get("name")
        if part_name == "language":
            language, _ = _value_and_type(part, context="designation.language")
        elif part_name == "value":
            value, _ = _value_and_type(part, context="designation.value")
        elif part_name == "use":
            coding = as_mapping(part.get("valueCoding"), context="designation.use.valueCoding")
            use_system = (
                as_str(coding["system"], context="designation.use.system")
                if "system" in coding
                else None
            )
            use_code = (
                as_str(coding["code"], context="designation.use.code") if "code" in coding else None
            )
            use_display = (
                as_str(coding["display"], context="designation.use.display")
                if "display" in coding
                else None
            )
    if value is None:
        raise TerminologyProtocolError("$lookup designation part missing a 'value' entry")
    return Designation(
        value=value,
        language=language,
        use_system=use_system,
        use_code=use_code,
        use_display=use_display,
    )


def _parse_properties(body: Mapping[str, object]) -> tuple[ConceptProperty, ...]:
    properties = []
    for parameter in _find_parameters(body, "property"):
        code: str | None = None
        value: str | None = None
        value_type: str | None = None
        for part in _parameter_parts(parameter):
            part_name = part.get("name")
            if part_name == "code":
                code, _ = _value_and_type(part, context="property.code")
            elif part_name == "value":
                value, value_type = _value_and_type(part, context="property.value")
            # "description" and "subproperty" parts are ignored - no caller
            # of this client needs them yet.
        if code is None or value is None or value_type is None:
            continue
        properties.append(ConceptProperty(code=code, value=value, value_type=value_type))
    return tuple(properties)


def parse_lookup(body: Mapping[str, object], *, code: str, system: str) -> LookupResult:
    """Parses a ``Parameters`` response from ``CodeSystem/$lookup``.

    ``code``/``system`` are the request's own code/system - ``$lookup``'s
    output parameters do not echo them, so the caller who made the request is
    the only place that knows them.
    """
    designations = tuple(
        _parse_lookup_designation(parameter) for parameter in _find_parameters(body, "designation")
    )
    return LookupResult(
        code=code,
        system=system,
        name=_first_value(body, "name"),
        display=_first_value(body, "display"),
        resolved_version=_first_value(body, "version"),
        designations=designations,
        properties=_parse_properties(body),
    )


def parse_subsumes(body: Mapping[str, object]) -> SubsumptionOutcome:
    """Parses a ``Parameters`` response from ``CodeSystem/$subsumes``."""
    outcome = _first_value(body, "outcome")
    if outcome is None:
        raise TerminologyProtocolError("$subsumes response missing an 'outcome' parameter")
    try:
        return SubsumptionOutcome(outcome)
    except ValueError as exc:
        raise TerminologyProtocolError(
            f"$subsumes returned unrecognised outcome {outcome!r}"
        ) from exc


def parse_validate_code(body: Mapping[str, object], *, code: str) -> ValidationResult:
    """Parses a ``Parameters`` response from either ``$validate-code`` form."""
    result_value = _first_value(body, "result")
    if result_value is None:
        raise TerminologyProtocolError("$validate-code response missing a 'result' parameter")
    return ValidationResult(
        code=code,
        result=result_value == "true",
        display=_first_value(body, "display"),
        message=_first_value(body, "message"),
        resolved_version=_first_value(body, "version"),
    )
