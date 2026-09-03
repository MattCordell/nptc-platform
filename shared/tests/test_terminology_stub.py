"""Tests for StubTerminologyClient's own behaviour: seeding, its small ECL
subset, and the not-seeded/unsupported failure modes (FR-53, NFR-37).

The shared contract suite (``test_terminology_contract.py``) covers what the
stub has in common with ``OntoserverClient``; this file covers what is
specific to the stub - the concept-table-driven ``expand``/``lookup``/
``subsumes``/``validate_code`` derivation and its ECL subset - none of which
a real Ontoserver's behaviour needs to match.
"""

from __future__ import annotations

import pytest

from nptc_shared.terminology.models import (
    PROCEDURE_ROOT_CODE,
    SNOMED_CT_AU,
    SNOMED_CT_INTERNATIONAL,
    ConceptProperty,
    Operation,
    SubsumptionOutcome,
)
from nptc_shared.terminology.snomed import ecl_set_of
from nptc_shared.terminology.stub import (
    StubConcept,
    StubEclNotSupportedError,
    StubNotSeededError,
    StubTerminologyClient,
)


def _client(*concepts: StubConcept) -> StubTerminologyClient:
    return StubTerminologyClient(concepts=concepts)


# -- expand: the ECL subset -------------------------------------------------


def test_expand_evaluates_a_disjunction_of_literal_codes() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    result = client.expand("122192001 OR 71388002", edition=SNOMED_CT_AU)
    assert set(result.codes) == {"122192001", "71388002"}


def test_expand_descendants_or_self_includes_the_root() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", parents=("71388002",))
    )
    result = client.expand(f"<<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_AU)
    assert set(result.codes) == {"71388002", "122192001"}


def test_expand_strict_descendants_excludes_the_root() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", parents=("71388002",))
    )
    result = client.expand(f"<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_AU)
    assert set(result.codes) == {"122192001"}


def test_expand_follows_the_parents_chain_transitively() -> None:
    client = _client(
        StubConcept(code="A", fsn="A", parents=("B",)),
        StubConcept(code="B", fsn="B", parents=("71388002",)),
    )
    result = client.expand(f"<<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_AU)
    assert set(result.codes) == {"71388002", "A", "B"}


def test_expand_fr84_minus_idiom_reports_no_violations_when_every_code_is_a_procedure() -> None:
    """The target ergonomics: a test seeds concepts once, then the FR-84
    ``(codes) MINUS <<71388002`` idiom just works, with no per-chunk seeding."""
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", parents=("71388002",))
    )
    codes = ["122192001", "71388002"]
    violations = client.expand(
        f"({ecl_set_of(codes)}) MINUS <<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_AU
    )
    assert violations.codes == ()
    assert len(client.requests) == 1


def test_expand_fr84_minus_idiom_reports_the_non_procedure_code_as_a_violation() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", parents=("71388002",))
    )
    codes = ["122192001", "71388002", "999999990"]
    violations = client.expand(
        f"({ecl_set_of(codes)}) MINUS <<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_AU
    )
    assert violations.codes == ("999999990",)


@pytest.mark.req("FR-75")
def test_expand_minus_attribute_refinement_reports_codes_with_no_value() -> None:
    """``(chunk) MINUS (* : attr = *)`` - ``codes_without_attribute``'s own ECL
    shape (issue #29)."""
    client = _client(
        StubConcept(
            code="47615003",
            fsn="Acetone level (procedure)",
            properties=(ConceptProperty(code="116686009", value="122575003", value_type="code"),),
        ),
        StubConcept(code="121960004", fsn="Adenovirus antigen level (procedure)"),
    )
    result = client.expand(
        "(47615003 OR 121960004) MINUS (* : 116686009 = *)", edition=SNOMED_CT_AU
    )
    assert result.codes == ("121960004",)


