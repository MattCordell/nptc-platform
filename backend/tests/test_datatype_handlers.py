"""Per-handler tests for the five builtin datatype handlers (FR-77,
ADR-0013). One section per handler, each covering its principal failure
mode, not just the happy path (CLAUDE.md's testing convention).

No fixtures - handlers are constructed directly, and the `code` handler is
given a `StubTerminologyClient` (NFR-37: no network access).
"""

from __future__ import annotations

import json

import jsonschema
import pytest
from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table
from sqlalchemy.dialects import postgresql

from nptc.registry import (
    BindingSpec,
    ControlKind,
    FilterOp,
    PropertyDefinitionSpec,
    SerialisationTarget,
    UnsupportedBindingError,
    UnsupportedFilterOpError,
)
from nptc.registry.datatypes.code import CodeHandler
from nptc.registry.datatypes.decimal import DecimalHandler
from nptc.registry.datatypes.positive_int import PositiveIntHandler
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.datatypes.url import UrlHandler
from nptc_shared.terminology.models import Edition, ValidationResult
from nptc_shared.terminology.stub import StubTerminologyClient


def _spec(
    datatype: str,
    *,
    binding: BindingSpec | None = None,
    constraints: dict[str, object] | None = None,
    filterable: bool = True,
    **overrides: object,
) -> PropertyDefinitionSpec:
    defaults: dict[str, object] = {
        "key": "test_property",
        "label": "Test property",
        "datatype": datatype,
        "cardinality": "0..1",
        "scope": frozenset({"maintenance"}),
        "required_for_submission": False,
        "required_for_publication": False,
        "binding": binding,
        "filterable": filterable,
        "constraints": constraints or {},
    }
    defaults.update(overrides)
    return PropertyDefinitionSpec(**defaults)  # type: ignore[arg-type]


def _compiled(clause: object) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[attr-defined]


# --- string -----------------------------------------------------------


@pytest.mark.req("FR-77")
def test_string_reports_max_length_breach_as_validation_issue_not_exception() -> None:
    handler = StringHandler()
    spec = _spec("string", constraints={"maxLength": 5})

    issues = handler.validate("way too long", spec)

    assert len(issues) == 1
    assert issues[0].code == "max-length-exceeded"


@pytest.mark.req("FR-77")
def test_string_wrong_type_is_reported_not_raised() -> None:
    handler = StringHandler()
    assert handler.validate(123, _spec("string"))[0].code == "wrong-type"


@pytest.mark.req("FR-77")
def test_string_schema_fragment_is_a_well_formed_schema() -> None:
    fragment = StringHandler().json_schema_fragment(_spec("string", constraints={"maxLength": 10}))
    jsonschema.Draft202012Validator.check_schema(dict(fragment))


@pytest.mark.req("FR-77")
def test_string_form_control_params_are_json_serialisable() -> None:
    descriptor = StringHandler().form_control(_spec("string"))
    assert descriptor.control is ControlKind.TEXT
    json.dumps(descriptor.params)


@pytest.mark.req("FR-77")
def test_string_facet_clause_compiles_to_sql_with_no_database() -> None:
    table = Table("t", MetaData(), Column("value", String))
    clause = StringHandler().filter_clause(FilterOp.PREFIX, "abc", table.c.value)
    assert "LIKE" in _compiled(clause).upper()


@pytest.mark.req("FR-77")
def test_string_unsupported_filter_op_raises() -> None:
    table = Table("t", MetaData(), Column("value", String))
    with pytest.raises(UnsupportedFilterOpError):
        StringHandler().filter_clause(FilterOp.RANGE, ("a", "z"), table.c.value)


# --- url ----------------------------------------------------------------


@pytest.mark.req("FR-77")
def test_url_rejects_scheme_outside_constraints() -> None:
    handler = UrlHandler()
    spec = _spec("url", constraints={"schemes": ["https"]})

    issues = handler.validate("http://example.org/vs", spec)

    assert issues[0].code == "scheme-not-allowed"


