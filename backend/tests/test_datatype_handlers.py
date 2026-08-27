"""Per-handler tests for the five builtin datatype handlers (FR-77,
ADR-0013). One section per handler, each covering its principal failure
mode, not just the happy path (CLAUDE.md's testing convention).

No fixtures - handlers are constructed directly, and the `code` handler is
given a `StubTerminologyClient` (NFR-37: no network access).
"""

from __future__ import annotations

import json
from decimal import Decimal

import jsonschema
import pytest
from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from nptc.db.property_indexes import DesiredIndex, create_statement
from nptc.registry import (
    BindingSpec,
    ControlKind,
    FilterOp,
    IndexKind,
    PropertyDefinitionSpec,
    ResolvedLocalCode,
    SerialisationTarget,
    UnsupportedBindingError,
    UnsupportedFilterOpError,
    ValueExpression,
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


def _compiled_with_params(clause: object) -> tuple[str, dict[str, object]]:
    """`literal_binds=True` has no literal renderer for a JSONB bind value
    (SQLAlchemy core has no "render this dict as a JSONB literal" support)
    - `CodeHandler.filter_clause`'s `{"code": ...}` containment argument
    needs this instead: the operator structure from the compiled SQL, the
    actual value from the bound parameters."""
    compiled = clause.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    return str(compiled), dict(compiled.params)


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
def test_string_prefix_filter_escapes_like_wildcards() -> None:
    """Without autoescape, a caller-supplied `%`/`_` is a LIKE wildcard, not
    a literal character - "a_c" would otherwise match "abc"."""
    table = Table("t", MetaData(), Column("value", String))
    clause = StringHandler().filter_clause(FilterOp.PREFIX, "a_c", table.c.value)
    assert "ESCAPE" in _compiled(clause).upper()


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
def test_url_scheme_constraint_is_case_insensitive() -> None:
    """`urlsplit` lowercases the parsed scheme - a constraint spelled
    "HTTPS" must still match, not silently reject every value."""
    handler = UrlHandler()
    spec = _spec("url", constraints={"schemes": ["HTTPS"]})

    assert handler.validate("https://example.org/vs", spec) == []


@pytest.mark.req("FR-77")
def test_url_prefix_filter_escapes_like_wildcards() -> None:
    table = Table("t", MetaData(), Column("value", String))
    clause = UrlHandler().filter_clause(FilterOp.PREFIX, "https://a_c", table.c.value)
    assert "ESCAPE" in _compiled(clause).upper()


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
def test_decimal_accepts_a_decimal_decimal() -> None:
    """`decimal.Decimal` registers as `numbers.Number`, not `numbers.Real` -
    a value arriving from a JSONB/Numeric round-trip is commonly a Decimal,
    not a float, and must not be rejected as `wrong-type`."""
    handler = DecimalHandler()
    assert handler.validate(Decimal("1.50"), _spec("decimal")) == []


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


@pytest.mark.req("FR-89")
def test_code_rejects_a_forbidden_code() -> None:
    """FR-89: 'Any' is never a specimen code - the exact seam
    Specimen's own `constraints = {"forbidden_codes": ["Any"]}` uses
    (`nptc.db.bootstrap`)."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    spec = _spec("code", constraints={"forbidden_codes": ["Any"]})
    value = {"system": "http://example.org/local", "code": "Any"}

    issues = handler.validate(value, spec)

    assert [issue.code for issue in issues] == ["forbidden-code"]


@pytest.mark.req("FR-89")
def test_code_rejects_a_forbidden_code_case_insensitively() -> None:
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    spec = _spec("code", constraints={"forbidden_codes": ["Any"]})
    value = {"system": "http://example.org/local", "code": "any"}

    issues = handler.validate(value, spec)

    assert [issue.code for issue in issues] == ["forbidden-code"]


@pytest.mark.req("FR-89")
def test_code_accepts_a_code_not_on_the_forbidden_list() -> None:
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    spec = _spec("code", constraints={"forbidden_codes": ["Any"]})
    # A real SCTID (see shared/tests/test_sctid.py's own known-good
    # fixture), with no binding configured, so nothing else can reject it.
    value = {"system": "http://snomed.info/sct", "code": "873871000168106"}

    issues = handler.validate(value, spec)

    assert issues == []


@pytest.mark.req("FR-89")
def test_code_ignores_a_malformed_non_list_forbidden_codes_rather_than_failing_open() -> None:
    """A `forbidden_codes` stored as a bare string is iterable-of-characters
    - without the `isinstance(forbidden_codes, list)` guard, `"Any"` would
    silently forbid the single-letter codes `a`, `n`, `y` while never
    catching the intended whole-code entries, including `"Any"` itself.
    `nptc.registry.schema.validate_constraints` is the layer meant to
    reject this shape before it reaches here; this is the defence in depth
    for the case where it doesn't."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    spec = _spec("code", constraints={"forbidden_codes": "Any"})

    issues_for_any = handler.validate({"system": "http://example.org/local", "code": "Any"}, spec)
    issues_for_a = handler.validate({"system": "http://example.org/local", "code": "a"}, spec)

    assert issues_for_any == []
    assert issues_for_a == []


@pytest.mark.req("FR-77")
@pytest.mark.req("FR-06")
def test_code_rejects_a_numeric_code_as_a_validation_issue_not_a_crash() -> None:
    """`json_schema_fragment` declares `code` as `"type": "string"` - a
    numeric code must fail here, not reach `has_valid_format` and raise
    `TypeError`, and never flow through to storage as a number (FR-06's
    defect class)."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    value = {"system": "http://example.org/local", "code": 138875005}

    issues = handler.validate(value, _spec("code"))

    assert [issue.code for issue in issues] == ["wrong-type"]


@pytest.mark.req("FR-77")
def test_code_rejects_a_numeric_system() -> None:
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    value = {"system": 12345, "code": "138875005"}

    issues = handler.validate(value, _spec("code"))

    assert [issue.code for issue in issues] == ["wrong-type"]


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


class _StubLocalCodeLookup:
    """A minimal `LocalCodeLookup`, no database - keyed on
    `(system_key, code)` so a test seeds only the rows it needs."""

    def __init__(self, table: dict[tuple[str, str], ResolvedLocalCode]) -> None:
        self._table = table
        self.calls: list[tuple[str, str]] = []

    def resolve(self, system_key: str, code: str) -> ResolvedLocalCode | None:
        self.calls.append((system_key, code))
        return self._table.get((system_key, code))


@pytest.mark.req("FR-10")
def test_code_local_code_system_binding_without_a_key_raises_loudly() -> None:
    """A `BindingSpec(binding_target="local_code_system",
    local_code_system_key=None)` is a malformed property definition, not a
    bad value."""
    handler = CodeHandler(
        terminology_client=StubTerminologyClient(),
        local_code_lookup=_StubLocalCodeLookup({}),
    )
    binding = BindingSpec(
        binding_target="local_code_system",
        value_set_uri=None,
        strength="required",
        edition="local",
        local_code_system_key=None,
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://example.org/local", "code": "DISC-1"}

    with pytest.raises(UnsupportedBindingError):
        handler.validate(value, spec)


@pytest.mark.req("FR-10")
def test_code_local_code_system_binding_resolves_against_the_lookup_not_the_terminology_server() -> (
    None
):
    """FR-10/#56: validated internally against LocalCode, never Ontoserver."""
    terminology = StubTerminologyClient()
    lookup = _StubLocalCodeLookup(
        {
            ("discipline", "DISC-1"): ResolvedLocalCode(
                code="DISC-1",
                display="Chemical pathology",
                status="active",
                system_status="active",
                provisional=False,
            )
        }
    )
    handler = CodeHandler(terminology_client=terminology, local_code_lookup=lookup)
    binding = BindingSpec(
        binding_target="local_code_system",
        value_set_uri=None,
        strength="required",
        edition="local",
        local_code_system_key="discipline",
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://example.org/local", "code": "DISC-1"}

    issues = handler.validate(value, spec)

    assert issues == []
    assert lookup.calls == [("discipline", "DISC-1")]


@pytest.mark.req("FR-10")
def test_code_local_code_system_binding_rejects_a_code_absent_from_the_system() -> None:
    handler = CodeHandler(
        terminology_client=StubTerminologyClient(),
        local_code_lookup=_StubLocalCodeLookup({}),
    )
    binding = BindingSpec(
        binding_target="local_code_system",
        value_set_uri=None,
        strength="required",
        edition="local",
        local_code_system_key="discipline",
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://example.org/local", "code": "NOT-A-CODE"}

    issues = handler.validate(value, spec)

    assert [issue.code for issue in issues] == ["not-a-local-code"]


@pytest.mark.req("FR-10")
def test_code_local_code_system_binding_rejects_a_deprecated_code() -> None:
    lookup = _StubLocalCodeLookup(
        {
            ("discipline", "DISC-1"): ResolvedLocalCode(
                code="DISC-1",
                display="Retired",
                status="deprecated",
                system_status="active",
                provisional=False,
            )
        }
    )
    handler = CodeHandler(terminology_client=StubTerminologyClient(), local_code_lookup=lookup)
    binding = BindingSpec(
        binding_target="local_code_system",
        value_set_uri=None,
        strength="required",
        edition="local",
        local_code_system_key="discipline",
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://example.org/local", "code": "DISC-1"}

    issues = handler.validate(value, spec)

    assert [issue.code for issue in issues] == ["local-code-deprecated"]


@pytest.mark.req("FR-10")
def test_code_local_code_system_binding_rejects_a_code_in_a_deprecated_system() -> None:
    """A code can be fine while its owning system has been retired wholesale
    - `system_status` is checked independently of the code's own `status`."""
    lookup = _StubLocalCodeLookup(
        {
            ("discipline", "DISC-1"): ResolvedLocalCode(
                code="DISC-1",
                display="Chemical pathology",
                status="active",
                system_status="deprecated",
                provisional=False,
            )
        }
    )
    handler = CodeHandler(terminology_client=StubTerminologyClient(), local_code_lookup=lookup)
    binding = BindingSpec(
        binding_target="local_code_system",
        value_set_uri=None,
        strength="required",
        edition="local",
        local_code_system_key="discipline",
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://example.org/local", "code": "DISC-1"}

    issues = handler.validate(value, spec)

    assert [issue.code for issue in issues] == ["local-code-system-deprecated"]


@pytest.mark.req("FR-10")
def test_code_value_set_binding_without_a_uri_raises_loudly() -> None:
    """A `BindingSpec(binding_target="value_set", value_set_uri=None)` is a
    malformed property definition, not a bad value - it must not silently
    call the terminology server with `value_set_url=None`."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    binding = BindingSpec(
        binding_target="value_set", value_set_uri=None, strength="required", edition="edition"
    )
    spec = _spec("code", binding=binding)
    value = {"system": "http://snomed.info/sct", "code": "138875005"}

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


@pytest.mark.req("FR-77")
def test_code_serialise_gives_each_target_its_own_representation() -> None:
    """Not `dict(value)` for all three - `PLAIN_TEXT` (a CSV/xlsx cell) must
    be a scalar, and `FHIR_VALUE` a Coding, not this handler's internal
    storage shape verbatim."""
    handler = CodeHandler(terminology_client=StubTerminologyClient())
    value = {
        "system": "http://snomed.info/sct",
        "code": "138875005",
        "display": "SNOMED CT Concept (procedure)",
    }

    assert handler.serialise(value, SerialisationTarget.PLAIN_TEXT) == "138875005"
    assert handler.serialise(value, SerialisationTarget.FHIR_VALUE) == {
        "system": "http://snomed.info/sct",
        "code": "138875005",
        "display": "SNOMED CT Concept (procedure)",
    }


# --- filter_clause / index expression parity (issue #54, FR-13) -----------
#
# ADR-0012 fixed three index shapes; ADR-0027 made the numeric one
# cast-safe. Neither is useful if the *filter* a caller actually runs
# doesn't render the identical expression the index was built over - an
# index that exists but is never matched by a query plan delivers nothing.
# These tests are the parity argument test_db_property_index_plan.py's
# EXPLAIN proof cannot make on its own, since that proof only exercises one
# handler's fixture, not all five.


@pytest.mark.req("FR-13")
def test_code_equals_filter_uses_containment_not_key_equality() -> None:
    """`index_shape()` declares this property's index as a `jsonb_path_ops`
    GIN - that opclass serves only `@>`/`@?`/`@@`, so a `->>` equality
    predicate (this handler's shape before #54) could never use it at
    all."""
    table = Table("t", MetaData(), Column("value", JSONB))
    handler = CodeHandler(terminology_client=StubTerminologyClient())

    clause = handler.filter_clause(FilterOp.EQUALS, "138875005", table.c.value)

    compiled, params = _compiled_with_params(clause)
    assert "@>" in compiled
    assert {"code": "138875005"} in params.values()


@pytest.mark.req("FR-13")
def test_code_in_filter_is_an_or_of_containments_not_a_single_array_containment() -> None:
    """`@> ANY(array)` is not an indexable form under `jsonb_path_ops` - an
    `OR` of individually-indexable containments is."""
    table = Table("t", MetaData(), Column("value", JSONB))
    handler = CodeHandler(terminology_client=StubTerminologyClient())

    clause = handler.filter_clause(FilterOp.IN, ["1", "2"], table.c.value)

    compiled, params = _compiled_with_params(clause)
    assert compiled.count("@>") == 2
    assert " OR " in compiled.upper()
    assert {"code": "1"} in params.values()
    assert {"code": "2"} in params.values()


@pytest.mark.req("FR-13")
def test_string_equals_filter_matches_an_unquoted_value() -> None:
    """Regression: the pre-#54 `CAST(value AS VARCHAR)` shape stays
    JSON-quoted (`'"abc"'`, never `abc`), so an EQUALS filter for the bare
    value `abc` could never match a real row at all."""
    table = Table("t", MetaData(), Column("value", JSONB))

    clause = StringHandler().filter_clause(FilterOp.EQUALS, "abc", table.c.value)

    assert _compiled(clause) == "(t.value #>> '{}') = 'abc'"


@pytest.mark.req("FR-13")
def test_decimal_equals_filter_uses_the_cast_safe_function() -> None:
    """Regression: the pre-#54 `CAST(value AS NUMERIC)` shape raises
    outright against a retained JSONB *string* value - see ADR-0027."""
    table = Table("t", MetaData(), Column("value", JSONB))

    clause = DecimalHandler().filter_clause(FilterOp.EQUALS, 5, table.c.value)

    compiled = _compiled(clause)
    assert "nptc_numeric_or_null" in compiled
    assert "CAST" not in compiled.upper()


@pytest.mark.req("FR-13")
def test_positive_int_equals_filter_uses_the_cast_safe_function() -> None:
    table = Table("t", MetaData(), Column("value", JSONB))

    clause = PositiveIntHandler().filter_clause(FilterOp.EQUALS, 5, table.c.value)

    assert "nptc_numeric_or_null" in _compiled(clause)


@pytest.mark.req("FR-13")
@pytest.mark.parametrize(
    ("handler", "value", "expression", "shared_fragment"),
    [
        (StringHandler(), "abc", ValueExpression.TEXT_SCALAR, "#>> '{}'"),
        (UrlHandler(), "https://example.org", ValueExpression.TEXT_SCALAR, "#>> '{}'"),
        (DecimalHandler(), 5, ValueExpression.NUMERIC_SCALAR, "nptc_numeric_or_null("),
        (PositiveIntHandler(), 5, ValueExpression.NUMERIC_SCALAR, "nptc_numeric_or_null("),
    ],
)
def test_filter_clause_shares_its_expression_with_the_generated_index(
    handler: object, value: object, expression: ValueExpression, shared_fragment: str
) -> None:
    """The two are built by entirely different code paths -
    `nptc.db.property_indexes.create_statement` for the index,
    `filter_clause` for the query - so nothing but a test like this one
    stops a future edit to either side from silently drifting apart and
    leaving the index unused."""
    table = Table("t", MetaData(), Column("value", JSONB))
    desired = DesiredIndex(
        property_key="k", index_seq=1, kind=IndexKind.EXPRESSION_BTREE, expression=expression
    )

    index_sql = create_statement(desired).as_string(None)
    filter_sql = _compiled(handler.filter_clause(FilterOp.EQUALS, value, table.c.value))  # type: ignore[attr-defined]

    assert shared_fragment in index_sql
    assert shared_fragment in filter_sql
