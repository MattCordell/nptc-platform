"""Tests for the FR-52 batch sweep and the FR-84 hierarchy check.

Most of these assert a *call count*, because that is what the requirements
are about: FR-52 forbids one request per code, FR-84 mandates one batch
request for the whole hierarchy check (NFR-38 test 13), and both are
behaviours that pass every correctness test while being wrong.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from nptc_shared.terminology.config import TerminologyConfig
from nptc_shared.terminology.errors import (
    OperationOutcomeIssue,
    TerminologyConfigError,
    TerminologyStatusError,
    TerminologyTransportError,
)
from nptc_shared.terminology.models import (
    AU_LANGUAGE_TAG,
    HAS_SPECIMEN_ATTRIBUTE,
    PROCEDURE_ROOT_CODE,
    SNOMED_CT_AU,
    SNOMED_CT_INTERNATIONAL,
    ConceptProperty,
    Edition,
    ExpandedConcept,
    Expansion,
    Operation,
    ValidationResult,
)
from nptc_shared.terminology.ontoserver import OntoserverClient
from nptc_shared.terminology.stub import StubConcept, StubTerminologyClient
from nptc_shared.terminology.sweep import LabelConfirmation, SweepResult, TerminologySweep

# Codes are arbitrary but well-formed (6-18 digits): ecl_set_of refuses
# anything else, and screening for that is the caller's job (see
# nptc_transform.terminology_check), not the sweep's.
FIRST_CODE = 100000001


def _codes(count: int, *, start: int = FIRST_CODE) -> tuple[str, ...]:
    return tuple(str(start + index) for index in range(count))


def _procedure(
    code: str,
    *,
    fsn: str | None = None,
    active: bool = True,
    parents: tuple[str, ...] = (PROCEDURE_ROOT_CODE,),
    editions: tuple[str, ...] = ("au", "int"),
) -> StubConcept:
    return StubConcept(
        code=code,
        fsn=fsn if fsn is not None else f"Fixture concept {code} (procedure)",
        preferred_terms={"en": f"Fixture concept {code}"},
        active=active,
        parents=parents,
        editions=editions,
    )


def _stub(*concepts: StubConcept) -> StubTerminologyClient:
    return StubTerminologyClient(
        concepts=concepts,
        resolved_version={
            "au": "http://snomed.info/sct/32506021000036107/version/20260531",
            "int": "http://snomed.info/sct/900000000000207008/version/20260501",
        },
    )


def _expansions(client: StubTerminologyClient) -> tuple[str, ...]:
    """The ECL of every status expansion - the FR-84 check excluded."""
    return tuple(
        request.detail
        for request in client.requests
        if request.operation is Operation.EXPAND and " MINUS " not in request.detail
    )


def _hierarchy_expansions(client: StubTerminologyClient) -> tuple[str, ...]:
    return tuple(
        request.detail
        for request in client.requests
        if request.operation is Operation.EXPAND and " MINUS " in request.detail
    )


def _lookups(client: StubTerminologyClient) -> tuple[str, ...]:
    return tuple(
        request.detail for request in client.requests if request.operation is Operation.LOOKUP
    )


def _not_found(code: str) -> TerminologyStatusError:
    """What a conformant server returns for a $lookup of a code it lacks."""
    return TerminologyStatusError(
        f"CodeSystem/$lookup returned 404 for {code}",
        operation=Operation.LOOKUP,
        status_code=404,
        issues=(OperationOutcomeIssue(severity="error", code="not-found"),),
    )


@pytest.mark.req("FR-52")
def test_a_sweep_of_n_codes_issues_one_expansion_per_chunk_and_no_per_code_calls() -> None:
    """FR-52's whole point, asserted the only way it can be: by call count.

    Seven codes at a chunk size of three is three expansions, not seven
    $validate-code calls and not seven $lookups. The failure mode this closes
    is a sweep that is entirely correct and issues 40,000 requests.
    """
    codes = _codes(7)
    client = _stub(*(_procedure(code) for code in codes))
    sweep = TerminologySweep(client, chunk_size=3)

    result = sweep.run(codes, edition=SNOMED_CT_AU)

    assert len(_expansions(client)) == 3  # ceil(7 / 3)
    assert _lookups(client) == ()
    assert result.active == codes
    assert result.inactive == ()
    assert result.absent == ()


@pytest.mark.req("FR-52")
def test_each_chunk_expands_the_disjunction_of_exactly_its_own_codes() -> None:
    codes = _codes(5)
    client = _stub(*(_procedure(code) for code in codes))

    TerminologySweep(client, chunk_size=2).run(codes, edition=SNOMED_CT_AU)

    assert _expansions(client) == (
        "100000001 OR 100000002",
        "100000003 OR 100000004",
        "100000005",
    )


@pytest.mark.req("FR-52")
def test_a_repeated_code_costs_one_slot_not_one_per_binding() -> None:
    """The catalogue binds the same concept from many rows; the sweep is over
    the *set* of codes, so 300 rows sharing 3 codes is one chunk."""
    client = _stub(_procedure("100000001"), _procedure("100000002"))

    TerminologySweep(client, chunk_size=300).run(
        ["100000002", "100000001", "100000002", "100000001"], edition=SNOMED_CT_AU
    )

    assert _expansions(client) == ("100000001 OR 100000002",)


@pytest.mark.req("FR-52")
def test_chunk_size_is_configurable_from_the_environment() -> None:
    config = TerminologyConfig.from_env({"NPTC_TX_CHUNK_SIZE": "2", "NPTC_TX_MAX_CONCURRENCY": "1"})
    client = _stub(*(_procedure(code) for code in _codes(4)))

    sweep = TerminologySweep.from_config(client, config)
    sweep.run(_codes(4), edition=SNOMED_CT_AU)

    assert sweep.chunk_size == 2
    assert sweep.max_concurrency == 1
    assert len(_expansions(client)) == 2


@pytest.mark.req("FR-52")
def test_an_empty_catalogue_issues_no_requests_at_all() -> None:
    client = _stub()

    result = TerminologySweep(client).run([], edition=SNOMED_CT_AU)

    assert client.requests == ()
    assert result == SweepResult(edition_label="au")


def test_a_chunk_size_below_one_is_refused_rather_than_looping_or_skipping() -> None:
    """One exception type for this floor regardless of construction path -
    ``TerminologyConfig.__post_init__`` raises the same type on the
    ``from_config`` path, and the CLI's ``except TerminologyError`` needs a
    single type to catch."""
    with pytest.raises(TerminologyConfigError, match="chunk_size"):
        TerminologySweep(_stub(), chunk_size=0)
    with pytest.raises(TerminologyConfigError, match="max_concurrency"):
        TerminologySweep(_stub(), max_concurrency=0)


class _CappedPageClient(StubTerminologyClient):
    """A server that will not return more than ``page_size`` members at once.

    Ontoserver and friends impose exactly this ceiling; a sweep that treats
    one expansion as exhaustive silently reports the codes past the cap as
    absent, which is indistinguishable from a genuinely short result.
    """

    def __init__(self, *, page_size: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._page_size = page_size

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        capped = self._page_size if count is None else min(count, self._page_size)
        return super().expand(
            ecl,
            edition=edition,
            count=capped,
            offset=offset,
            include_designations=include_designations,
            display_language=display_language,
            active_only=active_only,
        )


@pytest.mark.req("FR-52")
def test_a_page_capped_below_the_chunk_size_is_paged_not_reported_as_absent() -> None:
    codes = _codes(5)
    client = _CappedPageClient(page_size=2, concepts=[_procedure(code) for code in codes])

    result = TerminologySweep(client, chunk_size=5).run(codes, edition=SNOMED_CT_AU)

    assert result.active == codes
    assert result.absent == ()
    assert _lookups(client) == ()
    assert len(_expansions(client)) == 3  # 2 + 2 + 1, one chunk


class _OmittingClient(StubTerminologyClient):
    """A server that leaves ``omitted`` out of an expansion and reports a
    ``total`` consistent with what it returned - so nothing signals the gap.

    Real servers do this for reasons the client cannot see. The point of the
    delta pass is that a code missing from an expansion is a *question*, not
    a verdict.
    """

    def __init__(self, *, omitted: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._omitted = omitted

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        expansion = super().expand(
            ecl,
            edition=edition,
            count=count,
            offset=offset,
            include_designations=include_designations,
            display_language=display_language,
            active_only=active_only,
        )
        kept = tuple(concept for concept in expansion.concepts if concept.code != self._omitted)
        return Expansion(
            concepts=kept,
            total=len(kept),
            offset=expansion.offset,
            resolved_versions=expansion.resolved_versions,
        )


@pytest.mark.req("FR-52")
def test_a_lookup_that_reports_the_code_active_overturns_its_absence_from_the_expansion() -> None:
    """The delta pass is what makes an omission recoverable rather than
    fatal: the code is active, the lookup says so, and it must not be
    reported inactive because one expansion did not mention it."""
    codes = _codes(2)
    client = _OmittingClient(omitted=codes[1], concepts=[_procedure(code) for code in codes])

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert _lookups(client) == (codes[1],)
    assert result.active == codes
    assert result.inactive == ()


class _EmptyPageClient(StubTerminologyClient):
    """A server that promises members and returns none - forever.

    Pathological, but the loop shape has to survive it: a sweep that trusted
    ``total`` and kept advancing the offset would never return.
    """

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        super().expand(
            ecl,
            edition=edition,
            count=count,
            offset=offset,
            include_designations=include_designations,
            display_language=display_language,
            active_only=active_only,
        )
        return Expansion(concepts=(), total=99, offset=offset)


@pytest.mark.req("FR-54")
def test_a_server_that_promises_members_and_returns_none_terminates_the_sweep() -> None:
    codes = _codes(2)
    client = _EmptyPageClient(concepts=[_procedure(code) for code in codes])

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    # One status expansion, one hierarchy expansion - not an unbounded paging
    # loop - and every code falls through to the delta pass, which resolves
    # them one at a time rather than reporting them absent.
    assert len(_expansions(client)) == 1
    assert len(_hierarchy_expansions(client)) == 1
    assert result.active == codes


@pytest.mark.req("FR-84")
def test_more_violations_than_one_page_holds_are_all_reported() -> None:
    """Under-reporting an FR-84 violation is the failure that matters: the
    codes past the page boundary would publish as compliant."""
    codes = _codes(3)
    client = _CappedPageClient(
        page_size=2, concepts=[_procedure(code, parents=("105590001",)) for code in codes]
    )

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert result.hierarchy_violations == codes
    assert len(_hierarchy_expansions(client)) == 2


@pytest.mark.req("FR-52")
def test_only_the_delta_gets_a_lookup_and_an_inactive_code_is_reported_inactive() -> None:
    """FR-52 step 2: the second pass is the delta, not the catalogue."""
    codes = _codes(4)
    client = _stub(
        _procedure(codes[0]),
        _procedure(codes[1]),
        _procedure(codes[2]),
        _procedure(codes[3], active=False),
    )

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    assert _lookups(client) == (codes[3],)
    assert result.active == codes[:3]
    assert result.inactive == (codes[3],)
    assert result.absent == ()
    assert [lookup.code for lookup in result.lookups] == [codes[3]]


@pytest.mark.req("FR-52")
def test_the_delta_lookup_requests_the_inactive_property_explicitly() -> None:
    """The stub can't prove this - it ignores ``properties`` entirely and
    always returns every seeded property regardless of what was asked for,
    which is exactly why the underlying bug was invisible against it. FHIR R4
    does not require a server to volunteer a property that wasn't requested,
    so without an explicit ``property=inactive`` a code missing from the bulk
    expansion for an unrelated reason (a truncated page, say) would come back
    ``LookupResult.inactive is None`` and be misclassified as inactive - a
    false blocking defect against a real binding."""
    code = "122192001"
    captured_properties: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("$expand"):
            return httpx.Response(
                200, json={"resourceType": "ValueSet", "expansion": {"total": 0, "contains": []}}
            )
        captured_properties.extend(request.url.params.get_list("property"))
        return httpx.Response(
            200,
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "name", "valueString": "SNOMED CT"},
                    {
                        "name": "property",
                        "part": [
                            {"name": "code", "valueCode": "inactive"},
                            {"name": "value", "valueBoolean": False},
                        ],
                    },
                ],
            },
        )

    client = OntoserverClient(
        TerminologyConfig(base_url="https://tx.example.test/fhir"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )

    with client:
        result = TerminologySweep(client).run([code], edition=SNOMED_CT_AU)

    assert "inactive" in captured_properties
    assert result.active == (code,)


class _CountingLookupClient(StubTerminologyClient):
    """Counts every ``$lookup`` invocation and fails on ``fail_at``."""

    def __init__(self, *, fail_at: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._fail_at = fail_at
        self.lookup_count = 0

    def lookup(
        self,
        code: str,
        *,
        edition: Edition,
        properties: tuple[str, ...] = (),
        display_language: str | None = None,
    ) -> object:  # type: ignore[override]
        self.lookup_count += 1
        if code == self._fail_at:
            raise TerminologyStatusError(
                "CodeSystem/$lookup returned 503", operation=Operation.LOOKUP, status_code=503
            )
        return super().lookup(
            code, edition=edition, properties=properties, display_language=display_language
        )


@pytest.mark.req("FR-52")
def test_the_delta_pass_stops_after_the_failing_batch_rather_than_draining_the_queue() -> None:
    """``Executor.map`` submits every future the instant it is called
    (``[self.submit(...) for ...]``, eagerly), so a failure on any one code
    would not stop the remaining codes - potentially thousands of them - from
    being queued and executed before the exception ever surfaces. With
    batched submission, a failure in the first batch means the second batch
    is never submitted: 20 codes at max_concurrency=4 with the first code
    failing caps total lookups at 4, not 20."""
    codes = _codes(20)
    client = _CountingLookupClient(
        fail_at=codes[0], concepts=[_procedure(code, active=False) for code in codes]
    )

    with pytest.raises(TerminologyStatusError):
        TerminologySweep(client, chunk_size=300, max_concurrency=4).run(codes, edition=SNOMED_CT_AU)

    assert client.lookup_count <= 4


@pytest.mark.req("FR-52")
def test_a_code_the_server_does_not_have_is_absent_not_inactive() -> None:
    codes = _codes(2)
    client = _stub(_procedure(codes[0]), _procedure(codes[1], editions=("int",)))
    client.seed_error(Operation.LOOKUP, _not_found(codes[1]), key=codes[1])

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert result.absent == (codes[1],)
    assert result.inactive == ()
    assert result.active == (codes[0],)


@pytest.mark.req("FR-54")
def test_a_server_failure_during_the_delta_pass_raises_rather_than_reporting_absences() -> None:
    """The FR-54 hazard in miniature: an outage that reads as 20,000 codes
    that do not exist is worse than an outage that reads as an outage."""
    codes = _codes(2)
    client = _stub(_procedure(codes[0]), _procedure(codes[1], active=False))
    client.seed_error(
        Operation.LOOKUP,
        TerminologyStatusError(
            "CodeSystem/$lookup returned 503", operation=Operation.LOOKUP, status_code=503
        ),
        key=codes[1],
    )

    with pytest.raises(TerminologyStatusError):
        TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-54")
def test_a_transport_failure_is_never_mistaken_for_a_missing_code() -> None:
    codes = _codes(2)
    client = _stub(_procedure(codes[0]), _procedure(codes[1], active=False))
    client.seed_error(
        Operation.LOOKUP,
        TerminologyTransportError("connection reset", operation=Operation.LOOKUP),
        key=codes[1],
    )

    with pytest.raises(TerminologyTransportError):
        TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)


@pytest.mark.req("FR-84")
@pytest.mark.req("NFR-38")
def test_the_hierarchy_check_is_one_request_for_the_whole_catalogue() -> None:
    """NFR-38 test 13's second half: asserted to issue **one** batch request
    rather than one per code."""
    codes = _codes(50)
    client = _stub(*(_procedure(code) for code in codes))

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    hierarchy = _hierarchy_expansions(client)
    assert len(hierarchy) == 1
    assert hierarchy[0].endswith(f") MINUS <<{PROCEDURE_ROOT_CODE}")
    assert result.hierarchy_violations == ()
    assert Operation.SUBSUMES not in {request.operation for request in client.requests}


@pytest.mark.req("FR-84")
def test_the_hierarchy_check_chunks_at_catalogue_scale_rather_than_one_request() -> None:
    """A single disjunction over the whole catalogue does not fit in a
    request at the PRD's 20,000-code planning ceiling (~340KB of
    percent-encoded ECL, measured with this repo's own builders - see
    ADR-0005). Chunked the same way the status pass is: 7 codes at
    chunk_size=3 is ceil(7/3)=3 hierarchy requests, not 1."""
    codes = _codes(7)
    client = _stub(*(_procedure(code) for code in codes))

    result = TerminologySweep(client, chunk_size=3).run(codes, edition=SNOMED_CT_AU)

    assert len(_hierarchy_expansions(client)) == 3
    assert result.hierarchy_violations == ()


@pytest.mark.req("FR-84")
def test_absent_codes_never_appear_in_the_hierarchy_ecl() -> None:
    """A code the status pass already proved absent must not also be
    concatenated into the disjunction sent for the hierarchy check - it
    shrinks the ECL, and it stops a transcription-error code (unknown to the
    server) from ever appearing in a request at all, rather than relying on
    every server tolerating an unknown concept reference inside one.

    ``codes[2]`` is registered but only for "int", not the "au" edition under
    test here, so it is genuinely invisible to the AU bulk pass (an
    unregistered code, by contrast, is treated by this stub as visible by
    default - see ``_visible_in_edition``'s own docstring) and falls through
    to the seeded not-found lookup.
    """
    codes = _codes(3)
    client = _stub(
        _procedure(codes[0]), _procedure(codes[1]), _procedure(codes[2], editions=("int",))
    )
    client.seed_error(Operation.LOOKUP, _not_found(codes[2]), key=codes[2])

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    hierarchy = _hierarchy_expansions(client)
    assert len(hierarchy) == 1
    assert codes[2] not in hierarchy[0]
    assert result.absent == (codes[2],)
    assert result.hierarchy_violations == ()


@pytest.mark.req("FR-84")
def test_a_sweep_where_every_code_is_absent_makes_no_hierarchy_request_at_all() -> None:
    codes = _codes(2)
    client = _stub(*(_procedure(code, editions=("int",)) for code in codes))
    for code in codes:
        client.seed_error(Operation.LOOKUP, _not_found(code), key=code)

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert result.absent == codes
    assert _hierarchy_expansions(client) == ()


@pytest.mark.req("FR-84")
@pytest.mark.req("NFR-38")
def test_a_code_outside_the_procedure_hierarchy_is_detected_by_that_one_request() -> None:
    """NFR-38 test 13's first half. The failure mode is not a false positive
    but a check that passes everything forever: writing the ECL as
    ``<code MINUS <<71388002`` asks for each code's descendants, which for a
    leaf concept is empty, so nothing is ever reported."""
    codes = _codes(3)
    client = _stub(
        _procedure(codes[0]),
        _procedure(codes[1]),
        _procedure(codes[2], fsn="Fixture substance (substance)", parents=("105590001",)),
    )

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    assert result.hierarchy_violations == (codes[2],)
    assert len(_hierarchy_expansions(client)) == 1


@pytest.mark.req("FR-84")
def test_a_code_absent_from_the_edition_is_not_reported_as_a_hierarchy_violation() -> None:
    """An AU-only code checked against International is absent, not
    out-of-scope: an ECL enumerating codes returns only concepts that exist,
    so the status pass owns that finding and this one must stay silent."""
    codes = _codes(2)
    client = _stub(_procedure(codes[0]), _procedure(codes[1], editions=("au",)))
    client.seed_error(Operation.LOOKUP, _not_found(codes[1]), key=codes[1])

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_INTERNATIONAL)

    assert result.absent == (codes[1],)
    assert result.hierarchy_violations == ()


@pytest.mark.req("FR-99")
def test_a_subsumed_concept_with_a_non_procedure_tag_is_a_warning_not_a_violation() -> None:
    """PRD Appendix A.10: 71388002 |Procedure| subsumes 243120004
    |Regime/therapy (regime/therapy)|, so the tag cannot be inferred from
    subsumption. FR-84 and FR-99 are therefore separate outcomes, and the
    concept must not appear in ``hierarchy_violations``."""
    codes = _codes(2)
    client = _stub(
        _procedure(codes[0]),
        _procedure(codes[1], fsn="Fixture regime (regime/therapy)"),
    )

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    assert result.hierarchy_violations == ()
    assert [(tag.code, tag.tag) for tag in result.unexpected_semantic_tags] == [
        (codes[1], "regime/therapy")
    ]
    assert (
        result.unexpected_semantic_tags[0].fully_specified_name == "Fixture regime (regime/therapy)"
    )


@pytest.mark.req("FR-99")
def test_the_semantic_tag_check_costs_no_additional_requests_per_code() -> None:
    """ "No additional requests" means no *per-code* $lookup/$validate-code -
    the hierarchy check itself is chunked the same way the status pass is
    (fix for catalogue-scale ECL size), so 4 codes at chunk_size=2 is 2 status
    chunks plus 2 hierarchy chunks, not 2 + 1."""
    codes = _codes(4)
    client = _stub(*(_procedure(code, fsn=f"Fixture {code} (regime/therapy)") for code in codes))

    result = TerminologySweep(client, chunk_size=2).run(codes, edition=SNOMED_CT_AU)

    assert len(result.unexpected_semantic_tags) == 4
    assert len(_expansions(client)) == 2
    assert len(_hierarchy_expansions(client)) == 2
    assert _lookups(client) == ()
    assert len(client.requests) == 4


class _OverlappingPageClient(StubTerminologyClient):
    """Shifts each page back by one from what was actually requested,
    reproducing a one-item overlap between consecutive pages - the
    misbehaviour ``_expand_chunk``'s own docstring says the loop tolerates
    rather than requires paging correctness from the server."""

    def __init__(self, *, page_size: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._page_size = page_size

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        capped = self._page_size if count is None else min(count, self._page_size)
        shifted = max(0, offset - 1)
        return super().expand(
            ecl,
            edition=edition,
            count=capped,
            offset=shifted,
            include_designations=include_designations,
            display_language=display_language,
            active_only=active_only,
        )


@pytest.mark.req("FR-99")
def test_overlapping_pages_do_not_duplicate_a_concept_tag() -> None:
    """The paging loop tolerates a server that overlaps pages by design (see
    ``_expand_chunk``'s own docstring) - this proves that tolerance doesn't
    leak into duplicated FR-99 findings when a code's page is fetched more
    than once."""
    codes = _codes(3)
    client = _OverlappingPageClient(
        page_size=2,
        concepts=[_procedure(code, fsn=f"Fixture {code} (regime/therapy)") for code in codes],
    )

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    assert {tag.code for tag in result.unexpected_semantic_tags} == set(codes)
    assert len(result.unexpected_semantic_tags) == 3


@pytest.mark.req("FR-99")
def test_a_concept_already_out_of_the_hierarchy_is_not_also_tag_warned() -> None:
    """It is out of the procedure hierarchy entirely - an error is already
    raised against it, and a warning about its tag is a symptom, not a second
    finding."""
    code = "100000001"
    client = _stub(_procedure(code, fsn="Fixture substance (substance)", parents=("105590001",)))

    result = TerminologySweep(client).run([code], edition=SNOMED_CT_AU)

    assert result.hierarchy_violations == (code,)
    assert result.unexpected_semantic_tags == ()


class _NoFsnDesignationClient(StubTerminologyClient):
    """Returns every concept from a normal expansion, but with ``target``'s
    designations stripped entirely - a server that returns the concept (so it
    correctly resolves and sits under the procedure hierarchy) but without a
    designation this client recognises as the FSN, e.g. one that doesn't
    honour ``includeDesignations``, or tags ``use`` non-standardly."""

    def __init__(self, *, target: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._target = target

    def expand(
        self,
        ecl: str,
        *,
        edition: Edition,
        count: int | None = None,
        offset: int = 0,
        include_designations: bool = False,
        display_language: str | None = None,
        active_only: bool | None = None,
    ) -> Expansion:
        expansion = super().expand(
            ecl,
            edition=edition,
            count=count,
            offset=offset,
            include_designations=include_designations,
            display_language=display_language,
            active_only=active_only,
        )
        concepts = tuple(
            ExpandedConcept(code=c.code, system=c.system, display=c.display, version=c.version)
            if c.code == self._target
            else c
            for c in expansion.concepts
        )
        return Expansion(
            concepts=concepts,
            total=expansion.total,
            offset=expansion.offset,
            resolved_versions=expansion.resolved_versions,
        )


@pytest.mark.req("FR-99")
def test_a_concept_whose_fsn_the_server_did_not_return_raises_no_tag_warning() -> None:
    """No tag observed is not evidence of a wrong tag - a server that returns
    a concept without a designation this client recognises as the FSN must
    not be treated as a tag violation.

    But the gap is not silent: it is exactly what ``unresolved_fsn_count``
    exists to surface, since a server that never returns an identifiable FSN
    for anything would otherwise make the FR-99 check pass permanently with
    no signal it never ran. Uses a properly registered, procedure-hierarchy
    concept (unlike a bare unregistered code, which this stub's own ECL
    evaluator would report as an FR-84 violation - out of scope here) so the
    unresolved-FSN path is exercised in isolation.
    """
    code = "100000001"
    client = _NoFsnDesignationClient(target=code, concepts=[_procedure(code)])

    result = TerminologySweep(client).run([code], edition=SNOMED_CT_AU)

    assert result.active == (code,)
    assert result.hierarchy_violations == ()
    assert result.unexpected_semantic_tags == ()
    assert result.unresolved_fsn_count == 1


@pytest.mark.req("FR-48")
def test_every_resolved_edition_version_the_server_reported_is_recorded() -> None:
    codes = _codes(2)
    client = _stub(_procedure(codes[0]), _procedure(codes[1]))

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert result.resolved_versions == (
        "http://snomed.info/sct/32506021000036107/version/20260531",
    )


@pytest.mark.req("FR-74")
def test_the_same_sweep_object_serves_both_editions() -> None:
    """FR-74: one validation engine, both editions - not a second
    implementation for the migration path."""
    codes = _codes(2)
    client = _stub(_procedure(codes[0]), _procedure(codes[1], editions=("au",)))
    client.seed_error(Operation.LOOKUP, _not_found(codes[1]), key=codes[1])
    sweep = TerminologySweep(client)

    au = sweep.run(codes, edition=SNOMED_CT_AU)
    international = sweep.run(codes, edition=SNOMED_CT_INTERNATIONAL)

    assert au.edition_label == "au"
    assert au.absent == ()
    assert international.edition_label == "int"
    assert international.absent == (codes[1],)


class _ConcurrencyProbe(StubTerminologyClient):
    """Records peak concurrent ``$lookup`` calls, and refuses to answer until
    ``parties`` of them are in flight at once.

    The barrier is what makes "concurrency actually happens" a deterministic
    assertion rather than a timing race: if the sweep serialised the second
    pass, the barrier would time out and the test would fail rather than
    quietly proving nothing.
    """

    def __init__(self, *, parties: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._barrier = threading.Barrier(parties, timeout=10)
        self._lock = threading.Lock()
        self._in_flight = 0
        self.peak_in_flight = 0

    def lookup(
        self,
        code: str,
        *,
        edition: Edition,
        properties: tuple[str, ...] = (),
        display_language: str | None = None,
    ) -> object:  # type: ignore[override]
        with self._lock:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            self._barrier.wait()
            return super().lookup(
                code, edition=edition, properties=properties, display_language=display_language
            )
        finally:
            with self._lock:
                self._in_flight -= 1


@pytest.mark.req("FR-52")
def test_the_delta_pass_is_concurrent_but_bounded_by_max_concurrency() -> None:
    """FR-52 step 3. Both halves matter: unbounded fan-out at a shared server
    is the discourtesy the requirement exists to prevent, and a serial pass
    is the slowness it exists to prevent."""
    codes = _codes(8)
    client = _ConcurrencyProbe(
        parties=4, concepts=[_procedure(code, active=False) for code in codes]
    )

    result = TerminologySweep(client, chunk_size=300, max_concurrency=4).run(
        codes, edition=SNOMED_CT_AU
    )

    assert result.inactive == codes
    assert client.peak_in_flight == 4


@pytest.mark.req("FR-52")
def test_a_max_concurrency_of_one_runs_the_delta_pass_in_the_calling_thread() -> None:
    """A serial sweep must not need a thread pool - and the exception from a
    failed lookup must surface with the caller's own stack, not a worker's."""
    codes = _codes(2)
    client = _stub(_procedure(codes[0], active=False), _procedure(codes[1], active=False))
    caller = threading.current_thread().name
    seen: list[str] = []

    class _RecordingSweep(TerminologySweep):
        def _lookup(self, code: str, *, edition: Edition) -> object:  # type: ignore[override]
            seen.append(threading.current_thread().name)
            return super()._lookup(code, edition=edition)

    _RecordingSweep(client, max_concurrency=1).run(codes, edition=SNOMED_CT_AU)

    assert seen == [caller, caller]


@pytest.mark.req("FR-52")
def test_a_429_during_a_sweep_honours_retry_after_and_completes() -> None:
    """FR-52 step 4, end to end. The backoff itself is OntoserverClient's and
    is tested there; what this asserts is that the sweep inherits it instead
    of turning a rate limit into a failed run."""
    codes = _codes(2)
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(
                200,
                json={
                    "resourceType": "ValueSet",
                    "expansion": {
                        "total": 2,
                        "contains": [
                            {"system": "http://snomed.info/sct", "code": code} for code in codes
                        ],
                    },
                },
            ),
            httpx.Response(
                200,
                json={"resourceType": "ValueSet", "expansion": {"total": 0, "contains": []}},
            ),
        ]
    )
    slept: list[float] = []
    client = OntoserverClient(
        TerminologyConfig(base_url="https://tx.example.test/fhir"),
        transport=httpx.MockTransport(lambda _request: next(responses)),
        sleep=slept.append,
    )

    with client:
        result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert slept == [7.0]
    assert result.active == codes
    assert result.absent == ()


# -- FR-97: SweepResult.designations -----------------------------------------


@pytest.mark.req("FR-97")
def test_designations_are_populated_from_the_bulk_expansion_at_no_extra_request_cost() -> None:
    code = "122192001"
    concept = StubConcept(
        code=code,
        fsn="Acanthamoeba culture (procedure)",
        preferred_terms={AU_LANGUAGE_TAG: "Acanthamoeba culture"},
        synonyms=("Acanthamoeba species culture",),
    )
    client = _stub(concept)

    result = TerminologySweep(client).run([code], edition=SNOMED_CT_AU)

    assert len(result.designations) == 1
    entry = result.designations[0]
    assert entry.code == code
    assert entry.fully_specified_name == "Acanthamoeba culture (procedure)"
    assert entry.display == "Acanthamoeba culture"
    assert set(entry.values) == {
        "Acanthamoeba culture (procedure)",
        "Acanthamoeba culture",
        "Acanthamoeba species culture",
    }
    assert _lookups(client) == ()


@pytest.mark.req("FR-97")
def test_designations_are_sorted_by_code() -> None:
    codes = _codes(3)
    client = _stub(*(_procedure(code) for code in reversed(codes)))

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert tuple(entry.code for entry in result.designations) == codes


@pytest.mark.req("FR-97")
def test_designations_are_deduplicated_across_overlapping_pages() -> None:
    """The paging loop tolerates a server that overlaps pages by design (see
    ``_expand_chunk``'s own docstring) - this proves that tolerance doesn't
    leak into a doubled designation set for FR-97's reconciliation."""
    codes = _codes(3)
    client = _OverlappingPageClient(page_size=2, concepts=[_procedure(code) for code in codes])

    result = TerminologySweep(client, chunk_size=300).run(codes, edition=SNOMED_CT_AU)

    assert tuple(entry.code for entry in result.designations) == codes


@pytest.mark.req("FR-97")
def test_a_delta_confirmed_active_code_still_gets_a_designations_entry() -> None:
    """A code the bulk expansion omitted (a server that under-reports a page,
    say) but the delta ``$lookup`` confirmed active is exactly as active as
    any code the bulk pass did return - ``designations`` must not silently
    drop it, or a caller (FR-97's reconciliation, FR-99's tag check) cannot
    tell "no designations projected" apart from "concept absent/inactive"
    for a code this same result reports active."""
    codes = _codes(2)
    client = _OmittingClient(omitted=codes[1], concepts=[_procedure(code) for code in codes])

    result = TerminologySweep(client).run(codes, edition=SNOMED_CT_AU)

    assert result.active == codes
    assert tuple(entry.code for entry in result.designations) == codes
    delta_entry = result.designations[1]
    assert delta_entry.fully_specified_name == f"Fixture concept {codes[1]} (procedure)"
    assert f"Fixture concept {codes[1]}" in delta_entry.values


@pytest.mark.req("FR-97")
def test_a_concept_out_of_the_hierarchy_still_has_a_designation_entry() -> None:
    """Unlike ``unexpected_semantic_tags``, FR-97's reconciliation must not
    exclude a hierarchy violation: a label defect on that cell survives the
    rebinding that would fix the hierarchy violation, so the two findings
    describe different remediations of different cells' worth of content."""
    code = "100000001"
    client = _stub(_procedure(code, fsn="Fixture substance (substance)", parents=("105590001",)))

    result = TerminologySweep(client).run([code], edition=SNOMED_CT_AU)

    assert result.hierarchy_violations == (code,)
    assert tuple(entry.code for entry in result.designations) == (code,)


@pytest.mark.req("FR-97")
def test_unresolved_fsn_count_is_unaffected_by_the_designations_projection() -> None:
    """Regression guard for folding ``_unexpected_tags`` onto the same
    ``ConceptDesignations`` projection FR-97 reads: the FR-99 count must not
    change, and the concept must still appear in ``designations`` with a
    ``None`` FSN rather than being silently dropped."""
    code = "100000001"
    client = _NoFsnDesignationClient(target=code, concepts=[_procedure(code)])

    result = TerminologySweep(client).run([code], edition=SNOMED_CT_AU)

    assert result.unresolved_fsn_count == 1
    assert len(result.designations) == 1
    assert result.designations[0].fully_specified_name is None


# -- FR-49: Edition.pinned_to preserves display_language ---------------------


@pytest.mark.req("FR-49")
def test_pinning_an_edition_preserves_its_display_language() -> None:
    """A silently dropped ``display_language`` on the pinned edition would
    make FR-49's reproduce-a-historical-run path quietly wrong on exactly the
    runs where reproducing past behaviour matters most."""
    pinned = SNOMED_CT_AU.pinned_to("20260531")
    assert pinned.display_language == SNOMED_CT_AU.display_language
    assert pinned.display_language == AU_LANGUAGE_TAG


# -- FR-97: TerminologySweep.confirm_labels ----------------------------------


@pytest.mark.req("FR-97")
def test_confirm_labels_issues_one_validate_code_per_unique_probe() -> None:
    code = "122192001"
    client = _stub(StubConcept(code=code, fsn="Acanthamoeba culture (procedure)"))
    client.seed_validate_code(
        code, ValidationResult(code=code, result=True, display="Acanthamoeba culture")
    )

    confirmations = TerminologySweep(client).confirm_labels(
        [(code, "Acanthamoeba culture"), (code, "Acanthamoeba culture")], edition=SNOMED_CT_AU
    )

    validate_code_requests = [
        r for r in client.requests if r.operation is Operation.CODE_SYSTEM_VALIDATE_CODE
    ]
    assert len(validate_code_requests) == 1
    assert confirmations == (
        LabelConfirmation(code=code, display="Acanthamoeba culture", matched=True, message=None),
    )


@pytest.mark.req("FR-97")
def test_confirm_labels_reports_a_server_rejection_with_its_message() -> None:
    code = "122192001"
    client = _stub(StubConcept(code=code, fsn="Acanthamoeba culture (procedure)"))

    confirmations = TerminologySweep(client).confirm_labels(
        [(code, "Acanthamoeba species culture")], edition=SNOMED_CT_AU
    )

    assert len(confirmations) == 1
    assert confirmations[0].matched is False
    assert confirmations[0].message is not None


@pytest.mark.req("FR-97")
def test_confirm_labels_propagates_a_server_failure_rather_than_treating_it_as_absence() -> None:
    """Unlike ``_lookup``, a probe is only ever issued for a code this sweep
    just resolved as active - so a failure here is a contradiction with the
    status pass, not an answer, and must abort the run (FR-54) rather than
    being folded into a "no match" outcome."""
    code = "122192001"
    client = _stub(StubConcept(code=code, fsn="Acanthamoeba culture (procedure)"))
    client.seed_error(Operation.CODE_SYSTEM_VALIDATE_CODE, _not_found(code), key=code)

    with pytest.raises(TerminologyStatusError):
        TerminologySweep(client).confirm_labels([(code, "Anything")], edition=SNOMED_CT_AU)


@pytest.mark.req("FR-97")
def test_confirm_labels_of_an_empty_sequence_issues_no_requests() -> None:
    client = _stub()
    assert TerminologySweep(client).confirm_labels([], edition=SNOMED_CT_AU) == ()
    assert client.requests == ()


# -- FR-75: codes_without_attribute / codes_with_attribute_value / describe --


def _specimen_bound(code: str, *, specimen: str, **kwargs: object) -> StubConcept:
    return StubConcept(
        code=code,
        fsn=kwargs.pop("fsn", f"Fixture concept {code} (procedure)"),  # type: ignore[arg-type]
        properties=(
            ConceptProperty(code=HAS_SPECIMEN_ATTRIBUTE, value=specimen, value_type="code"),
        ),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.req("FR-75")
def test_codes_without_attribute_reports_only_codes_with_no_value_at_all() -> None:
    modelled = _specimen_bound("100000001", specimen="122575003")
    unmodelled = _procedure("100000002")
    client = _stub(modelled, unmodelled)

    result = TerminologySweep(client).codes_without_attribute(
        ["100000001", "100000002"], attribute=HAS_SPECIMEN_ATTRIBUTE, edition=SNOMED_CT_AU
    )

    assert result == ("100000002",)


@pytest.mark.req("FR-75")
def test_codes_without_attribute_chunks_the_same_way_the_hierarchy_check_does() -> None:
    codes = _codes(5)
    client = _stub(*(_procedure(code) for code in codes))

    TerminologySweep(client, chunk_size=2).codes_without_attribute(
        codes, attribute=HAS_SPECIMEN_ATTRIBUTE, edition=SNOMED_CT_AU
    )

    minus_expansions = [
        r.detail for r in client.requests if r.operation is Operation.EXPAND and "MINUS" in r.detail
    ]
    assert len(minus_expansions) == 3  # ceil(5 / 2)


@pytest.mark.req("FR-75")
def test_codes_with_attribute_value_reports_codes_whose_value_is_subsumed_by_root() -> None:
    agrees = _specimen_bound("100000001", specimen="122575003")
    differs = _specimen_bound("100000002", specimen="119364003")  # serum, not urine
    client = _stub(agrees, differs)

    result = TerminologySweep(client).codes_with_attribute_value(
        ["100000001", "100000002"],
        attribute=HAS_SPECIMEN_ATTRIBUTE,
        root="122575003",
        edition=SNOMED_CT_AU,
    )

    assert result == ("100000001",)


@pytest.mark.req("FR-75")
def test_codes_with_attribute_value_closure_catches_a_descendant_specimen_value() -> None:
    """The ``<<`` on the value side, not just around the whole refinement -
    a code whose value is a *descendant* of ``root`` still agrees."""
    descendant_value = _procedure("122575099", parents=("122575003",))
    bound = _specimen_bound("100000001", specimen="122575099")
    client = _stub(descendant_value, bound)

    result = TerminologySweep(client).codes_with_attribute_value(
        ["100000001"], attribute=HAS_SPECIMEN_ATTRIBUTE, root="122575003", edition=SNOMED_CT_AU
    )

    assert result == ("100000001",)


@pytest.mark.req("FR-75")
def test_describe_resolves_every_code_directly_not_through_a_hierarchy_expression() -> None:
    concept = StubConcept(
        code="122575003", fsn="Urine specimen (specimen)", synonyms=("Urine sample",)
    )
    client = _stub(concept)

    result = TerminologySweep(client).describe(["122575003"], edition=SNOMED_CT_AU)

    assert len(result) == 1
    assert result[0].code == "122575003"
    assert result[0].fully_specified_name == "Urine specimen (specimen)"
    assert "Urine sample" in result[0].values
    assert _hierarchy_expansions(client) == ()  # never MINUS <<71388002


@pytest.mark.req("FR-75")
def test_describe_of_an_empty_sequence_issues_no_requests() -> None:
    client = _stub()
    assert TerminologySweep(client).describe([], edition=SNOMED_CT_AU) == ()
    assert client.requests == ()


@pytest.mark.req("FR-75")
def test_describe_chunks_the_specimen_table_the_same_way_status_resolution_does() -> None:
    codes = _codes(5)
    client = _stub(*(_procedure(code) for code in codes))

    TerminologySweep(client, chunk_size=2).describe(codes, edition=SNOMED_CT_AU)

    assert len(_expansions(client)) == 3  # ceil(5 / 2), plain $expand, no MINUS/AND


@pytest.mark.req("FR-75")
def test_describe_never_runs_the_fr84_hierarchy_check_or_fr99_tag_check() -> None:
    """Deliberate: these are specimen concepts, not procedures - ``run()``'s
    FR-84/FR-99 checks would misfire on every one of them."""
    concept = StubConcept(code="119339001", fsn="Stool specimen (specimen)")
    client = _stub(concept)

    TerminologySweep(client).describe(["119339001"], edition=SNOMED_CT_AU)

    assert _hierarchy_expansions(client) == ()
    assert len(client.requests) == 1
