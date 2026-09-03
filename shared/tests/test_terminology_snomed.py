"""Tests for the pure SNOMED CT URI/ECL builders (FR-48, FR-49, FR-84)."""

from __future__ import annotations

import pytest

from nptc_shared.terminology.models import SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL
from nptc_shared.terminology.snomed import (
    ecl_from_implicit_value_set_url,
    ecl_set_of,
    edition_from_implicit_value_set_url,
    implicit_value_set_url,
    semantic_tag,
    strip_semantic_tag,
)


def test_implicit_value_set_url_unpinned_edition_has_no_version_segment() -> None:
    url = implicit_value_set_url("122192001", SNOMED_CT_AU)
    assert url == "http://snomed.info/sct/32506021000036107?fhir_vs=ecl/122192001"


def test_implicit_value_set_url_pinned_edition_includes_the_version_segment() -> None:
    pinned = SNOMED_CT_AU.pinned_to("20260531")
    url = implicit_value_set_url("122192001", pinned)
    assert url == (
        "http://snomed.info/sct/32506021000036107/version/20260531?fhir_vs=ecl/122192001"
    )


def test_implicit_value_set_url_percent_encodes_the_ecl_exactly_once() -> None:
    url = implicit_value_set_url("<<71388002", SNOMED_CT_INTERNATIONAL)
    assert "ecl/%3C%3C71388002" in url
    assert "<<" not in url


@pytest.mark.req("FR-10")
def test_ecl_from_implicit_value_set_url_round_trips_with_the_builder() -> None:
    for ecl, edition in (
        ("122192001", SNOMED_CT_AU),
        ("<<71388002", SNOMED_CT_INTERNATIONAL),
        ("(122192001 OR 71388002) MINUS <<71388002", SNOMED_CT_AU.pinned_to("20260531")),
    ):
        assert ecl_from_implicit_value_set_url(implicit_value_set_url(ecl, edition)) == ecl


def test_ecl_from_implicit_value_set_url_rejects_a_non_ecl_implicit_value_set() -> None:
    with pytest.raises(ValueError, match="not a SNOMED implicit ECL value set URI"):
        ecl_from_implicit_value_set_url(
            "http://snomed.info/sct/32506021000036107?fhir_vs=isa/71388002"
        )


def test_ecl_from_implicit_value_set_url_rejects_a_uri_with_no_fhir_vs_parameter() -> None:
    with pytest.raises(ValueError, match="not a SNOMED implicit ECL value set URI"):
        ecl_from_implicit_value_set_url("http://snomed.info/sct/32506021000036107")


@pytest.mark.req("FR-10")
def test_edition_from_implicit_value_set_url_round_trips_with_the_builder() -> None:
    """`implicit_value_set_url` always writes a module-qualified base
    (`Edition.system_version_uri`), so `label` is never needed to resolve
    one of its own outputs - the URI alone is already authoritative,
    including any pinned version."""
    for ecl, edition in (
        ("122192001", SNOMED_CT_AU),
        ("<<71388002", SNOMED_CT_INTERNATIONAL),
        ("(122192001 OR 71388002) MINUS <<71388002", SNOMED_CT_AU.pinned_to("20260531")),
    ):
        assert edition_from_implicit_value_set_url(implicit_value_set_url(ecl, edition)) == edition


@pytest.mark.req("FR-10")
def test_edition_from_implicit_value_set_url_recovers_display_language() -> None:
    """The whole reason this function exists rather than reconstructing an
    `Edition` from a label: `display_language` (FR-82) isn't encoded in the
    URI at all, so it has to come from matching `known_editions`, not from
    a caller-supplied default."""
    url = implicit_value_set_url("122192001", SNOMED_CT_AU)
    assert (
        edition_from_implicit_value_set_url(url).display_language == SNOMED_CT_AU.display_language
    )


@pytest.mark.req("FR-10")
def test_edition_from_implicit_value_set_url_resolves_the_bare_system_shape_by_label() -> None:
    """PRD S6.6's own worked example (line 417) and `nptc.db.bootstrap`'s
    real seeded `specimen` binding both store `value_set_uri` in this bare,
    module-less form - the edition isn't in the URI at all here, so `label`
    (the property's own stored `binding.edition`) is what resolves it."""
    bare = "http://snomed.info/sct?fhir_vs=ecl/%3C123038009"
    assert edition_from_implicit_value_set_url(bare, label="au") == SNOMED_CT_AU
    assert edition_from_implicit_value_set_url(bare, label="int") == SNOMED_CT_INTERNATIONAL


def test_edition_from_implicit_value_set_url_rejects_a_bare_system_uri_with_no_label() -> None:
    with pytest.raises(ValueError, match="does not match a recognised edition"):
        edition_from_implicit_value_set_url("http://snomed.info/sct?fhir_vs=ecl/%3C123038009")


