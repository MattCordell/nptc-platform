"""The FR-53 contract suite: every test here runs once against
``StubTerminologyClient`` and once against ``OntoserverClient`` (the
``client`` fixture in ``conftest.py``), seeded from the same captured FHIR
bodies (``shared/tests/fixtures/terminology/``) via the same production
parsers in ``nptc_shared.terminology.fhir``. This is the issue's acceptance
criterion made mechanical: a behaviour one implementation has and the other
lacks cannot pass.

The ECL/code/display literals below must match the keys in conftest.py's
``EXCHANGES`` tuple exactly - that shared identity is what lets one seeded
fixture answer both implementations.
"""

from __future__ import annotations

import pytest

from nptc_shared.terminology.client import TerminologyClient
from nptc_shared.terminology.errors import TerminologyError
from nptc_shared.terminology.models import AU_LANGUAGE_TAG, SNOMED_CT_AU, SubsumptionOutcome

ECL_TWO_CODES = "122192001 OR 71388002"
ECL_FR84_CHECK = "(122192001 OR 71388002) MINUS <<71388002"
ECL_SINGLE_CONCEPT = "122192001"


@pytest.mark.req("FR-53")
def test_expand_returns_codes_as_strings_in_order(client: TerminologyClient) -> None:
    result = client.expand(ECL_TWO_CODES, edition=SNOMED_CT_AU)
    assert result.codes == ("122192001", "71388002")
    assert all(isinstance(code, str) for code in result.codes)


@pytest.mark.req("FR-53")
def test_an_empty_expansion_is_a_result_not_a_failure(client: TerminologyClient) -> None:
    """FR-84's compliance case: every code in scope, zero violations."""
    result = client.expand(ECL_FR84_CHECK, edition=SNOMED_CT_AU)
    assert result.codes == ()
    assert result.total == 0


def test_expand_with_designations_keeps_the_fsn_verbatim_with_tag_intact(
    client: TerminologyClient,
) -> None:
    result = client.expand(ECL_SINGLE_CONCEPT, edition=SNOMED_CT_AU, include_designations=True)
    assert len(result.concepts) == 1
    fsn = next(d for d in result.concepts[0].designations if d.is_fully_specified_name)
    assert fsn.value == "Acanthamoeba culture (procedure)"


@pytest.mark.req("FR-97")
def test_expand_with_designations_and_display_language_reports_a_non_fsn_designation(
    client: TerminologyClient,
) -> None:
    """FR-97's zero-extra-request path: the status pass's own bulk
    ``$expand`` already carries enough to classify a published label without
    a further per-code request - an FSN designation, at least one other
    designation, and ``display`` set to the requested language's preferred
    term."""
    result = client.expand(
        ECL_SINGLE_CONCEPT,
        edition=SNOMED_CT_AU,
        include_designations=True,
        display_language=AU_LANGUAGE_TAG,
    )
    concept = result.concepts[0]
    assert any(d.is_fully_specified_name for d in concept.designations)
    assert any(not d.is_fully_specified_name for d in concept.designations)
    assert concept.display == "Acanthamoeba culture"


@pytest.mark.req("FR-10")
def test_expand_filter_narrows_to_the_matching_display(client: TerminologyClient) -> None:
    """A coded-property picker's search-as-you-type (issue #247) is server-
    side filtering, not a client-side scan of the whole expansion."""
    result = client.expand(ECL_TWO_CODES, edition=SNOMED_CT_AU, filter="acanth")
    assert result.codes == ("122192001",)


@pytest.mark.req("FR-53")
def test_lookup_exposes_display_fsn_and_active_status(client: TerminologyClient) -> None:
    result = client.lookup("122192001", edition=SNOMED_CT_AU)
    assert result.display == "Acanthamoeba culture"
    assert result.fully_specified_name == "Acanthamoeba culture (procedure)"
    assert result.inactive is False


def test_lookup_of_an_inactive_concept_exposes_reason_and_same_as_association(
    client: TerminologyClient,
) -> None:
    """FR-46 groundwork: inactivation reason and historical association target."""
    result = client.lookup("873871000168106", edition=SNOMED_CT_AU)
    assert result.inactive is True
    assert result.property_values("inactivationReason") == ("Duplicate",)
    assert result.property_values("SAME_AS") == ("122192001",)


@pytest.mark.parametrize(
    ("code_a", "code_b", "expected"),
    [
        ("71388002", "71388002", SubsumptionOutcome.EQUIVALENT),
        ("71388002", "122192001", SubsumptionOutcome.SUBSUMES),
        ("122192001", "71388002", SubsumptionOutcome.SUBSUMED_BY),
        ("122192001", "243120004", SubsumptionOutcome.NOT_SUBSUMED),
    ],
)
def test_subsumes_reports_every_outcome(
    client: TerminologyClient, code_a: str, code_b: str, expected: SubsumptionOutcome
) -> None:
    assert client.subsumes(code_a, code_b, edition=SNOMED_CT_AU) is expected


def test_validate_code_true_for_a_matching_display(client: TerminologyClient) -> None:
    result = client.validate_code("122192001", edition=SNOMED_CT_AU, display="Acanthamoeba culture")
    assert result.result is True


@pytest.mark.req("FR-53")
def test_validate_code_false_for_a_mismatched_display(client: TerminologyClient) -> None:
    """PRD Appendix A.11 row 22 (FR-97): a stored display that matches no
    designation on the bound concept."""
    result = client.validate_code(
        "122192001", edition=SNOMED_CT_AU, display="Acanthamoeba species culture"
    )
    assert result.result is False
    assert result.message is not None


def test_resolved_version_is_a_fully_qualified_uri(client: TerminologyClient) -> None:
    """FR-48: every result records the fully qualified version URI it resolved against."""
    expansion = client.expand(ECL_TWO_CODES, edition=SNOMED_CT_AU)
    assert expansion.resolved_versions
    assert expansion.resolved_versions[0].startswith(
        "http://snomed.info/sct/32506021000036107/version/"
    )

    lookup = client.lookup("122192001", edition=SNOMED_CT_AU)
    assert lookup.resolved_version is not None
    assert lookup.resolved_version.startswith("http://snomed.info/sct/32506021000036107/version/")


@pytest.mark.req("FR-53")
def test_a_request_neither_implementation_was_taught_raises_rather_than_a_default_value(
    client: TerminologyClient,
) -> None:
    """The principal failure mode: FR-54's hazard is an outage that reads as
    a clean, empty result. Neither implementation may do that - both raise."""
    with pytest.raises(TerminologyError):
        client.lookup("999999990", edition=SNOMED_CT_AU)
