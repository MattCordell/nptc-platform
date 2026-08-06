"""SNOMED CT identifier validation: format and Verhoeff check digit (FR-06, FR-74).

Written once here so the transform's import-time validation and the backend's
entry-time validation can never diverge on what counts as a valid SCTID (ADR-0001).
The PRD's own sample data (Appendix, Australian extension identifiers) shows why
this must never touch a numeric type: a SNOMED CT identifier of 16+ digits held in
a spreadsheet cell formatted as a number is silently corrupted by Excel's
15-significant-digit limit. ``SCTID`` is therefore a string-only wrapper that
deliberately implements no ``__int__``/``__index__`` - passing one where a number
is expected must fail loudly, never coerce silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCTID_FORMAT = re.compile(r"^[0-9]{6,18}$")

# Verhoeff dihedral-group tables (D5), standard for SNOMED CT identifiers: `_D` is
# the multiplication table and `_P` permutes each digit by its position from the
# right (mod 8). Generating a check digit needs an inverse table too, but that's
# out of scope - this library only validates existing SCTIDs.
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


class InvalidSCTIDError(ValueError):
    """Raised when a candidate string fails SCTID format or check-digit validation."""


def has_valid_format(value: str) -> bool:
    """True if ``value`` matches ``^\\d{6,18}$`` (FR-06)."""
    return _SCTID_FORMAT.fullmatch(value) is not None


def has_valid_check_digit(value: str) -> bool:
    """True if the Verhoeff checksum over ``value``'s digits reduces to zero.

    Total over any ``str``: a ``value`` that isn't a well-formed SCTID (wrong
    length, non-digit characters, empty) is simply not a valid check digit
    either, rather than raising - callers can use this standalone without
    calling ``has_valid_format`` first.

    Verhoeff is computed right-to-left, so position ``i`` (0-based, from the
    right) selects the permutation row ``_P[i % 8]`` - this is the standard
    algorithm shape, not an SCTID-specific variant.
    """
    if not has_valid_format(value):
        return False
    checksum = 0
    for position, char in enumerate(reversed(value)):
        checksum = _D[checksum][_P[position % 8][int(char)]]
    return checksum == 0


@dataclass(frozen=True, slots=True)
class SCTID:
    """A validated SNOMED CT identifier: string-only, end-to-end (FR-06).

    Construction validates both format and check digit, so any ``SCTID`` in
    memory is known-valid - callers never need to re-check it.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(
                f"SCTID value must be str, got {type(self.value).__name__}: "
                "a numeric type has already lost precision or leading zeros "
                "before it reached this constructor"
            )
        if not has_valid_format(self.value):
            raise InvalidSCTIDError(f"{self.value!r} does not match ^\\d{{6,18}}$")
        if not has_valid_check_digit(self.value):
            raise InvalidSCTIDError(f"{self.value!r} fails Verhoeff check-digit validation")

    def __str__(self) -> str:
        return self.value
