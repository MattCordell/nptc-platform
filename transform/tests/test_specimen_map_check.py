"""The specimen map's code check against the terminology server (FR-88, ADR-0044)."""

from __future__ import annotations

import pytest

from nptc_shared.terminology.models import AU_LANGUAGE_TAG, SNOMED_CT_AU, Operation
from nptc_shared.terminology.stub import StubConcept, StubTerminologyClient
from nptc_shared.terminology.sweep import ConceptDesignations, TerminologySweep
from nptc_transform.bands import Band
from nptc_transform.specimen_map import SPECIMEN_MAP, parse_specimen_map
from nptc_transform.specimen_map_check import check_specimen_map

_AU_VERSION = "http://snomed.info/sct/32506021000036107/version/20260531"
_HEADER = (
    "Source code\tSource display\tTarget code\tTarget display\tRelationship type code\t"
    "Relationship type display\tNo map flag\tStatus"
)


_URINE_ROW = (
    "\nh\tUrine\t122575003\tUrine specimen (specimen)\tTARGET_EQUIVALENT\tn\tfalse\tMAPPED\n"
)


def _client(*concepts: StubConcept) -> StubTerminologyClient:
    return StubTerminologyClient(concepts=concepts, resolved_version={"au": _AU_VERSION})


def _expansions(client: StubTerminologyClient) -> list[str]:
    return [r.detail for r in client.requests if r.operation is Operation.EXPAND]


@pytest.mark.req("FR-88")
def test_every_packaged_code_passes_when_all_sit_under_the_specimen_root(
    specimen_map_concepts: tuple[StubConcept, ...],
) -> None:
    client = _client(*specimen_map_concepts)

    outcome = check_specimen_map(TerminologySweep(client))

    assert outcome.findings == ()
    assert outcome.run.codes_checked == len(SPECIMEN_MAP.codes)
    assert outcome.run.resolved_versions == (_AU_VERSION,)


@pytest.mark.req("FR-88")
def test_the_whole_map_is_checked_in_one_request_and_its_terms_read_in_another(
    specimen_map_concepts: tuple[StubConcept, ...],
) -> None:
    client = _client(*specimen_map_concepts)

    check_specimen_map(TerminologySweep(client))

    root_check, preferred_terms = _expansions(client)
    assert " AND <<123038009" in root_check
    assert "<<" not in preferred_terms
    assert [r.display_language for r in client.requests if r.operation is Operation.EXPAND][
        1
    ] == AU_LANGUAGE_TAG


@pytest.mark.req("FR-88")
def test_each_passing_code_carries_its_au_preferred_term_not_its_fsn() -> None:
    parsed = parse_specimen_map(_HEADER + _URINE_ROW)
    client = _client(
        StubConcept(
            code="122575003",
            fsn="Urine specimen (specimen)",
            preferred_terms={AU_LANGUAGE_TAG: "Urine specimen"},
            parents=("123038009",),
        )
    )

    outcome = check_specimen_map(TerminologySweep(client), parsed)

    assert outcome.run.preferred_terms == (("122575003", "Urine specimen"),)


class _NoTermSweep(TerminologySweep):
    """A sweep whose server serves no AU preferred term for any concept."""

    def describe(  # type: ignore[override]
        self, codes: object, *, edition: object, versions: set[str] | None = None
    ) -> tuple[ConceptDesignations, ...]:
        assert edition is SNOMED_CT_AU
        return tuple(
            ConceptDesignations(code=code, fully_specified_name=None, display=None)
            for code in codes  # type: ignore[attr-defined]
        )


@pytest.mark.req("FR-88")
def test_a_code_with_no_served_preferred_term_blocks_rather_than_seed_a_blank_display() -> None:
    parsed = parse_specimen_map(_HEADER + _URINE_ROW)
    client = _client(
        StubConcept(code="122575003", fsn="Urine specimen (specimen)", parents=("123038009",))
    )

    outcome = check_specimen_map(_NoTermSweep(client), parsed)

    (finding,) = outcome.findings
    assert finding.code == "SPECIMEN_MAP_NO_PREFERRED_TERM"
    assert finding.band is Band.DATA_DEFECT
    assert str(finding.location) == "specimen_map.tsv!C2"
    assert outcome.run.preferred_terms == ()


@pytest.mark.req("FR-88")
def test_a_code_outside_the_specimen_hierarchy_blocks_and_points_at_its_map_row(
    specimen_map_concepts: tuple[StubConcept, ...],
) -> None:
    serum = SPECIMEN_MAP.resolve("Serum")
    assert serum is not None and serum.code is not None
    moved = StubConcept(code=serum.code, fsn="Serum specimen (specimen)", parents=("71388002",))
    others = tuple(c for c in specimen_map_concepts if c.code != serum.code)

    outcome = check_specimen_map(TerminologySweep(_client(moved, *others)))

    (finding,) = outcome.findings
    assert finding.code == "SPECIMEN_MAP_CODE_OUT_OF_SCOPE"
    assert finding.band is Band.DATA_DEFECT
    assert str(finding.location) == f"specimen_map.tsv!C{serum.line}"
    assert "'Serum'" in finding.message and serum.code in finding.message


@pytest.mark.req("FR-88")
def test_a_code_the_server_does_not_have_blocks_like_one_outside_the_hierarchy(
    specimen_map_concepts: tuple[StubConcept, ...],
) -> None:
    """An ECL that enumerates an absent code returns nothing for it, so a check
    that only looked for codes *outside* the root would pass an invented code."""
    urine = SPECIMEN_MAP.resolve("Urine")
    assert urine is not None and urine.code is not None
    others = tuple(c for c in specimen_map_concepts if c.code != urine.code)

    outcome = check_specimen_map(TerminologySweep(_client(*others)))

    assert [f.message for f in outcome.findings if "'Urine'" in f.message]


@pytest.mark.req("FR-88")
def test_every_string_that_shares_a_failing_code_gets_its_own_finding(
    specimen_map_concepts: tuple[StubConcept, ...],
) -> None:
    fluid = SPECIMEN_MAP.resolve("Fluid")
    assert fluid is not None and fluid.code is not None
    others = tuple(c for c in specimen_map_concepts if c.code != fluid.code)

    outcome = check_specimen_map(TerminologySweep(_client(*others)))

    messages = " ".join(f.message for f in outcome.findings)
    assert "'Fluid'" in messages and "'Fluids'" in messages and "'Body fluid'" in messages


@pytest.mark.req("FR-88")
def test_a_no_map_row_is_never_checked() -> None:
    parsed = parse_specimen_map(
        _HEADER + "\nh\tN/A\t\t\t\t\ttrue\tMAPPED\n" + "h\tUrine\t122575003\tUrine specimen\t"
        "TARGET_EQUIVALENT\tn\tfalse\tMAPPED\n"
    )
    client = _client(
        StubConcept(code="122575003", fsn="Urine specimen (specimen)", parents=("123038009",))
    )

    outcome = check_specimen_map(TerminologySweep(client), parsed)

    assert outcome.findings == ()
    assert outcome.run.codes_checked == 1


@pytest.mark.req("FR-88")
def test_the_specimen_root_itself_passes() -> None:
    parsed = parse_specimen_map(
        _HEADER + "\nh\tAny\t123038009\tSpecimen (specimen)\tTARGET_INEXACT\tn\tfalse\tMAPPED\n"
    )

    outcome = check_specimen_map(
        TerminologySweep(_client(StubConcept(code="123038009", fsn="Specimen (specimen)"))), parsed
    )

    assert outcome.findings == ()
