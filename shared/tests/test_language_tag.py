"""Tests for BCP-47 language tag well-formedness (FR-04, issue #47).

Offline, no network access (NFR-37) - this is a pure regex check with no
external registry lookup at all.
"""

from __future__ import annotations

import pytest

from nptc_shared.language import DEFAULT_LANGUAGE, is_well_formed_language_tag

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