@pytest.mark.req("FR-77")
def test_url_rejects_a_non_absolute_value() -> None:
    handler = UrlHandler()
    assert handler.validate("not-a-url", _spec("url"))[0].code == "not-a-url"


@pytest.mark.req("FR-77")
def test_url_defaults_to_https_only() -> None:
    handler = UrlHandler()
    assert handler.validate("https://example.org/vs", _spec("url")) == []


@pytest.mark.req("FR-77")
def test_url_schema_fragment_is_a_well_formed_schema() -> None:
    fragment = UrlHandler().json_schema_fragment(_spec("url"))
    jsonschema.Draft202012Validator.check_schema(dict(fragment))


@pytest.mark.req("FR-77")
def test_url_form_control_carries_allowed_schemes() -> None:
    descriptor = UrlHandler().form_control(_spec("url", constraints={"schemes": ["https", "http"]}))
    assert descriptor.control is ControlKind.URI
    assert descriptor.params["schemes"] == ["https", "http"]
    json.dumps(descriptor.params)


# --- decimal --------------------------------------------------------------


@pytest.mark.req("FR-77")
def test_decimal_rejects_non_numeric_value() -> None:
    handler = DecimalHandler()
    assert handler.validate("not a number", _spec("decimal"))[0].code == "wrong-type"


@pytest.mark.req("FR-77")
def test_decimal_rejects_bool_despite_bool_being_an_int_subclass() -> None:
    """`isinstance(True, int)` is `True` in Python - a naive numeric check
    would silently accept a boolean as a decimal value."""
    handler = DecimalHandler()
    assert handler.validate(True, _spec("decimal"))[0].code == "wrong-type"


@pytest.mark.req("FR-77")
def test_decimal_facet_expression_is_none_faceting_is_meaningless() -> None:
    """ADR-0013's table: `decimal` has no facet - a continuous value has no
    meaningful group-by. This is the FR-16 case the guard must never mask
    with a silent `else` elsewhere."""
    table = Table("t", MetaData(), Column("value", Numeric))
    assert DecimalHandler().facet_expression(table.c.value) is None


@pytest.mark.req("FR-77")
def test_decimal_range_filter_compiles_with_no_database() -> None:
    table = Table("t", MetaData(), Column("value", Numeric))
    clause = DecimalHandler().filter_clause(FilterOp.RANGE, (1, 10), table.c.value)
    compiled = _compiled(clause).upper()
    assert ">=" in compiled and "<=" in compiled


@pytest.mark.req("FR-77")
def test_decimal_schema_fragment_is_a_well_formed_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(
        dict(DecimalHandler().json_schema_fragment(_spec("decimal")))
    )


# --- positiveInt ------------------------------------------------------


@pytest.mark.req("FR-77")
def test_positive_int_rejects_a_non_integer_value() -> None:
    """`positiveInt` is a separate handler from `decimal` precisely so that
    `1.5` is unrepresentable, not merely refused - the schema fragment makes
    it so, and validate() backs that up structurally too."""
    handler = PositiveIntHandler()
    assert handler.validate(1.5, _spec("positiveInt"))[0].code == "wrong-type"


@pytest.mark.req("FR-77")
def test_positive_int_rejects_zero() -> None:
    handler = PositiveIntHandler()
    assert handler.validate(0, _spec("positiveInt"))[0].code == "not-positive"


@pytest.mark.req("FR-77")
def test_positive_int_schema_fragment_makes_non_integers_unrepresentable() -> None:
    fragment = dict(PositiveIntHandler().json_schema_fragment(_spec("positiveInt")))
    jsonschema.Draft202012Validator.check_schema(fragment)
    validator = jsonschema.Draft202012Validator(fragment)
    assert not validator.is_valid(1.5)
    assert not validator.is_valid(0)
    assert validator.is_valid(1)


@pytest.mark.req("FR-77")
def test_positive_int_in_filter_compiles_with_no_database() -> None:
    table = Table("t", MetaData(), Column("value", Integer))
    clause = PositiveIntHandler().filter_clause(FilterOp.IN, (1, 2, 3), table.c.value)
    assert "IN" in _compiled(clause).upper()


