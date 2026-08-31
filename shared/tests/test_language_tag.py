"""Tests for BCP-47 language tag well-formedness (FR-04, issue #47).

Offline, no network access (NFR-37) - this is a pure regex check with no
external registry lookup at all.
"""

from __future__ import annotations

import pytest

from nptc_shared.language import (
    DEFAULT_LANGUAGE,
    canonicalize_language_tag,
    is_well_formed_language_tag,
)

WELL_FORMED = (
    "en",
    "en-AU",
    "mi-NZ",
    "zh-Hans-CN",
    "fr-CA",
    "en-au",  # casing is not required to be canonical
)

MALFORMED = (
    "",
    "   ",
    "-",
    "en-",
    "-AU",
    "en--AU",  # doubled hyphen produces an empty subtag
    "e",  # single-character primary subtag
    "en_AU",  # underscore, not a hyphen
    "en AU",  # space, not a hyphen
)


@pytest.mark.parametrize("tag", WELL_FORMED)
def test_well_formed_tags_are_accepted(tag: str) -> None:
    assert is_well_formed_language_tag(tag)


@pytest.mark.parametrize("tag", MALFORMED)
def test_malformed_tags_are_rejected(tag: str) -> None:
    assert not is_well_formed_language_tag(tag)


def test_default_language_is_well_formed() -> None:
    """The catalogue's own default (PRD §6.3) must itself pass the check it is
    mirrored against - a default that failed its own validator would be a
    silent contradiction between the constant and the column CHECK it backs."""
    assert is_well_formed_language_tag(DEFAULT_LANGUAGE)
    assert DEFAULT_LANGUAGE == "en-AU"


@pytest.mark.parametrize(
    ("tag", "canonical"),
    [
        ("en", "en"),
        ("en-AU", "en-AU"),
        ("en-au", "en-AU"),
        ("EN-AU", "en-AU"),
        ("mi-nz", "mi-NZ"),
        ("zh-hans-cn", "zh-Hans-CN"),
        ("zh-Hans-CN", "zh-Hans-CN"),
        ("fr-ca", "fr-CA"),
    ],
)
def test_canonicalize_folds_to_bcp47_canonical_casing(tag: str, canonical: str) -> None:
    """`en-au` and `en-AU` must resolve to the one stored/compared form -
    every string-equality check this catalogue makes against a language tag
    (`DEFAULT_LANGUAGE`, the designation partial unique indexes,
    `ck_designation_no_en_au_preferred`) relies on this having already run
    once, at the write boundary (issue #224 review finding 2)."""
    assert canonicalize_language_tag(tag) == canonical


def test_canonicalize_is_idempotent() -> None:
    assert canonicalize_language_tag(canonicalize_language_tag("en-au")) == "en-AU"