@pytest.mark.req("FR-75")
def test_expand_and_attribute_refinement_reports_codes_with_a_subsumed_value() -> None:
    """``(chunk) AND (* : attr = <<root)`` - ``codes_with_attribute_value``'s
    own ECL shape, including the descendant closure on the value side."""
    client = _client(
        StubConcept(code="122575003", fsn="Urine specimen (specimen)"),
        StubConcept(
            code="47615003",
            fsn="Acetone level (procedure)",
            properties=(ConceptProperty(code="116686009", value="122575003", value_type="code"),),
        ),
        StubConcept(
            code="121302000",
            fsn="Hydroxymandelate level (procedure)",
            properties=(ConceptProperty(code="116686009", value="119364003", value_type="code"),),
        ),
    )
    result = client.expand(
        "(47615003 OR 121302000) AND (* : 116686009 = <<122575003)", edition=SNOMED_CT_AU
    )
    assert result.codes == ("47615003",)


@pytest.mark.req("FR-75")
def test_expand_and_attribute_refinement_value_closure_catches_a_descendant_value() -> None:
    """A concept whose ``Has specimen`` value is a *descendant* of the group
    root (e.g. "Urine specimen from catheter" under "Urine specimen") must
    still agree - dropping the ``<<`` on the value side would silently miss
    it and report a false ``TERM_SPECIMEN_DIFFERS``."""
    client = _client(
        StubConcept(code="122575003", fsn="Urine specimen (specimen)"),
        StubConcept(
            code="122575099",
            fsn="Urine specimen from catheter (specimen)",
            parents=("122575003",),
        ),
        StubConcept(
            code="47615003",
            fsn="Acetone level (procedure)",
            properties=(ConceptProperty(code="116686009", value="122575099", value_type="code"),),
        ),
    )
    result = client.expand("(47615003) AND (* : 116686009 = <<122575003)", edition=SNOMED_CT_AU)
    assert result.codes == ("47615003",)


@pytest.mark.req("FR-75")
def test_expand_attribute_refinement_outside_the_recognised_shape_still_raises() -> None:
    """A comparison operator other than ``=`` is more ECL than this stub's
    subset covers - it must raise, never silently match nothing."""
    client = _client()
    with pytest.raises(StubEclNotSupportedError):
        client.expand("(* : 116686009 != *)", edition=SNOMED_CT_AU)


def test_expand_edition_filters_the_descendant_closure() -> None:
    client = _client(
        StubConcept(
            code="122192001",
            fsn="Acanthamoeba culture (procedure)",
            parents=("71388002",),
            editions=("au",),
        )
    )
    au_result = client.expand(f"<<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_AU)
    international_result = client.expand(
        f"<<{PROCEDURE_ROOT_CODE}", edition=SNOMED_CT_INTERNATIONAL
    )
    assert "122192001" in au_result.codes
    assert "122192001" not in international_result.codes


def test_expand_unsupported_ecl_raises_not_an_ecl_engine() -> None:
    client = _client()
    with pytest.raises(StubEclNotSupportedError, match="not an ECL engine"):
        client.expand("122192001:246093002=50875003", edition=SNOMED_CT_AU)


def test_expand_rejects_two_descendant_operators_ored_together_rather_than_fabricating_a_code() -> (
    None
):
    """A malformed ECL the stub's subset doesn't cover must raise, never be
    silently accepted as a literal code built from leftover ECL syntax."""
    client = _client()
    with pytest.raises(StubEclNotSupportedError):
        client.expand("<<71388002 OR <<122192001", edition=SNOMED_CT_AU)


def test_expand_rejects_an_unbalanced_paren_left_over_from_a_double_wrap() -> None:
    client = _client()
    with pytest.raises(StubEclNotSupportedError):
        client.expand("((122192001 OR 71388002) MINUS <<71388002)", edition=SNOMED_CT_AU)


def test_expand_rejects_an_empty_term_rather_than_returning_an_empty_match_set() -> None:
    """An empty parenthesised term ("()") is not a zero-element code list -
    fabricating an empty match set for it is the same silent-empty-result
    class as an un-expanded ValueSet."""
    client = _client()
    with pytest.raises(StubEclNotSupportedError):
        client.expand("()", edition=SNOMED_CT_AU)


def test_expand_active_only_excludes_known_inactive_concepts() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", active=True),
        StubConcept(
            code="873871000168106", fsn="Fixture duplicate concept (procedure)", active=False
        ),
    )
    result = client.expand("122192001 OR 873871000168106", edition=SNOMED_CT_AU, active_only=True)
    assert result.codes == ("122192001",)


