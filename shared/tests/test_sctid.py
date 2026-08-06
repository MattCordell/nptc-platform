"""Tests for the SCTID format and Verhoeff check-digit library (FR-06, FR-74).

The three "known-good" codes are real Australian-extension SCTIDs quoted in the
PRD (15, 16 and 18 digits respectively) - using real codes, rather than invented
ones, is how this test suite confirms the algorithm actually matches SNOMED CT's
check-digit scheme rather than merely being self-consistent.
"""

from __future__ import annotations

import pytest

from nptc_shared.sctid import SCTID, InvalidSCTIDError, has_valid_check_digit, has_valid_format

REAL_SCTIDS = (
    "873871000168106",  # 15 digits
    "1393151000168101",  # 16 digits
    "933434771000036107",  # 18 digits
)


def _to_unicode_digits(ascii_digits: str, zero_codepoint: int) -> str:
    """Transliterates an ASCII digit string into another Unicode digit script.

    Built via ``chr()`` arithmetic, not literal non-ASCII characters, per this
    repo's convention (see ``shared/tests/test_text.py``) - and it sidesteps
    ruff's RUF001 ambiguous-character check on a source literal.
    """
    return "".join(chr(zero_codepoint + int(d)) for d in ascii_digits)


ARABIC_INDIC_ZERO = 0x0660
FULLWIDTH_DIGIT_ZERO = 0xFF10


@pytest.mark.req("FR-06")
@pytest.mark.parametrize("value", REAL_SCTIDS)
def test_real_sctids_pass_format_and_check_digit(value: str) -> None:
    assert has_valid_format(value) is True
    assert has_valid_check_digit(value) is True


@pytest.mark.req("FR-06")
@pytest.mark.parametrize("value", REAL_SCTIDS)
def test_real_sctids_with_corrupted_check_digit_are_rejected(value: str) -> None:
    correct_digit = value[-1]
    corrupted_variants = [value[:-1] + str(d) for d in range(10) if str(d) != correct_digit]
    for corrupted in corrupted_variants:
        assert has_valid_check_digit(corrupted) is False


@pytest.mark.req("FR-06")
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123456", True),  # 6 digits: minimum length
        ("12345", False),  # 5 digits: below minimum
        ("123456789012345678", True),  # 18 digits: maximum length
        ("1234567890123456789", False),  # 19 digits: above maximum
        ("12345a", False),  # non-digit character
        ("", False),  # empty string
    ],
)
def test_has_valid_format_enforces_six_to_eighteen_digits(value: str, expected: bool) -> None:
    assert has_valid_format(value) is expected


@pytest.mark.req("FR-06")
def test_has_valid_format_rejects_non_ascii_unicode_digits() -> None:
    # `\d` in a Python str regex matches all Unicode decimal digits, not just
    # [0-9] - these would otherwise be declared valid here and rejected by the
    # Postgres ^[0-9]{6,18}$ check constraint FR-06 mandates.
    ascii_sctid = "873871000168106"
    assert has_valid_format(_to_unicode_digits(ascii_sctid, ARABIC_INDIC_ZERO)) is False
    assert has_valid_format(_to_unicode_digits(ascii_sctid, FULLWIDTH_DIGIT_ZERO)) is False


@pytest.mark.req("FR-06")
@pytest.mark.parametrize(
    "value",
    [
        "12345a",  # non-digit character: would raise ValueError from int(char) unguarded
        "12345",  # below minimum length
        "",  # empty: unguarded, checksum 0 would wrongly read as valid
    ],
)
def test_has_valid_check_digit_is_total_and_rejects_malformed_input(value: str) -> None:
    assert has_valid_check_digit(value) is False


@pytest.mark.req("FR-06")
def test_seventeen_and_eighteen_digit_synthetic_sctids_validate() -> None:
    # Verhoeff checksum of 0 confirmed by direct computation, not guessed - these
    # are the acceptance-criteria lengths named in issue #32.
    seventeen_digit = "12345678912345674"
    eighteen_digit = "123456789123456784"
    assert has_valid_format(seventeen_digit) is True
    assert has_valid_check_digit(seventeen_digit) is True
    assert has_valid_format(eighteen_digit) is True
    assert has_valid_check_digit(eighteen_digit) is True
    assert SCTID(seventeen_digit).value == seventeen_digit
    assert SCTID(eighteen_digit).value == eighteen_digit


@pytest.mark.req("FR-06")
def test_sctid_construction_accepts_a_valid_string() -> None:
    sctid = SCTID("873871000168106")
    assert sctid.value == "873871000168106"
    assert str(sctid) == "873871000168106"


@pytest.mark.req("FR-06")
def test_sctid_construction_rejects_bad_format() -> None:
    with pytest.raises(InvalidSCTIDError):
        SCTID("12345")  # too short


@pytest.mark.req("FR-06")
def test_sctid_construction_rejects_bad_check_digit() -> None:
    with pytest.raises(InvalidSCTIDError):
        SCTID("873871000168100")  # correct except for the last digit


@pytest.mark.req("FR-06")
def test_sctid_never_accepts_a_numeric_type() -> None:
    with pytest.raises(TypeError):
        SCTID(873871000168106)  # type: ignore[arg-type]


@pytest.mark.req("FR-06")
def test_sctid_defines_no_int_coercion() -> None:
    sctid = SCTID("873871000168106")
    with pytest.raises(TypeError):
        int(sctid)  # type: ignore[call-overload]