# --- code -----------------------------------------------------------------


@pytest.mark.req("FR-77")
@pytest.mark.req("FR-06")
def test_code_rejects_malformed_sctid_format() -> None:
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    value = {"system": "http://snomed.info/sct", "code": "not-digits"}

    issues = handler.validate(value, _spec("code"))

    assert issues[0].code == "invalid-sctid-format"


@pytest.mark.req("FR-77")
@pytest.mark.req("FR-06")
def test_code_rejects_sctid_failing_verhoeff_check_digit() -> None:
    """Well-formed digits, wrong check digit - the format check alone would
    pass this."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    value = {"system": "http://snomed.info/sct", "code": "123456"}

    issues = handler.validate(value, _spec("code"))

    assert issues[0].code == "invalid-sctid-check-digit"


@pytest.mark.req("FR-77")
def test_code_rejects_missing_required_field() -> None:
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    assert handler.validate({"system": "http://snomed.info/sct"}, _spec("code"))[0].code == (
        "missing-field"
    )


@pytest.mark.req("FR-10")
def test_code_binding_check_surfaces_a_not_in_value_set_result() -> None:
    """FR-10's live binding check, through a StubTerminologyClient - no
    network access (NFR-37)."""
    stub = StubTerminologyClient()
    edition = Edition(module_id="900000000000207008", label="900000000000207008")
    stub.seed_validate_code(
        "138875005",
        ValidationResult(code="138875005", result=False, message="not in value set"),
        value_set_url="http://example.org/vs",
        edition=edition,
    )
    handler = CodeHandler(terminology_client=stub)
    binding = BindingSpec(
        binding_target="value_set",
        value_set_uri="http://example.org/vs",
        strength="required",
        edition="900000000000207008",
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://snomed.info/sct", "code": "138875005"}

    # 138875005 is a valid SCTID (SNOMED CT root concept), so only the
    # binding check should fire.
    issues = handler.validate(value, spec)

    assert [issue.code for issue in issues] == ["not-in-value-set"]


@pytest.mark.req("FR-10")
def test_code_local_code_system_without_lookup_raises_loudly() -> None:
    """ADR-0013 open question 1: a loud refusal, never a silent pass, until
    #56 supplies a real LocalCodeLookup."""
    handler = CodeHandler(terminology_client=StubTerminologyClient(), local_code_lookup=None)
    binding = BindingSpec(
        binding_target="local_code_system",
        value_set_uri=None,
        strength="required",
        edition="local",
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://example.org/local", "code": "DISC-1"}

    with pytest.raises(UnsupportedBindingError):
        handler.validate(value, spec)


@pytest.mark.req("FR-77")
def test_code_form_control_computes_allow_justification_from_strength() -> None:
    """The frontend never branches on `strength` (ADR-0013 SS3) - the
    handler computes `allowJustification` for it."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    binding = BindingSpec(
        binding_target="value_set",
        value_set_uri="http://example.org/vs",
        strength="extensible",
        edition="900000000000207008",
    )
    descriptor = handler.form_control(_spec("code", binding=binding))

    assert descriptor.control is ControlKind.CONCEPT_PICKER
    assert descriptor.params["allowJustification"] is True
    json.dumps(descriptor.params)


@pytest.mark.req("FR-77")
def test_code_schema_fragment_is_a_well_formed_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(
        dict(
            CodeHandler(terminology_client=StubTerminologyClient()).json_schema_fragment(
                _spec("code")
            )
        )
    )


@pytest.mark.req("FR-83")
def test_code_serialise_never_touches_display_text() -> None:
    """FR-83's guarantee is a claim about the number of call sites (exactly
    one, in the export renderer, over `code_binding.fsn`) - this handler
    must not be a second one."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    value = {
        "system": "http://snomed.info/sct",
        "code": "138875005",
        "display": "SNOMED CT Concept (procedure)",
    }

    serialised = handler.serialise(value, SerialisationTarget.JSON)

    assert serialised["display"] == "SNOMED CT Concept (procedure)"