def test_expand_active_only_keeps_codes_the_stub_has_no_concept_info_for() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    result = client.expand("122192001 OR 71388002", edition=SNOMED_CT_AU, active_only=True)
    assert set(result.codes) == {"122192001", "71388002"}


def test_expand_count_and_offset_page_the_derived_result_and_report_the_full_total() -> None:
    client = _client(
        StubConcept(code="111111116", fsn="A"),
        StubConcept(code="222222223", fsn="B"),
        StubConcept(code="333333330", fsn="C"),
    )
    result = client.expand(
        "111111116 OR 222222223 OR 333333330", edition=SNOMED_CT_AU, count=1, offset=1
    )
    assert result.total == 3
    assert result.offset == 1
    assert len(result.concepts) == 1
    assert not result.is_complete


@pytest.mark.req("FR-10")
def test_expand_records_offset_count_and_display_language_on_the_request_log() -> None:
    """`StubRequest.offset`/`.count`/`.display_language` (issue #247
    review) let a caller of the stub prove what it actually passed to
    `expand`, independent of what a seeded or derived response returns -
    without this, a caller silently dropping or transposing any of the
    three would pass unnoticed."""
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    client.expand(
        "122192001", edition=SNOMED_CT_AU, count=5, offset=2, display_language="en-x-test"
    )
    assert client.requests[-1].offset == 2
    assert client.requests[-1].count == 5
    assert client.requests[-1].display_language == "en-x-test"


def test_expand_literal_code_disjunction_respects_edition_membership() -> None:
    """Unlike the '<<'/'<' branches, the plain-disjunction branch used to be
    edition-blind - FR-47's dual-edition diff needs every branch to agree."""
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", editions=("au",))
    )
    au_result = client.expand("122192001", edition=SNOMED_CT_AU)
    international_result = client.expand("122192001", edition=SNOMED_CT_INTERNATIONAL)
    assert au_result.codes == ("122192001",)
    assert international_result.codes == ()


@pytest.mark.req("FR-97")
def test_expand_with_designations_includes_synonyms() -> None:
    """Regression: ``expand`` used to emit only the FSN and preferred terms in
    ``include_designations`` mode, silently dropping ``StubConcept.synonyms``
    while ``lookup`` included them - so FR-97's "matches another active
    designation on the concept" outcome was unreachable through the bulk
    ``$expand`` pass the sweep actually drives, only through the much rarer
    delta ``$lookup``."""
    client = _client(
        StubConcept(
            code="122192001",
            fsn="Acanthamoeba culture (procedure)",
            synonyms=("Acanthamoeba species culture",),
        )
    )
    result = client.expand("122192001", edition=SNOMED_CT_AU, include_designations=True)
    values = {designation.value for designation in result.concepts[0].designations}
    assert "Acanthamoeba species culture" in values


@pytest.mark.req("FR-97")
def test_expand_honours_display_language() -> None:
    """Regression: ``expand`` used to ignore ``display_language`` entirely,
    always reporting the first preferred term in dict-insertion order (or the
    FSN) as ``display`` - so an AU-edition expansion's ``display`` could be
    a non-AU preferred term wearing an AU label."""
    client = _client(
        StubConcept(
            code="122192001",
            fsn="Acanthamoeba culture (procedure)",
            preferred_terms={
                "en-x-sctlang-32570271-00003610-6": "Acanthamoeba culture",
                "en-other": "Some other preferred term",
            },
        )
    )
    result = client.expand(
        "122192001",
        edition=SNOMED_CT_AU,
        include_designations=True,
        display_language="en-x-sctlang-32570271-00003610-6",
    )
    assert result.concepts[0].display == "Acanthamoeba culture"


def test_seeded_expansion_takes_precedence_over_the_concept_table() -> None:
    from nptc_shared.terminology.models import Expansion

    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    seeded = Expansion(concepts=(), total=0)
    client.seed_expansion("122192001", seeded, edition=SNOMED_CT_AU)
    result = client.expand("122192001", edition=SNOMED_CT_AU)
    assert result is seeded


