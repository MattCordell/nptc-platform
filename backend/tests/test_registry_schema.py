"""`nptc.registry.schema` tests (issue #52, FR-09, FR-10, ADR-0012).

No fixtures beyond `_spec` (mirrors `test_datatype_handlers.py`'s own
helper) - schema derivation and value validation take a frozen
`PropertyDefinitionSpec` and a handler, never the ORM model or a database.
"""

from __future__ import annotations

import pytest

from nptc.registry import BindingSpec, PropertyDefinitionSpec
from nptc.registry.datatypes.code import CodeHandler
from nptc.registry.datatypes.positive_int import PositiveIntHandler
from nptc.registry.datatypes.string import StringHandler
from nptc.registry.schema import (
    MalformedConstraintsError,
    property_schema,
    reset_schema_cache,
    validate_constraints,
    validate_values,
)
from nptc_shared.terminology.stub import StubTerminologyClient


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    # See `reset_schema_cache`'s own docstring: the (key, row_version)
    # cache is process-global by design, and every test below reuses the
    # same key/row_version with a different spec shape.
    reset_schema_cache()


def _spec(
    *,
    key: str = "test_property",
    cardinality: str = "0..1",
    constraints: dict[str, object] | None = None,
    binding: BindingSpec | None = None,
) -> PropertyDefinitionSpec:
    return PropertyDefinitionSpec(
        key=key,
        label="Test property",
        datatype="string",
        cardinality=cardinality,
        scope=frozenset({"maintenance"}),
        required_for_submission=False,
        required_for_publication=False,
        binding=binding,
        filterable=False,
        constraints=constraints or {},
    )


class TestPropertySchema:
    def test_derives_the_handler_fragment(self) -> None:
        spec = _spec(constraints={"maxLength": 5})
        schema = property_schema(spec, StringHandler(), row_version=1)
        assert schema == {"type": "string", "maxLength": 5}

    def test_memoises_by_key_and_row_version(self) -> None:
        """A second call with the same (key, row_version) returns the
        cached fragment even when the spec passed this time would derive
        something different - this is the memoisation ADR-0012 describes,
        not a bug: `row_version` is the only thing meant to bust it."""
        spec_a = _spec(constraints={"maxLength": 5})
        spec_b = _spec(constraints={"maxLength": 999})
        first = property_schema(spec_a, StringHandler(), row_version=1)
        second = property_schema(spec_b, StringHandler(), row_version=1)
        assert first == second == {"type": "string", "maxLength": 5}

    def test_a_new_row_version_re_derives(self) -> None:
        spec_a = _spec(constraints={"maxLength": 5})
        spec_b = _spec(constraints={"maxLength": 999})
        first = property_schema(spec_a, StringHandler(), row_version=1)
        second = property_schema(spec_b, StringHandler(), row_version=2)
        assert first != second
        assert second == {"type": "string", "maxLength": 999}

    def test_a_hit_is_genuinely_lru_not_fifo(self) -> None:
        """Re-derive `_SCHEMA_CACHE_SIZE` distinct keys, re-touching the
        very first one just before the cache is full - a FIFO eviction
        would drop it anyway (it is the oldest by insertion order); a
        genuine LRU must not, since the touch makes it the most recently
        used."""
        import nptc.registry.schema as schema_module

        cache_size = schema_module._SCHEMA_CACHE_SIZE
        handler = StringHandler()
        first_key = _spec(key="key-0", constraints={"maxLength": 1})
        property_schema(first_key, handler, row_version=1)

        for n in range(1, cache_size):
            property_schema(
                _spec(key=f"key-{n}", constraints={"maxLength": 1}), handler, row_version=1
            )

        # Touch the first key again - now the most recently used.
        property_schema(first_key, handler, row_version=1)
        # One more distinct key forces exactly one eviction.
        property_schema(
            _spec(key=f"key-{cache_size}", constraints={"maxLength": 1}), handler, row_version=1
        )

        assert ("key-0", 1) in schema_module._FRAGMENT_CACHE
        assert ("key-1", 1) not in schema_module._FRAGMENT_CACHE


