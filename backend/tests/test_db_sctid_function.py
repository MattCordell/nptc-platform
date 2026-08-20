"""Parity test for `nptc_sctid_is_valid` (issue #48, ADR-0023).

This is the whole basis of ADR-0023's argument for accepting a database
function at all: it must be exhaustive over a real corpus, not illustrative,
or a future edit to either side (the SQL function, or `nptc_shared.sctid`)
could let them silently disagree with nothing to catch it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from nptc_shared.sctid import has_valid_check_digit, has_valid_format


#: A corpus built to exercise every boundary this function has to get
#: right: PRD sample SCTIDs (391483001, 71388002, 123037004), the 6-digit
#: lower bound and 16/17/18-digit upper boundary (FR-07's own boundary
#: case), a single-digit perturbation of each known-valid code (proving the
#: checksum actually depends on every digit, not just length), and format
#: junk (too short, too long, non-digit, empty).
def _perturb_last_digit(code: str) -> str:
    last = int(code[-1])
    return code[:-1] + str((last + 1) % 10)


_KNOWN_VALID = ["391483001", "71388002", "123037004", "111111111111111118"]

_CANDIDATES = sorted(
    {
        *_KNOWN_VALID,
        *(_perturb_last_digit(code) for code in _KNOWN_VALID),
        "100000",  # 6-digit lower bound, format-valid, checksum unknown
        "1234567890123456",  # 16 digits
        "12345678901234567",  # 17 digits
        "12345678901234567890",  # 21 digits: too long
        "12345",  # 5 digits: too short
        "39148300X",  # non-digit
        "",  # empty
    }
)


@pytest.mark.integration
@pytest.mark.parametrize("candidate", _CANDIDATES)
def test_function_agrees_with_the_python_implementation(db: Connection, candidate: str) -> None:
    expected = has_valid_format(candidate) and has_valid_check_digit(candidate)

    actual = db.execute(
        text("SELECT nptc_sctid_is_valid(:code) AS result"), {"code": candidate}
    ).scalar_one()

    assert actual is expected, f"{candidate!r}: expected {expected}, got {actual}"