# -- lookup ------------------------------------------------------------------


def test_lookup_derives_a_result_from_the_concept_table() -> None:
    client = _client(
        StubConcept(
            code="122192001",
            fsn="Acanthamoeba culture (procedure)",
            preferred_terms={"en-x-sctlang-32570271-00003610-6": "Acanthamoeba culture"},
        )
    )
    result = client.lookup("122192001", edition=SNOMED_CT_AU)
    assert result.fully_specified_name == "Acanthamoeba culture (procedure)"
    assert result.inactive is False


def test_lookup_reports_inactivation_and_same_as_from_seeded_properties() -> None:
    client = _client(
        StubConcept(
            code="873871000168106",
            fsn="Fixture duplicate concept (procedure)",
            active=False,
            properties=(
                ConceptProperty(code="inactivationReason", value="Duplicate", value_type="string"),
                ConceptProperty(code="SAME_AS", value="122192001", value_type="code"),
            ),
        )
    )
    result = client.lookup("873871000168106", edition=SNOMED_CT_AU)
    assert result.inactive is True
    assert result.property_values("SAME_AS") == ("122192001",)


def test_lookup_raises_when_the_stub_was_not_taught_the_code() -> None:
    client = _client()
    with pytest.raises(StubNotSeededError):
        client.lookup("122192001", edition=SNOMED_CT_AU)


def test_lookup_raises_when_the_concept_is_not_in_the_requested_edition() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", editions=("au",))
    )
    with pytest.raises(StubNotSeededError):
        client.lookup("122192001", edition=SNOMED_CT_INTERNATIONAL)


def test_seeded_lookup_takes_precedence_over_the_concept_table() -> None:
    from nptc_shared.terminology.models import LookupResult

    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    seeded = LookupResult(
        code="122192001", system="http://snomed.info/sct", display="Seeded override"
    )
    client.seed_lookup("122192001", seeded, edition=SNOMED_CT_AU)
    assert client.lookup("122192001", edition=SNOMED_CT_AU) is seeded


# -- subsumes ------------------------------------------------------------------


def test_subsumes_equivalent_for_identical_codes() -> None:
    client = _client()
    assert (
        client.subsumes("71388002", "71388002", edition=SNOMED_CT_AU)
        is SubsumptionOutcome.EQUIVALENT
    )


def test_subsumes_via_the_parents_closure() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)", parents=("71388002",))
    )
    assert (
        client.subsumes("71388002", "122192001", edition=SNOMED_CT_AU)
        is SubsumptionOutcome.SUBSUMES
    )
    assert (
        client.subsumes("122192001", "71388002", edition=SNOMED_CT_AU)
        is SubsumptionOutcome.SUBSUMED_BY
    )


def test_subsumes_not_subsumed_for_unrelated_known_concepts() -> None:
    client = _client(
        StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"),
        StubConcept(code="243120004", fsn="Regime/therapy (regime/therapy)"),
    )
    assert (
        client.subsumes("122192001", "243120004", edition=SNOMED_CT_AU)
        is SubsumptionOutcome.NOT_SUBSUMED
    )


def test_subsumes_raises_when_a_code_is_unknown_to_the_stub() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    with pytest.raises(StubNotSeededError):
        client.subsumes("122192001", "999999990", edition=SNOMED_CT_AU)


# -- validate_code -----------------------------------------------------------


def test_validate_code_true_when_display_matches_a_known_designation() -> None:
    client = _client(
        StubConcept(
            code="122192001",
            fsn="Acanthamoeba culture (procedure)",
            preferred_terms={"en-x-sctlang-32570271-00003610-6": "Acanthamoeba culture"},
        )
    )
    result = client.validate_code("122192001", edition=SNOMED_CT_AU, display="Acanthamoeba culture")
    assert result.result is True


def test_validate_code_false_when_display_matches_no_designation() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    result = client.validate_code(
        "122192001", edition=SNOMED_CT_AU, display="Acanthamoeba species culture"
    )
    assert result.result is False
    assert result.message is not None


def test_validate_code_with_no_display_is_true_for_a_known_code() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    result = client.validate_code("122192001", edition=SNOMED_CT_AU)
    assert result.result is True


