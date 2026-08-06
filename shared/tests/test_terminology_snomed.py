"""Tests for the pure SNOMED CT URI/ECL builders (FR-48, FR-49, FR-84)."""

from __future__ import annotations

import pytest

from nptc_shared.terminology.models import SNOMED_CT_AU, SNOMED_CT_INTERNATIONAL
from nptc_shared.terminology.snomed import ecl_set_of, implicit_value_set_url


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