def test_edition_from_implicit_value_set_url_rejects_a_bare_system_uri_with_an_unknown_label() -> (
    None
):
    """The defect issue #247's review found: a label-only reconstruction
    (`Edition(module_id=label, label=label)`) would have silently
    fabricated a nonsense `Edition` for any label at all, instead of
    raising for one that names no real edition."""
    with pytest.raises(ValueError, match="does not match a recognised edition"):
        edition_from_implicit_value_set_url(
            "http://snomed.info/sct?fhir_vs=ecl/%3C123038009", label="not-a-real-edition"
        )


def test_edition_from_implicit_value_set_url_rejects_a_non_ecl_implicit_value_set() -> None:
    with pytest.raises(ValueError, match="not a SNOMED implicit ECL value set URI"):
        edition_from_implicit_value_set_url(
            "http://snomed.info/sct/32506021000036107?fhir_vs=isa/71388002"
        )


def test_edition_from_implicit_value_set_url_rejects_an_unrecognised_module_id() -> None:
    with pytest.raises(ValueError, match="unrecognised SNOMED module id"):
        edition_from_implicit_value_set_url(
            "http://snomed.info/sct/99999999999999999?fhir_vs=ecl/122192001"
        )


def test_ecl_set_of_joins_codes_with_or() -> None:
    assert ecl_set_of(["122192001", "71388002"]) == "122192001 OR 71388002"


def test_ecl_set_of_rejects_an_empty_iterable() -> None:
    with pytest.raises(ValueError, match="at least one code"):
        ecl_set_of([])


def test_ecl_set_of_rejects_a_code_that_is_not_a_valid_sctid_format() -> None:
    with pytest.raises(ValueError, match="not a valid SCTID"):
        ecl_set_of(["122192001", "not-a-code"])


def test_ecl_set_of_never_emits_the_placeholder_less_than_notation() -> None:
    """FR-84's PRD idiom writes ``<code1>`` as placeholder notation, not
    ECL's descendant-of operator - this builder must never emit a literal
    ``<`` for a plain code, or a hierarchy check built from it would
    silently ask for descendants instead of the codes themselves."""
    result = ecl_set_of(["122192001", "71388002", "243120004"])
    assert "<" not in result


@pytest.mark.req("FR-99")
def test_semantic_tag_reads_the_final_parenthesised_group() -> None:
    assert semantic_tag("Acanthamoeba culture (procedure)") == "procedure"
    assert semantic_tag("Regime/therapy (regime/therapy)") == "regime/therapy"


@pytest.mark.req("FR-99")
def test_semantic_tag_of_an_fsn_with_an_internal_group_takes_only_the_last() -> None:
    """PRD FR-83's own example: the tag is the *final* group, and an FSN can
    carry a parenthesised phrase in its body ("Microscopy (acid fast
    bacilli)") that is part of the term, not the tag."""
    assert semantic_tag("Microscopy (acid fast bacilli) (procedure)") == "procedure"


@pytest.mark.req("FR-99")
def test_semantic_tag_is_none_when_there_is_no_trailing_group() -> None:
    """The SPIA workbook's "Fully Specified Name" column carries no tags at
    all (Appendix A.8). A caller acting on FR-99 must be able to tell "no tag
    served" from "a tag that is not (procedure)" - reporting the former as a
    warning would flag every row in the source."""
    assert semantic_tag("Adenovirus nucleic acid detection") is None
    assert semantic_tag("Trailing group is empty ()") is None
    assert semantic_tag("(procedure) leading only") is None


@pytest.mark.req("FR-97")
def test_strip_semantic_tag_removes_the_final_group_exactly_once() -> None:
    assert strip_semantic_tag("Acanthamoeba culture (procedure)") == "Acanthamoeba culture"


@pytest.mark.req("FR-97")
def test_strip_semantic_tag_leaves_an_internal_parenthesised_phrase_intact() -> None:
    """PRD Appendix A.10 row 29's caution: 391483001's FSN legitimately ends
    in a parenthesised phrase that is part of the term, not the tag - the
    correctly stripped label is "Microscopy (acid fast bacilli)", not
    "Microscopy"."""
    assert (
        strip_semantic_tag("Microscopy (acid fast bacilli) (procedure)")
        == "Microscopy (acid fast bacilli)"
    )


@pytest.mark.req("FR-97")
def test_strip_semantic_tag_returns_the_input_unchanged_when_there_is_no_trailing_group() -> None:
    """The SPIA workbook's "Fully Specified Name" column carries no tags at
    all (Appendix A.8) - stripping a value that was never a served FSN must
    not raise or silently mutate it, since the caller falls through to
    comparing the untouched value against the concept's raw designation set."""
    assert (
        strip_semantic_tag("Adenovirus nucleic acid detection")
        == "Adenovirus nucleic acid detection"
    )
