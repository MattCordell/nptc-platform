"""Tests for the pure SNOMED CT URI/ECL builders (FR-48, FR-49, FR-84)."""

from __future__ import annotations

import pytest

from nptc_shared.terminology.models import SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL
from nptc_shared.terminology.snomed import (
    ecl_set_of,
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
