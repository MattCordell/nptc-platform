"""FR-89: the specimen root means "any specimen" and stands alone (ADR-0044). The rule is a pure
function of the submitted values, so these tests need no database."""

from __future__ import annotations

from typing import Any

import pytest

from nptc.catalogue.property_values import _validate_specimen_root_alone as validate

_ROOT = {"system": "http://snomed.info/sct", "code": "123038009"}
_SERUM = {"system": "http://snomed.info/sct", "code": "119364003"}


@pytest.mark.req("FR-89")
def test_the_root_alone_is_accepted() -> None:
    assert validate("specimen", [_ROOT]) == ()


@pytest.mark.req("FR-89")
def test_the_root_twice_is_not_a_conflict_with_itself() -> None:
    assert validate("specimen", [_ROOT, _ROOT]) == ()


@pytest.mark.req("FR-89")
def test_named_specimens_alone_are_accepted() -> None:
    assert validate("specimen", [_SERUM]) == ()


@pytest.mark.req("FR-89")
def test_no_values_are_accepted() -> None:
    assert validate("specimen", []) == ()


@pytest.mark.req("FR-89")
@pytest.mark.parametrize("values", [[_ROOT, _SERUM], [_SERUM, _ROOT], [_SERUM, _ROOT, _SERUM]])
def test_the_root_beside_a_named_specimen_is_one_issue_on_the_roots_ordinal(
    values: list[dict[str, Any]],
) -> None:
    (issue,) = validate("specimen", values)

    assert issue.code == "specimen-root-conflict"
    assert issue.property_key == "specimen"
    assert issue.ordinal == values.index(_ROOT)


@pytest.mark.req("FR-89")
def test_the_rule_applies_to_the_specimen_property_only() -> None:
    assert validate("discipline", [_ROOT, _SERUM]) == ()


@pytest.mark.req("FR-89")
def test_a_value_that_is_not_an_object_is_left_to_the_schema_check() -> None:
    assert validate("specimen", [_ROOT, "not an object", None]) == ()