def test_validate_code_raises_when_the_code_is_unknown() -> None:
    client = _client()
    with pytest.raises(StubNotSeededError):
        client.validate_code("122192001", edition=SNOMED_CT_AU, display="Anything")


def test_validate_code_with_a_value_set_url_raises_unless_seeded() -> None:
    """FR-10's value-set binding check: the stub has no ECL engine to
    evaluate membership, so it must never silently pass a value-set check
    it cannot actually evaluate - the concept-table fallback is code-system
    only, and does not apply here."""
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    with pytest.raises(StubNotSeededError):
        client.validate_code(
            "122192001",
            edition=SNOMED_CT_AU,
            value_set_url="http://example.test/fhir/ValueSet/specimen",
        )


def test_validate_code_with_a_value_set_url_returns_the_seeded_result() -> None:
    from nptc_shared.terminology.models import ValidationResult

    client = _client()
    seeded = ValidationResult(code="122192001", result=True, display="Acanthamoeba culture")
    client.seed_validate_code(
        "122192001",
        seeded,
        value_set_url="http://example.test/fhir/ValueSet/specimen",
        edition=SNOMED_CT_AU,
    )
    result = client.validate_code(
        "122192001",
        edition=SNOMED_CT_AU,
        value_set_url="http://example.test/fhir/ValueSet/specimen",
    )
    assert result is seeded


def test_a_value_set_url_seed_does_not_satisfy_a_plain_code_system_check() -> None:
    """A value-set-scoped seed and a code-system-scoped seed are genuinely
    different checks - one must never silently answer the other."""
    from nptc_shared.terminology.models import ValidationResult

    client = _client()
    client.seed_validate_code(
        "122192001",
        ValidationResult(code="122192001", result=True),
        value_set_url="http://example.test/fhir/ValueSet/specimen",
        edition=SNOMED_CT_AU,
    )
    with pytest.raises(StubNotSeededError):
        client.validate_code("122192001", edition=SNOMED_CT_AU)


def test_seed_error_for_value_set_validate_code_is_reachable() -> None:
    """Before the fix, validate_code always logged/raised against
    CODE_SYSTEM_VALIDATE_CODE, so a seeded VALUE_SET_VALIDATE_CODE error
    could never trigger."""
    from nptc_shared.terminology.errors import TerminologyStatusError

    client = _client()
    configured = TerminologyStatusError(
        "simulated", operation=Operation.VALUE_SET_VALIDATE_CODE, status_code=500
    )
    client.seed_error(Operation.VALUE_SET_VALIDATE_CODE, configured, key="122192001")
    with pytest.raises(TerminologyStatusError, match="simulated"):
        client.validate_code(
            "122192001",
            edition=SNOMED_CT_AU,
            value_set_url="http://example.test/fhir/ValueSet/specimen",
        )


# -- errors, introspection ---------------------------------------------------


def test_seed_error_raises_the_configured_error_on_the_next_matching_call() -> None:
    from nptc_shared.terminology.errors import TerminologyTransportError

    client = _client()
    configured = TerminologyTransportError("simulated outage", operation=Operation.LOOKUP)
    client.seed_error(Operation.LOOKUP, configured, key="122192001")
    with pytest.raises(TerminologyTransportError, match="simulated outage"):
        client.lookup("122192001", edition=SNOMED_CT_AU)


def test_requests_records_every_call_in_order() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    client.lookup("122192001", edition=SNOMED_CT_AU)
    client.expand("122192001", edition=SNOMED_CT_AU)
    assert [request.operation for request in client.requests] == [
        Operation.LOOKUP,
        Operation.EXPAND,
    ]


def test_reset_clears_the_request_log_but_not_seeded_data() -> None:
    client = _client(StubConcept(code="122192001", fsn="Acanthamoeba culture (procedure)"))
    client.lookup("122192001", edition=SNOMED_CT_AU)
    assert len(client.requests) == 1
    client.reset()
    assert client.requests == ()
    # Seeded data survives the reset - a second call still resolves.
    client.lookup("122192001", edition=SNOMED_CT_AU)
    assert len(client.requests) == 1