class TestValidateConstraints:
    def test_a_conforming_constraints_document_passes(self) -> None:
        spec = _spec(constraints={"maxLength": 10})
        validate_constraints(spec, StringHandler())  # does not raise

    def test_a_malformed_constraints_document_raises(self) -> None:
        spec = _spec(constraints={"maxLength": "not-an-integer"})
        with pytest.raises(MalformedConstraintsError, match="test_property"):
            validate_constraints(spec, StringHandler())


class TestValidateValuesCardinality:
    @pytest.mark.req("FR-09")
    def test_zero_or_one_rejects_a_second_value(self) -> None:
        spec = _spec(cardinality="0..1")
        issues = validate_values(["a", "b"], spec, StringHandler(), row_version=1)
        assert any(issue.code == "cardinality-above-maximum" for issue in issues)

    def test_one_one_rejects_zero_values(self) -> None:
        spec = _spec(cardinality="1..1")
        issues = validate_values([], spec, StringHandler(), row_version=1)
        assert any(issue.code == "cardinality-below-minimum" for issue in issues)

    def test_zero_or_one_accepts_exactly_one(self) -> None:
        spec = _spec(cardinality="0..1")
        issues = validate_values(["a"], spec, StringHandler(), row_version=1)
        assert issues == []

    def test_zero_or_many_accepts_an_arbitrary_count(self) -> None:
        spec = _spec(cardinality="0..*")
        issues = validate_values(
            ["a", "b", "c", "d", "e", "f", "g"], spec, StringHandler(), row_version=1
        )
        assert issues == []

    def test_one_or_many_rejects_zero_values(self) -> None:
        spec = _spec(cardinality="1..*")
        issues = validate_values([], spec, StringHandler(), row_version=1)
        assert any(issue.code == "cardinality-below-minimum" for issue in issues)


class TestValidateValuesSchemaAndHandler:
    def test_a_schema_violation_is_reported_with_its_ordinal(self) -> None:
        spec = _spec(cardinality="0..*", constraints={"maxLength": 3})
        issues = validate_values(["ok", "way too long"], spec, StringHandler(), row_version=1)
        assert len(issues) == 1
        assert issues[0].code == "schema-violation"
        assert issues[0].path == "1"

    def test_a_schema_failure_short_circuits_the_handler_call(self) -> None:
        """A value that fails PositiveIntHandler's own JSON Schema type
        check (e.g. a string) must not reach handler.validate() and
        produce a second, redundant issue."""
        spec = PropertyDefinitionSpec(
            key="count",
            label="Count",
            datatype="positiveInt",
            cardinality="0..1",
            scope=frozenset({"maintenance"}),
            required_for_submission=False,
            required_for_publication=False,
            binding=None,
            filterable=False,
            constraints={},
        )
        issues = validate_values(["not-a-number"], spec, PositiveIntHandler(), row_version=1)
        assert len(issues) == 1
        assert issues[0].code == "schema-violation"

    def test_a_handler_validate_issue_is_reported_with_its_ordinal(self) -> None:
        """A bad Verhoeff check digit passes CodeHandler's own
        `json_schema_fragment` (which only describes shape) and is caught
        by `handler.validate()` instead - the case a schema-only check
        cannot cover."""
        spec = PropertyDefinitionSpec(
            key="condition",
            label="Condition",
            datatype="code",
            cardinality="0..*",
            scope=frozenset({"maintenance"}),
            required_for_submission=False,
            required_for_publication=False,
            binding=None,
            filterable=False,
            constraints={},
        )
        handler = CodeHandler(terminology_client=StubTerminologyClient())
        # A real Australian-extension SCTID (PRD-quoted, matching
        # shared/tests/test_sctid.py's own known-good fixture) and the
        # same digits with a corrupted check digit.
        good = {"system": "http://snomed.info/sct", "code": "873871000168106"}
        bad = {"system": "http://snomed.info/sct", "code": "873871000168107"}
        issues = validate_values([good, bad], spec, handler, row_version=1)
        assert len(issues) == 1
        assert issues[0].code == "invalid-sctid-check-digit"
        assert issues[0].path == "1"

    def test_multiple_values_each_report_their_own_issues(self) -> None:
        spec = _spec(cardinality="0..*", constraints={"maxLength": 3})
        issues = validate_values(
            ["too long", "also too long"], spec, StringHandler(), row_version=1
        )
        assert {issue.path for issue in issues} == {"0", "1"}
